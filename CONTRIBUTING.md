# Contributing

Thanks for looking. `plate` is a small, opinionated tool; contributions that
keep it small and opinionated are the easiest to land.

## Before you start

- **Bugs and small fixes**: open a PR straight away, or an issue if you want
  to check the diagnosis first.
- **New flags, columns, or views**: open an issue first. The views follow a
  deliberate discipline (one health glyph per row, colour rationed to health,
  weight encoding attention — see [`DECISIONS.md`](./DECISIONS.md)), and it's
  cheaper to agree the shape before the code exists.
- Issues labelled `good first issue` are scoped and ready to pick up.

## Set-up

```sh
git clone https://github.com/SimAin/gh-plate && cd gh-plate
uv sync                          # creates .venv with dev tools
uv run plate issues --help
```

`uv tool install --editable .` puts a `plate` on your PATH that tracks the
checkout. The tool shells out to `gh`, so `gh auth login` first.

## Checks

CI runs these on Python 3.11, 3.12 and 3.13; run them locally before pushing:

```sh
uv run pytest              # tests — offline, sub-second, no gh calls
uv run ruff check          # lint
uv run ruff format --check # formatting (`uv run ruff format` to fix)
uv run mypy                # strict type-check of src/
```

Tests never touch the network: they monkeypatch the `gh` boundary
(`plate.core.gh.run_command`) or a domain's `fetch_*` function. Keep it that
way — a new test that needs a real token will be asked to change. An autouse
fixture in `tests/conftest.py` enforces it: it refuses the `gh` boundary's
`subprocess.run`, so a test that reaches it unpatched fails with "tests must
not shell out".

## Conventions

- **Zero runtime dependencies.** Standard library only; `gh` does the HTTP.
- **Commit messages are [Conventional Commits](https://www.conventionalcommits.org/)**
  (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `ci:`, `chore:`). Releases
  and the changelog are generated from them by release-please, so the type
  and scope matter — `feat` bumps the minor version, `fix` the patch.
- **One concern per PR**, small enough to read whole. Link the issue
  (`Closes #N`). The PR template asks what changed, what deliberately
  didn't, and how you verified it — fill it in honestly; "not verified" is a
  fine answer.
- **Comments say why, not what**, and stay short. Don't add issue numbers to
  new code (the existing ones are legacy) — put the reasoning in the commit
  message or in `DECISIONS.md`.
- **Public-facing text is British English** (`colour`, `licence`).

## Architecture

The package is split into `plate/core/` (shared, domain-agnostic plumbing) and
one directory per domain — `plate/issues/`, `plate/prs/` and `plate/retro/` —
each with the same four modules: `cli` (flags + dispatch), `github` (fetches),
`model` (pure transforms), `render` (presentation). The boundary rule is
enforced by `tests/test_boundaries.py`: a domain package may import
`plate.core`, but never another domain package, and `plate.core` never imports
a domain package back.

| Module | Responsibility |
| --- | --- |
| `plate/cli.py` | Top-level wiring only: builds the parser, dispatches to a subcommand via a command→runner registry, turns a `PlateError` into a clean exit. Knows nothing about issues, sprints, owners, PRs, or retros — this is the one place the domains may be named. |
| `plate/core/gh.py` | The only impure module: `git`/`gh` shelling (`run_command`), repo + login detection, owner-type resolution. Failures raise `PlateError`. Shared by every domain. Also the shared transient-5xx retry policy (`run_gh_with_retry`) and the stderr progress line (`progress`/`progress_clear`). |
| `plate/core/render.py` | Domain-agnostic presentation primitives: ANSI/width helpers, `format_cell`, `format_age`, `hyperlink`, `divider`, `color_enabled`. |
| `plate/core/text.py` | Data cleaning on the way in from a `gh` payload: `compact_text` (one safe line from untrusted text) and `parse_timestamp`. Shared by every domain's `model`. |
| `plate/core/owner.py` | The owner-wide views' shared plumbing: `resolve_owner` (alias table, then GitHub's owner type) and `listing_truncation_note`. |
| `plate/core/config.py` | The JSON config: special-label styles, the per-repo `repos` → project-board mapping, and the `owners` alias table. |
| `plate/issues/model.py` | Pure domain: raw JSON → `IssueRow` index → sorted forest (`build_index`/`build_forest`); the sprint buckets (`build_sprint_view`); health state, progress. |
| `plate/issues/github.py` | Issue-domain GraphQL fetches (`fetch_assigned_issues`, `fetch_owner_issues`, `fetch_sprint_items`) + pagination + board-field validation, built on `plate.core.gh`. |
| `plate/issues/render.py` | Issue-domain presentation: `terminal_tree`/`markdown_tree`, `sprint_table`/`sprint_markdown`, `owner_tree`/`owner_markdown`, built on `plate.core.render`. |
| `plate/issues/cli.py` | The `issues` subcommand: flags, and `run()`/`_run_yours`/`_run_owner`/`_run_sprint` dispatch (`--sprint` selects the board view, `--owner` the owner-wide view). |
| `plate/prs/model.py` | Pure domain: raw GraphQL PR nodes → `PrRow`s (`normalize_rows`), the yours/to-review/the-rest grouping (`sort_key`), the summary counts (`summary_counts`), and the owner view's per-repo sections (`group_by_repo`). |
| `plate/prs/github.py` | PR-domain GraphQL fetches (`fetch_prs_and_viewer`, `fetch_owner_prs`) + `gh api graphql --paginate` pagination, built on `plate.core.gh`. |
| `plate/prs/render.py` | PR-domain presentation: `terminal_table`/`markdown_table`, `owner_table`/`owner_markdown`, `summary_line`, `symbol_key`/`owner_key`, built on `plate.core.render`. |
| `plate/prs/cli.py` | The `prs` subcommand: flags, and `run()`/`_run_repo`/`_run_owner` dispatch (`--owner` selects the owner-wide view). |
| `plate/retro/model.py` | Pure domain: push events → one compare range per branch (`push_groups`), compare expansion → your commit refs, the per-owner channel sections and window arithmetic, plus the honesty notes. |
| `plate/retro/github.py` | Retro-domain REST fetches (events feed, PR search, compare API), built on `plate.core.gh`. |
| `plate/retro/render.py` | Retro-domain presentation: the per-owner day grid (`panel`) and its markdown table. |
| `plate/retro/cli.py` | The `retro` subcommand: flags and `run()`. |

## Releasing

Maintainers only. Merging to `main` makes release-please open or update a
release PR; merging that PR tags the release and updates `CHANGELOG.md` and
`src/plate/__init__.py`. Nothing is published to a package index.
