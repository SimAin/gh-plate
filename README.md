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
issue-check --sprint            # the repo's current sprint (see below)
issue-check --owner my-org      # every open issue across an owner (see below)
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

## Sprint view (`--sprint`)

Where the default view is *"what's on my plate?"*, `issue-check --sprint` is
*"what's in our current sprint, and who has it?"* — a **team** view of a repo's
GitHub Projects v2 board, scoped to the **current iteration**. It groups every
issue in the sprint into three buckets — **`yours` → `others` → `unassigned`** —
under a title carrying the sprint name, and adds **Assignee** and **Status**
columns:

```
Issue                            Age  Assignee     Status        Labels   Prog  Cmt
── Sprint 7  ·  current sprint ──────────────────────────────────────────────────────
── yours ──────────────────────
  • ⇄ #142  Wire up the exporter   4w  a-user       In review     …        1/7    0
── others ─────────────────────
  ·   #138  Tidy the config load  13d  a-teammate   In progress   …               1
── unassigned ─────────────────
  ·   #131  Flaky integration te…  2w  —            Backlog       …        0/2    0
```

*(Identifiers above are synthetic. Your real board lives only in your local
config — see below; it is never part of this repo.)*

`yours` rows keep the full health glyph + linked-PR marker; `others`/`unassigned`
are dimmed whole. Each bucket is sorted **active-first** by board Status. Done and
released items are included (a finished sprint item shows its status). It is a
single query — the board is filtered to its current iteration server-side.

This is opt-in per repo: a repo must be mapped to a project board in config
(below). See [`SPRINT.md`](./SPRINT.md) for the full design.

### Configuring the board

Add a `repos` block to your config file (same file as the special labels below;
run `issue-check --config-path` for its location), keyed by `OWNER/REPO`:

```json
{
  "repos": {
    "an-org/a-repo": {
      "project": "https://github.com/orgs/an-org/projects/1",
      "statusOrder": ["In progress", "In review", "Blocked", "Backlog"]
    }
  }
}
```

- **`project`** (required) — the board URL (`/orgs/OWNER/...` or `/users/OWNER/...`;
  a trailing `/views/N` is fine) or shorthand `OWNER/projects/N`.
- **`statusOrder`** (optional) — your board's statuses listed front-to-back for the
  active-first sort; values must match the board's status names exactly (including
  any emoji prefix). Any status you don't list sorts last.
- **`sprintField`** / **`statusField`** (optional) — default to GitHub's own
  `Iteration` and `Status` field names; set them only if your board renamed them.

This config — with your real org, repo, and project — stays in your local config
file; it is intentionally **not** committed to the repo (the tool ships agnostic).
`gh` must have the `read:project` scope (`gh auth refresh -s read:project`).

## Owner-wide view (`--owner`)

Where the default view is *"what's on my plate in this repo?"*, `issue-check
--owner` is *"what's open across everything this owner has?"* — every open issue
across all of an organization's or user account's repositories, in one table.
Rows are **grouped by repository**, with the **most recently active repo first**;
within each repo the same tree hierarchy applies. Because a personal project's
issues are often unassigned, this view shows **all** open issues by default and
renders **unassigned** ones full-weight — open work you could pick up — while
someone else's issues are dimmed. It needs no checkout, so it runs from anywhere:

```sh
issue-check --owner my-org         # an organization
issue-check --owner SimAin         # a personal account
issue-check --owner work           # a configured alias (see below)
issue-check --owner my-org --mine  # narrow to issues assigned to you
issue-check --owner my-org --format markdown
```

The account type (org vs. user) is detected automatically. `--mine` narrows to
your own assignments; it is only meaningful with `--owner` (the default view is
already yours-only).

### Owner aliases (configuration)

Add an `owners` block to your config file (same file as above) to give a short
alias to an org or username you type often:

```json
{
  "owners": {
    "personal": "SimAin",
    "work":     "company-org"
  }
}
```

`--owner work` then resolves to `company-org`, and the output shows the mapping
(`work → company-org`) so it's clear what was queried. Lookup is
case-insensitive. Any name **not** in the table falls through as a literal, so
`--owner some-other-org` keeps working with zero config. If an alias happens to
match a real owner's name, the **alias wins** (shadows the literal) — remove the
alias to get the literal back.

### Honesty notes

- **Archived repos are excluded** — an archived repo is done, not live work.
- **GitHub's search API caps any single query at 1000 results.** For a very
  large owner the tool cannot fetch past that ceiling, so it prints a note
  (`showing N of M`) rather than silently presenting a partial table. Use
  `--mine` or `--repo` to narrow.
- **`--limit` is global** across the whole owner (not per repo). Results are
  sorted most-recently-active first, so truncating by `--limit` keeps the issues
  that were touched most recently; a truncation note reports the count.

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
| `model.py` | Pure domain: raw JSON → `IssueRow` index → sorted forest (`build_index`/`build_forest`); the sprint buckets (`build_sprint_view`); health state, progress. |
| `github.py` | The only impure module: `git`/`gh` shelling, repo + login detection, the issue + project-board GraphQL fetches (`fetch_assigned_issues`, `fetch_owner_issues`, `fetch_sprint_items`) + owner-type resolution + pagination. Failures raise `IssueCheckError`. |
| `config.py` | The JSON config: special-label styles, the per-repo `repos` → project-board mapping, and the `owners` alias table. |
| `render.py` | Pure presentation: ANSI/width primitives, `terminal_tree`/`markdown_tree`, `sprint_table`/`sprint_markdown`, and `owner_tree`/`owner_markdown`. |
| `cli.py` | Argument parsing and wiring (`--sprint` selects the board view, `--owner` the owner-wide view); turns `IssueCheckError` into a clean exit. |

## Design & docs

| File | What it is |
| --- | --- |
| [`MVP.md`](./MVP.md) | The default ("yours") slice: scope, data layer, columns, hierarchy, acceptance. |
| [`SPRINT.md`](./SPRINT.md) | The `--sprint` slice: board data layer, buckets, columns, config, acceptance. |
| [`DECISIONS.md`](./DECISIONS.md) | Decision log with rationale. |
