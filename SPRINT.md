# Sprint slice — `--sprint`

Status: **built.** The second view of `issue-check`. Where the default view answers
*"what's on my plate, and what's rotting?"*, `issue-check --sprint` answers *"what's
in our current sprint, and who has it?"* — a team view of one repo's GitHub Projects
v2 board, scoped to the **current iteration**. Verified end-to-end against a real
board (`uv run pytest`, `ruff`, `mypy` all pass).

## The slice

Print **every issue in the current sprint** of the repo's configured project board,
grouped by whose turn it is — **`yours` → `others` → `unassigned`** — under a title
carrying the sprint name. Same table discipline as the default view (colour rationed
to health, weight to attention), with **Assignee** and **Status** columns added.

This is a parallel path beside the "yours" slice (see `MVP.md`); the default view is
unchanged. Selected with `--sprint`; opt-in per repo via config (see below).

> Identifiers in this document are synthetic (`an-org/a-repo`, account `a-user`,
> sprint `Sprint 7`). The tool ships agnostic; a real board mapping lives only in
> the user's local config, never in this repo.

## Why a board, and why server-side

A Projects v2 board is a different data source from the issue search the default view
uses. Two facts (validated against a real board) shape the design:

- **The board's `items()` accepts a server-side `query:` filter** — the same syntax
  as the board search bar. `items(first: 100, query: "iteration:@current")` returns
  **only the current sprint** (a handful of items, one page); `@current` is resolved
  from iteration dates *by GitHub*, so the tool does **no date math** and needs **no
  separate iteration-config query**. (A board can hold thousands of historical items
  across many past iterations — the filter is what keeps this a single cheap query.)
- **A board can span repos.** Items are filtered to the requested repo client-side.
  Board items that are PRs or draft issues are dropped — the view is issue-centric
  (a linked PR still surfaces via the `⇄` marker).
- Including **Done/closed** items is free: board items carry no open/closed filter,
  so a finished sprint item appears with its `✅ Done` / `🚢 Released` status.

## Data layer

`fetch_sprint_items()` (in `github.py`) runs one query against
`organization|user(login:).projectV2(number:).items(first: 100, query: $q)` with
`q = "<sprint-field>:@current"`. `content` carries the full Issue (the same fields
the default view fetches — `updatedAt`, `labels`, `comments`, `subIssuesSummary`,
`parent`, `closedByPullRequestsReferences`), and `fieldValueByName` reads the board's
**Status** (single-select) and **Iteration** (title) values. Pagination on
`pageInfo.hasNextPage` is kept for safety though a sprint is well under one page.

## Row & view model

```python
@dataclass(frozen=True)
class SprintRow:
    number; url; title; labels; comments_count
    age_days; is_stale; sub_total; sub_completed
    assignees: list[str]; status: str | None
    is_mine: bool; is_unassigned: bool
    pr_state; pr_number

@dataclass
class SprintView:
    title: str | None              # the sprint name, e.g. "Sprint 7"
    yours / others / unassigned: list[SprintRow]
```

`build_sprint_view()` (pure, in `model.py`) drops non-issue and wrong-repo items,
reads the sprint title from the first surviving item, splits rows into the three
buckets by assignee (`is_mine = login in assignees`), and sorts each bucket
**active-first by Status** via `status_rank` (config `statusOrder`; unlisted statuses
sort last, ties by issue number descending).

## Columns

```
<glyph + pr + #num + Title> · Age · Assignee · Status · Labels · Prog · Cmt
```

- **`yours` rows** — full weight: health glyph (`✓` active / `•` stale) + linked-PR
  marker, OSC-8 link on `#num`, rose Age when stale, special labels promoted.
- **`others` / `unassigned` rows** — the whole row dimmed with a neutral `·` (the
  weight-is-attention axis), but with full data shown (they're real sprint work by
  other people, not the default view's structural context ancestors).
- **Assignee** — first assignee login (`—` when unassigned). Cut from the default
  view as an all-"me" column; load-bearing here.
- **Status** — the board column. Emoji prefix stripped for the terminal (the width
  math counts code points, not display columns); kept in markdown.
- Age / Labels / Prog / Cmt as in the default view. Labels is narrowed to fit the two
  new columns (first-pass; may be tuned).

`--format markdown` renders the three buckets as `###` sections of bullets, each
carrying the same metadata (assignee, status *with* emoji, PR, age, prog, labels).

## Configuration

Global config (`config.py`) gains a `repos` block keyed by `OWNER/REPO`:

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

`project` accepts a board URL (`/orgs/…` or `/users/…`, trailing `/views/N` ignored)
or shorthand `OWNER/projects/N`. `sprintField` / `statusField` default to GitHub's
own `Iteration` / `Status` and are overridable. `statusOrder` drives the active-first
sort (values must match your board's status names exactly, including any emoji);
statuses you don't list sort last. The board filter token derives from the sprint
field name lowercased (`Iteration` → `iteration:@current`). Field names are
interpolated into the GraphQL query via `json.dumps`, so a quote in a name can't
break it.

## Empty / edge output

- No repo entry in config → `No sprint board configured for OWNER/REPO.` (+ how to
  add one), exit 1.
- No active iteration (the `@current` filter returns nothing) → `No active sprint for
  OWNER/REPO.`
- An active sprint with no issues for this repo → `No issues in the current sprint
  (NAME) for OWNER/REPO.` (the title is read before the repo filter, so the two empty
  cases are distinguishable).
- Project unreachable / wrong scope → a `gh`/GraphQL error surfaced via
  `IssueCheckError`.

## Acceptance — validated against a real board

- One query returns the current sprint — server-side `iteration:@current`, not a scan
  of the board's full (thousands-deep) item history;
- items grouped `yours` → `others` → `unassigned`, each sorted active-first by Status;
- Assignee + Status columns populated; status emoji stripped in terminal, kept in
  markdown; the sprint name titles the table;
- a multi-repo board is correctly filtered to the requested repo; PR/draft board
  items dropped;
- `--format markdown` and `--color never` clean; the default (yours) view unchanged.

## Out of scope (first pass; refine after it's up)

Labels-column width tuning · Status-driven (vs age) health glyph · a dim parent tag
on rows · selecting a sprint other than `@current` · showing board PR items · a
sprint-specific `--show-key`.
