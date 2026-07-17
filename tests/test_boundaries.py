"""Enforces the core/domain boundary rule from issue #50 (the founding
constraint of the gh-pr-status absorption epic, #50): domain packages
(``plate.issues`` today, a future ``plate.prs``) may import ``plate.core``,
but must never import each other, and ``plate.core`` must never import a
domain package back.

AST-based and import-free by design: this module parses source text with
:mod:`ast` and never imports ``plate`` itself, so a violation can't hide
behind an import-time side effect (e.g. a lazy/deferred import inside a
function body would still be caught, since it's still a top-level ``import``
or ``from`` statement in the parsed tree).

Domain directories are discovered by scanning ``src/plate/`` for package dirs
other than ``core`` — so a future ``src/plate/prs/`` is picked up
automatically, with no changes needed here.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC_PLATE = Path(__file__).resolve().parent.parent / "src" / "plate"


def _domain_dirs() -> list[str]:
    """Every package directory directly under ``src/plate`` except ``core``."""
    return sorted(
        p.name
        for p in SRC_PLATE.iterdir()
        if p.is_dir() and p.name != "core" and (p / "__init__.py").exists()
    )


def _py_files(pkg_dir: Path) -> list[Path]:
    return sorted(pkg_dir.rglob("*.py"))


def _package_parts(py_file: Path) -> list[str]:
    """The dotted package (not module) that ``py_file`` lives in, as parts.

    E.g. ``src/plate/issues/cli.py`` -> ``["plate", "issues"]`` (its
    containing package), and ``src/plate/issues/__init__.py`` -> the same —
    an ``__init__.py`` *is* that package, and relative-import levels resolve
    against it identically to any other module in the same directory.
    """
    rel = py_file.relative_to(SRC_PLATE.parent)  # relative to "src/"
    parts = list(rel.parts[:-1])  # drop the filename, keep directory chain
    return parts


def _referenced_domain(target: list[str], domain_dirs: list[str]) -> str | None:
    """The plate subpackage (domain name, or ``"core"``) ``target`` points into.

    ``target`` is a dotted import path split into parts, e.g.
    ``["plate", "issues", "github"]``. Returns ``None`` for anything that
    isn't a reference into a ``plate.<subpackage>`` (stdlib, third-party, or
    bare ``plate``/``plate.__version__``-style imports of the top package).
    """
    if len(target) < 2 or target[0] != "plate":
        return None
    second = target[1]
    if second == "core" or second in domain_dirs:
        return second
    return None


def _imports_of(py_file: Path, domain_dirs: list[str]) -> list[tuple[str, ast.AST]]:
    """``[(referenced_subpackage, node), ...]`` for every import in ``py_file``
    that points into ``plate.core`` or a ``plate.<domain>`` package.
    """
    tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
    package_parts = _package_parts(py_file)
    found: list[tuple[str, ast.AST]] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                target = alias.name.split(".")
                ref = _referenced_domain(target, domain_dirs)
                if ref is not None:
                    found.append((ref, node))
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                target = (node.module or "").split(".")
            else:
                # Relative import: strip (level - 1) trailing components from
                # the current package to find the base, then append the
                # imported module path (if any) — standard relative-import
                # resolution, computed without ever importing the package.
                trim = node.level - 1
                base = (
                    package_parts[: len(package_parts) - trim]
                    if trim
                    else package_parts
                )
                target = [*base, *(node.module.split(".") if node.module else [])]
            ref = _referenced_domain(target, domain_dirs)
            if ref is not None:
                found.append((ref, node))

    return found


def _fail_message(py_file: Path, node: ast.AST, referenced: str, rule: str) -> str:
    lineno = getattr(node, "lineno", "?")
    source = ast.unparse(node) if hasattr(ast, "unparse") else "<import>"
    rel = py_file.relative_to(SRC_PLATE.parent.parent)
    return (
        f"{rule}\n"
        f"  file:   {rel}:{lineno}\n"
        f"  import: {source}\n"
        f"  refers to: plate.{referenced}"
    )


def test_domains_never_import_each_other() -> None:
    domain_dirs = _domain_dirs()
    assert domain_dirs, "expected at least one domain package under src/plate/"

    violations: list[str] = []
    for domain in domain_dirs:
        for py_file in _py_files(SRC_PLATE / domain):
            for referenced, node in _imports_of(py_file, domain_dirs):
                if referenced != "core" and referenced != domain:
                    violations.append(
                        _fail_message(
                            py_file,
                            node,
                            referenced,
                            f'domain "{domain}" must not import domain '
                            f'"{referenced}" (only plate.core is shared).',
                        )
                    )

    assert not violations, "\n\n".join(violations)


def test_core_never_imports_a_domain() -> None:
    domain_dirs = _domain_dirs()

    violations: list[str] = []
    for py_file in _py_files(SRC_PLATE / "core"):
        for referenced, node in _imports_of(py_file, domain_dirs):
            if referenced in domain_dirs:
                violations.append(
                    _fail_message(
                        py_file,
                        node,
                        referenced,
                        f'plate.core must not import domain "{referenced}" '
                        "(core is shared beneath every domain, never the "
                        "reverse).",
                    )
                )

    assert not violations, "\n\n".join(violations)
