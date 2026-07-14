# gh-issue-check

`issue-check` prints a compact terminal status table for the open GitHub issues
**assigned to you** in a repository — "what's on my plate, and what's rotting?"

It follows a deliberate discipline: group by whose turn it is, one health glyph
per row, colour rationed to health, weight encoding attention. This is the
**"yours" MVP slice** — see
[`MVP.md`](./MVP.md) for exactly what is and isn't built yet.

It shells out to `git` and `gh` (GraphQL), so the GitHub CLI must be installed
and authenticated (`gh auth login`).

## Install

This is a [uv](https://docs.astral.sh/uv/) project. To install the `issue-check`
executable on your PATH:

```sh
uv tool install --editable .
```

For local development (creates `.venv`, installs dev tools):

```sh
uv sync
uv run issue-check --help
```

## Usage

From inside any cloned GitHub repository:

```sh
issue-check
```

Or target a repo explicitly from anywhere:

```sh
issue-check --repo OWNER/REPO
issue-check --format markdown
issue-check --color never
issue-check --stale-days 30
issue-check --show-key
```

If run outside a git repository without `--repo`, it prints an actionable error.

### How to read the table

The open issues assigned to you, shown as a **tree**: each issue sits indented
beneath its parent. An ancestor that *isn't* assigned to you is still shown,
**dimmed** with a `·`, so a child never floats parentless. Groups are ordered
**active-subtree first** — the cluster you're working in now rises as a whole
unit (it floats by its most recently touched issue), while long-untouched
clusters sink intact to the bottom.

In a terminal that supports OSC-8 hyperlinks, each `#number` is clickable and
opens the issue on GitHub (suppressed under `--color never` and when piped).

Colour is reserved for one thing: the health of an issue that's yours.

| Glyph | State | Meaning |
| --- | --- | --- |
| `✓` green | active | updated within `--stale-days` (default 14) |
| `•` gold | stale | not updated past `--stale-days` |
| `·` dim | context | an ancestor not assigned to you, shown for structure |

A **second glyph** beside the health glyph shows whether a pull request links the
issue — the "fix in flight" signal. When several PRs link one issue, the most
significant is shown (`open > draft > merged > closed`):

| Glyph | State | Meaning |
| --- | --- | --- |
| `⇄` | open / draft PR | a fix is in flight (draft is dimmed) |
| `⇄` green | merged | the fix landed but the issue is still open — nudge to close |
| `✗` red | closed | a PR was opened then abandoned — shown for context |

In `--format markdown` the marker carries the PR number and state word
(`⇄ #2457 draft`, `⇄ #1115 merged`).

The other columns carry the detail (for your own issues; blank on context rows):

- **Age** — time since last update (`6h`, `3d`, `4w`, `5mo`, `2y`), rose once stale.
- **Labels** — the raw label set, dimmed, `:emoji:` shortcodes stripped. Whole
  labels are packed into the column with a `+N` count for any that don't fit
  (e.g. `security +1`), so it stays a clean context indicator. `--format
  markdown` shows the full set.
- **Prog** — `completed/total` sub-issues; shown on any parent (including a
  dimmed context epic — the rollup is the useful part).
- **Cmt** — comment count.

`--format markdown` renders the same tree as a nested list.

## Special labels (configuration)

By default the label set is shown agnostically — except `blocked`, which is
called out in red. You can name other labels you care about and give each a
*style* in a JSON config file (`$ISSUE_CHECK_CONFIG`, else
`~/.config/issue-check/config.json` — run `issue-check --config-path` to see the
exact location):

```json
{
  "labels": {
    "blocked":    "alert",
    "needs-info": "warn",
    "status:*":   "info",
    "wontfix":    "hide"
  }
}
```

A recognised label is pulled to the front of the Labels cell and shown bright:
`alert` = red, `warn` = gold, `info` = green; `hide` drops the label entirely.
Matching is case-insensitive and supports `*` globs for prefixed schemes. Your
file merges over the built-in default, so you only list what you want to change.

## Development

```sh
uv run pytest      # tests
uv run ruff check  # lint
uv run mypy        # type-check (src/)
```

The code is split by responsibility so the logic stays testable and the I/O is
isolated:

| Module | Responsibility |
| --- | --- |
| `model.py` | Pure domain: raw JSON → `IssueRow` index → sorted forest (`build_index`/`build_forest`), health state, progress. |
| `github.py` | The only impure module: `git`/`gh` shelling, repo + login detection, the GraphQL fetch + pagination. Failures raise `IssueCheckError`. |
| `render.py` | Pure presentation: ANSI/width primitives, `terminal_tree`, `markdown_tree`. |
| `cli.py` | Argument parsing and wiring; turns `IssueCheckError` into a clean exit. |

## Design & docs

| File | What it is |
| --- | --- |
| [`MVP.md`](./MVP.md) | The built slice: scope, data layer, columns, hierarchy, acceptance. |
| [`DECISIONS.md`](./DECISIONS.md) | Decision log with rationale. |
