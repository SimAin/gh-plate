"""The owner-wide views' shared plumbing: an ``--owner`` argument turned into a
resolved GitHub owner, and the note that admits a listing was clipped.

Both the issues and the PR owner views take an alias or a literal owner, ask
GitHub what kind of account it is, and print the same two truncation notes with
a different noun; only the noun and the fetch differ, so the resolution and the
wording live here.
"""

from __future__ import annotations

from dataclasses import dataclass

from plate.core import config, gh
from plate.core.gh import PlateError


@dataclass(frozen=True)
class ResolvedOwner:
    """An ``--owner`` argument after the alias table and GitHub have had a say.

    ``name`` is the owner to query, ``owner_type`` GitHub's answer
    (organization/user). ``display`` is what the views print — ``alias → owner``
    when an alias fired, the bare argument otherwise — and ``alias_fired`` gates
    the extra header line that shows the mapping.
    """

    name: str
    owner_type: str
    display: str
    alias_fired: bool


def resolve_owner(requested: str, cfg: config.Config) -> ResolvedOwner:
    """Resolve ``requested`` through the alias table, then through GitHub.

    Resolving the owner type doubles as validation: an unknown owner raises.
    """
    resolved = cfg.resolve_owner(requested)
    # Show the alias mapping only when one actually fired (the resolver folds
    # case, so compare after resolution); a literal owner shows just its name.
    alias_fired = resolved != requested

    try:
        owner_type = gh.resolve_owner_type(resolved)
    except PlateError as exc:
        # An unknown alias falls through resolve_owner as a literal, so a typo'd
        # alias surfaces here as an unknown owner. If aliases are configured,
        # list them so the user can spot the one they meant.
        if cfg.owners:
            aliases = ", ".join(
                f"{alias} → {owner}" for alias, owner in cfg.owners.items()
            )
            raise PlateError(f"{exc}\nConfigured aliases: {aliases}") from exc
        raise

    return ResolvedOwner(
        name=resolved,
        owner_type=owner_type,
        display=f"{requested} → {resolved}" if alias_fired else requested,
        alias_fired=alias_fired,
    )


def truncation_note(
    noun: str, display: str, shown: int, total: int, limit: int
) -> str | None:
    """The note for a clipped owner-wide listing, or None when it was complete.

    ``noun`` names what was counted (``"open issues"``, ``"open PRs"``). Two
    causes, two remedies: the user's own ``--limit``, or GitHub's 1000-result
    search ceiling. The leading blank line separates the note from the table
    above it — callers print it to stderr as-is.
    """
    if shown >= total:
        return None
    if shown == limit:
        return (
            f"\nNote: showing {shown} of {total} {noun} for "
            f"{display} (--limit {limit})."
        )
    return (
        "\nNote: GitHub search returns at most 1000 results per query; "
        f"showing {shown} of {total} {noun} for {display}. "
        "Use --mine or --repo to narrow."
    )
