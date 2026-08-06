# plate

`plate issues` prints a compact terminal status table for the open GitHub
issues **assigned to you** in a repository — "what's on my plate, and what's
rotting?" `plate` is the renamed, restructured successor to
`gh-issue-check` (a hard switch — the `issue-check` command is gone; earlier
versions remain installable if you need the old CLI).

It follows a deliberate discipline: group by whose turn it is, one health glyph
per row, colour rationed to health, weight encoding attention. This is the
**"yours" MVP slice** — see
[`MVP.md`](./MVP.md) for exactly what is and isn't built yet.

It shells out to `git` and `gh` (GraphQL), so the GitHub CLI must be installed
and authenticated (`gh auth login`).

## Install

This is a [uv](https://docs.astral.sh/uv/) project. To install the `plate`
executable on your PATH:

```sh
uv tool install --editable .
```

For local development (creates `.venv`, installs dev tools):

```sh
uv sync
uv run plate issues --help
```

## Usage

From inside any cloned GitHub repository:

```sh
plate issues
```

Or target a repo explicitly from anywhere:

```sh
plate issues --repo OWNER/REPO
plate issues --format markdown
plate issues --color never
plate issues --stale-days 30
plate issues --show-key
plate issues --sprint            # the repo's current sprint (see below)
plate issues --owner my-org      # every open issue across an owner (see below)
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

## PR view (`plate prs`)

`plate prs` prints a compact terminal status table for the **open pull
requests** in a repository — "what's waiting on review, and what's mine?" It
is the renamed, restructured successor to `gh-pr-status` (`pr-check`).

```sh
plate prs
```

Or target a repo explicitly from anywhere:

```sh
plate prs --repo OWNER/REPO
plate prs --format markdown
plate prs --color never
plate prs --stale-days 7
plate prs --show-key
plate prs --timeline          # per-PR activity strip (see below)
plate prs --owner my-org      # every open PR across an owner (see below)
```

If run outside a git repository without `--repo`, it prints an actionable error.

### How to read the table

A one-line summary sits above the table (e.g. `12 open · 3 to review ·
1 with conflicts`); zero counts are suppressed. Rows are grouped by whose
turn it is — **yours**, **to review**, then **the rest** — under a labelled
divider that carries the group's count. Colour is reserved for one thing
only: a PR's health. The leading glyph is the headline state, resolved in
priority order:

| Glyph | State | Meaning |
| --- | --- | --- |
| `⚠` | conflict | has merge conflicts |
| `•` | waiting | not ready yet — needs review, CI, or fixes |
| `✓` | ready | approved with CI green |
| `?` | unconfirmed | approved with CI green, but GitHub hasn't confirmed it's conflict-free yet |
| `◦` | draft | still a draft |

Failing CI and requested changes are not headline states — they already show
in the CI (`✗`) and Review (`changes req`) columns, so the leading glyph stays
reserved for what those columns don't cover.

The remaining columns carry the detail:

- **Age** — time since the PR was opened (e.g. `3d`, `4w`) — total time in
  flight. Context only, always dimmed.
- **Last** — time since the last *human* move (a commit, review, or comment;
  bot activity never counts). Its weight answers "whose move is next?": full
  weight means the other side moved last and the days are **your** lag (on
  your PR, respond; on a to-review PR, review); dimmed means you moved last —
  nothing to chase. Rose once nobody has touched the PR in `--stale-days`
  (14 by default). The summary line counts these as `N your move`.
- **Review** — `approved`, `changes req`, `pending`, or `you ✓` when you have
  reviewed someone else's PR.
- **CI** — the status-check rollup as ✓ / ✗ / •.
- **Assignee** — other engineers' logins at full weight (who you might chase);
  `me`, bot authors (`dependabot`, `renovate`, `github-actions`, …), and
  unassigned (blank) dimmed back; an open `Release PR` flagged with a soft
  non-health tint.

Settled PRs in "the rest" are dimmed so your attention lands on what's live.

### The activity strip (`plate prs --timeline`)

`--timeline` adds a sub-line under each row: the last 28 days of human
activity, one cell per day, rightmost = today.

```
•  #81   feat: lag-days columns …   me    1w    2d  changes req  ✓    5
   ↳     ····················◆◆·●·▲··
```

`◆` commit, `●` comment, `▲` review, `·` quiet day. Your own events are
dimmed; other people's take gold — or the review verdict's colour (rose =
changes requested, green = approved). Bot activity never appears, so a
release-please or renovate PR shows an all-quiet strip. The row already says
who moved last and how long ago (the Last column); the strip adds the shape —
the rhythm and the silence. It needs event history, so the flag opts into a
heavier query; it applies to the repo view's terminal format only (ignored
with `--format markdown`, rejected with `--owner`).

In terminals that support OSC 8 hyperlinks (iTerm2, Kitty, WezTerm, VS Code,
and most modern emulators), the PR number is clickable and opens the PR on
GitHub. Piped or redirected output stays plain.

### Owner-wide PRs (`plate prs --owner`)

Like the issues owner view below, `plate prs --owner` answers *"what PRs are
open across everything this owner has?"* — every open pull request across all
of an organization's or user account's repositories, **grouped by repository**
with the **most recently active repo first**. Within each repo, rows keep the
same columns and health glyphs as the repo view; the per-repo divider replaces
the yours/to-review/the-rest grouping, and a PR that is neither yours nor
waiting on your review is dimmed. It needs no checkout, so it runs from
anywhere:

```sh
plate prs --owner my-org         # an organization
plate prs --owner SimAin         # a personal account
plate prs --owner work           # a configured alias (see below)
plate prs --owner my-org --mine  # narrow to PRs you authored
plate prs --owner my-org --format markdown
```

The account type (org vs. user) is detected automatically, and the same
[owner aliases](#owner-aliases-configuration) apply (`work → company-org` is
echoed when an alias fires). `--mine` narrows to PRs **you authored** — note
this is narrower than the repo view's "yours" group (author *or* assignee);
see `DECISIONS.md` D9 for why. It is only meaningful with `--owner`.

The same honesty notes as the issues owner view apply: archived repos are
excluded, results are sorted most-recently-active first, and a truncation note
reports `showing N of M` whenever `--limit` or GitHub's 1000-results-per-search
cap clipped the table.

## Retro view (`plate retro`)

Where every other view looks at the work, `plate retro` looks at **you**: a
day-by-day retrospective of your own GitHub activity, private repositories
included, **split into one panel per repository owner** so work-org and
personal activity read separately. The glance it exists for: *"I haven't
reviewed anything in two days… ah, because I was heads-down committing —
let's review something today."*

```sh
plate retro              # last 14 days
plate retro --days 21    # 7-30
```

```
── acme-corp · last 14 days ────────────────────────────────────────
               S  S  M  T  W  T  F  S  S  M  T  W  T  F    Σ
   reviews     ·  ·  2  1  ·  3  1  ·  ·  2  1  1  ·  ·   11   last 2d ago
   commits     ·  ·  1  ·  2  ·  ·  ·  ·  ·  4  6  5  3   21   today
   opened      ·  ·  ·  1  ·  ·  ·  ·  ·  ·  ·  1  ·  ·    2   last 2d ago
── SimAin · last 14 days ───────────────────────────────────────────
               S  S  M  T  W  T  F  S  S  M  T  W  T  F    Σ
   reviews     ·  ·  ·  ·  ·  ·  ·  ·  ·  ·  ·  ·  ·  ·    0   none in 14d
   commits     ·  ·  ·  ·  3  ·  ·  ·  ·  ·  ·  2  ·  1    6   today
   opened      ·  ·  ·  ·  1  ·  ·  ·  ·  ·  ·  ·  ·  ·    1   last 9d ago
```

Owners appear most active first. Columns are days (weekday ruler, weekends
dimmed, today bold, rightmost = today), digits are counts, `Σ` is the window
total, and each row ends with the literal answer to "when did I last…?". The
one tint is the gold nudge on a reviews row that has been quiet for two days
or more.

Each channel reads the best source that can see private activity — reviews
from your own events feed, PRs opened from search, and **commits from your
push events expanded through the compare API**, so branch work counts with
its real magnitude on the day it happened, not the day it merged. It needs
no checkout and runs from anywhere `gh` is authenticated.

Honesty notes when a source hits its limits: GitHub keeps only your 300 most
recent events (review/commit counts for early days may be undercounted), a
push whose history was rewritten can't be expanded (counted as one commit on
its push day), and search caps at 1000 results. Each case prints a note
rather than passing gaps off as rest. `--format markdown` prints a compact
per-owner `channel | total | last` table instead of the grid.

## Sprint view (`--sprint`)

Where the default view is *"what's on my plate?"*, `plate issues --sprint` is
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
run `plate issues --config-path` for its location), keyed by `OWNER/REPO`:

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

Where the default view is *"what's on my plate in this repo?"*, `plate issues
--owner` is *"what's open across everything this owner has?"* — every open issue
across all of an organization's or user account's repositories, in one table.
Rows are **grouped by repository**, with the **most recently active repo first**;
within each repo the same tree hierarchy applies. Because a personal project's
issues are often unassigned, this view shows **all** open issues by default and
renders **unassigned** ones full-weight — open work you could pick up — while
someone else's issues are dimmed. It needs no checkout, so it runs from anywhere:

```sh
plate issues --owner my-org         # an organization
plate issues --owner SimAin         # a personal account
plate issues --owner work           # a configured alias (see below)
plate issues --owner my-org --mine  # narrow to issues assigned to you
plate issues --owner my-org --format markdown
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
*style* in a JSON config file (`$PLATE_CONFIG`, else `~/.config/plate/config.json`
— run `plate issues --config-path` to see the exact location).

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

The package is split into `plate/core/` (shared, domain-agnostic plumbing) and
one directory per domain — `plate/issues/` and `plate/prs/` sit side by side.
The boundary rule is enforced by `tests/test_boundaries.py`: a domain package
may import `plate.core`, but never another domain package, and `plate.core`
never imports a domain package back (issue #50).

| Module | Responsibility |
| --- | --- |
| `plate/cli.py` | Top-level wiring only: builds the parser, dispatches to a subcommand via a command→runner registry, turns a `PlateError` into a clean exit. Knows nothing about issues, sprints, owners, or PRs — this is the one place both domains may be named. |
| `plate/core/gh.py` | The only impure module: `git`/`gh` shelling (`run_command`), repo + login detection, owner-type resolution. Failures raise `PlateError`. Shared by every domain. |
| `plate/core/render.py` | Domain-agnostic presentation primitives: ANSI/width helpers, `format_cell`, `format_age`, `hyperlink`, `divider`. |
| `plate/core/config.py` | The JSON config: special-label styles, the per-repo `repos` → project-board mapping, and the `owners` alias table. |
| `plate/issues/model.py` | Pure domain: raw JSON → `IssueRow` index → sorted forest (`build_index`/`build_forest`); the sprint buckets (`build_sprint_view`); health state, progress. |
| `plate/issues/github.py` | Issue-domain GraphQL fetches (`fetch_assigned_issues`, `fetch_owner_issues`, `fetch_sprint_items`) + pagination + board-field validation, built on `plate.core.gh`. |
| `plate/issues/render.py` | Issue-domain presentation: `terminal_tree`/`markdown_tree`, `sprint_table`/`sprint_markdown`, `owner_tree`/`owner_markdown`, built on `plate.core.render`. |
| `plate/issues/cli.py` | The `issues` subcommand: flags, and `run()`/`_run_yours`/`_run_owner`/`_run_sprint` dispatch (`--sprint` selects the board view, `--owner` the owner-wide view). |
| `plate/prs/model.py` | Pure domain: raw GraphQL PR nodes → `PrRow`s (`normalize_rows`), the yours/to-review/the-rest grouping (`sort_key`), the summary counts (`summary_counts`), and the owner view's per-repo sections (`group_by_repo`). |
| `plate/prs/github.py` | PR-domain GraphQL fetches (`fetch_prs_and_viewer`, `fetch_owner_prs`) + `gh api graphql --paginate` pagination, built on `plate.core.gh`. |
| `plate/prs/render.py` | PR-domain presentation: `terminal_table`/`markdown_table`, `owner_table`/`owner_markdown`, `summary_line`, `symbol_key`/`owner_key`, built on `plate.core.render`. |
| `plate/prs/cli.py` | The `prs` subcommand: flags, and `run()`/`_run_repo`/`_run_owner` dispatch (`--owner` selects the owner-wide view). |

## Design & docs

| File | What it is |
| --- | --- |
| [`MVP.md`](./MVP.md) | The default ("yours") slice: scope, data layer, columns, hierarchy, acceptance. |
| [`SPRINT.md`](./SPRINT.md) | The `--sprint` slice: board data layer, buckets, columns, config, acceptance. |
| [`DECISIONS.md`](./DECISIONS.md) | Decision log with rationale. |
