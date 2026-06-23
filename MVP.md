# MVP slice — "yours" only

Status: **built.** The first shippable rung of `issue-check` (see `DECISIONS.md`
for the decisions behind the full design this is carved from). Verified end-to-end
against a real repository; `uv run pytest`, `ruff`, and `mypy` all pass.

## The slice

Print **open issues assigned to me** in the current repo, as a terminal table
(plus `--format markdown`). One group, no triage classification, no "the rest".

This is the full design's build-order rung 1+2 narrowed to a single group. It is
useful on its own — "what's on my plate, and what's rotting" — and it is the
cheapest path to proving the GraphQL data layer.

## Why this slice is the right first cut

- **The 500+ issue count never bites.** We filter `assignee:<login>` server-side,
  so we fetch *my* issues (dozens, one page of 100) — not the whole repo. The
  pagination problem is deferred along with the groups that need it.
- **It dodges the two unresolved design problems.** The group/glyph collapse and
  the "zero labels = neglect" fragility both live in the *needs-triage* and
  *the-rest* groups. With one group there is nothing to collapse and no triage
  predicate to get wrong.
- **It still proves the load-bearing risk.** The GraphQL sub-issue fetch
  (`subIssuesSummary`, `parent`) is the one thing that could sink the whole
  design. This slice exercises it for real.

## Validated against a real repo

Findings from running the real query against a live repository. Identifiers
below are synthetic (`an-org/a-repo`, account `a-user`); the issue numbers are
kept only to make the worked examples consistent.

- `Issue.parent`, `subIssues`, `subIssuesSummary`, `reactionGroups` all resolve
  via `gh api graphql` with **no preview feature header** — sub-issues are GA.
- **~500 open issues in the repo, a few dozen assigned to me, one page**
  (`hasNextPage: false`). The server-side filter makes repo size irrelevant.
- **Most assigned issues have a parent; max ancestor depth is 2** (issue →
  parent → grandparent), and many roll up to a single shared root epic (`#2312`).
- Decision consequence: this is a **tree** view (see *Hierarchy* below). Each
  owned issue is a node beneath its parent; ancestors not assigned to you are
  shown dimmed for context. (A flat breadcrumb design was prototyped first; its
  leading `↳ #parent` read as if the row above were the parent and put the
  child id before the parent id — backwards. Indentation fixes both, since the
  parent genuinely *is* the line above.)
- Label names carry literal emoji shortcodes (`:cockroach: bug`, `:scroll:
  epic`) → must be stripped (see *Columns*).
- Reactions are all-zero on assigned work → the `👍` column is dropped from this
  slice (it's a demand/triage signal, not a "what's on my plate" signal).

## Data layer

One query, resolve my login first (we need it anyway, and it sidesteps `@me`
ambiguity in raw GraphQL search):

1. `fetch_current_login()` — resolve the authenticated login via `gh api user`.
2. Search, filtered server-side. `--paginate` is included for correctness but a
   single page of 100 covers any realistic assigned set.

```graphql
fragment NodeFields on Issue {
  number title url updatedAt
  subIssuesSummary { total completed }
}
query($q: String!, $endCursor: String) {
  search(query: $q, type: ISSUE, first: 100, after: $endCursor) {
    issueCount
    pageInfo { hasNextPage endCursor }
    nodes {
      ... on Issue {
        ...NodeFields
        labels(first: 10) { nodes { name } }
        comments { totalCount }
        parent { ...NodeFields parent { ...NodeFields parent { ...NodeFields } } }
      }
    }
  }
}
```

with `q = "repo:OWNER/REPO is:issue is:open assignee:LOGIN"`.

- `assignees` is **not** fetched — every owned row is me by definition.
- `reactionGroups` is **not** fetched — the `👍` column is dropped for this slice.
- The **`parent` chain is fetched three levels deep** so the tree can place each
  owned issue under its (possibly un-owned) ancestors — including each ancestor's
  `title` and `subIssuesSummary` — from this *single* query. Trees deeper than
  three levels lose their topmost ancestors; fine for shallow epic→task work.

## Row model

```python
@dataclass(frozen=True)
class IssueRow:
    number: int
    url: str
    title: str
    labels: list[str]        # emoji shortcodes stripped; [] for context nodes
    comments_count: int      # comments.totalCount; 0 for context nodes
    age_days: int | None
    is_stale: bool
    parent_number: int | None
    sub_total: int           # 0 when no children
    sub_completed: int
    mine: bool               # assigned to me, vs. an un-owned context ancestor

@dataclass
class TreeNode:
    row: IssueRow
    depth: int
    children: list["TreeNode"]
```

`build_index()` produces one `IssueRow` per owned issue (`mine=True`), then walks
each issue's parent chain to materialise any ancestor not already present as a
context node (`mine=False`). `build_forest()` links them into a sorted forest.

## Health glyph — only two branches reachable here

In the "yours" group an issue always has an assignee (me) and is owned, so the
`untriaged` and `backlog` states **cannot** occur. The full priority resolver is
built, but only two arms fire:

| Glyph | State | Rule |
| --- | --- | --- |
| `✓` green | active | updated within `--stale-days` |
| `•` gold | stale | not updated past `--stale-days` |

`--stale-days` stays at the inherited default of **14**. On a long-lived backlog
this flags nearly everything gold — that is accepted as the honest signal
("all your assigned work is old"), not a bug to tune away.

## Linked-PR marker — "fix in flight"

A **second glyph**, flush against the health glyph, shows whether a PR links the
issue (the full design's deferred linked-PR signal, pulled forward — it is the
highest day-to-day value-add). Data comes from `closedByPullRequestsReferences(first: 10,
includeClosedPrs: true)` on owned issues only; context ancestors carry no marker.

An issue can link several PRs (one big PR closing many sub-issues, plus abandoned
earlier attempts), so a single marker is shown — the **most significant** one, by
priority `open > draft > merged > closed`:

| Glyph | State | Meaning |
| --- | --- | --- |
| `⇄` | open PR | a fix is in flight (ready for review) |
| `⇄` dim | draft PR | a fix is in progress (WIP) |
| `⇄` green | merged | the fix landed but the issue is *still open* — a nudge to close it |
| `✗` red | closed | a PR was opened and abandoned without merging — context only |

The "in flight" states share the `⇄` glyph (draft = dimmed, mirroring how GitHub
greys drafts). A merged-but-still-open PR is the **same `⇄` tinted green** — a
positive "it landed" signal — and an abandoned PR is a **red `✗`**. This is a
deliberate, intentional use of colour *beyond* health: the linked-PR state earns
a glance of colour. A space separates the health and PR glyphs (`✓ ⇄ #2416`) for
legibility. In `--format markdown`, where colour is unavailable, the marker adds
the PR number and a state word (`⇄ #2457 draft`, `⇄ #1115 merged`).

Validated on the real repo: the active `#2312` cluster shows a draft `⇄ #2457`
(one PR closes the whole epic), `#1113` shows a green `⇄` (merged #1115, issue
still open), and `#2374`/`#2377` show a red `✗` (their only link is the abandoned
#2381).

## Columns

```
<indented glyph + #num + Title> · Age · Labels · Prog · Cmt
```

The leading tree cell carries indentation (depth), the health glyph, the issue
number, and the title together — so the bullet itself steps right under its
parent. The **Assignee** column from the full spec is dropped (an all-"me"
column is noise; it returns with other groups), as is **👍** (see above).

- **Tree cell** — `  ·depth` indent + glyph + `#num` + title, truncated to fit.
  Owned rows use the health glyph; context ancestors use a dim `·`. Each `#num`
  is an OSC-8 hyperlink to the issue (gated on colour, so `--color never` and
  piped output stay plain); the width math strips the escape via `_OSC8_RE` so a
  linked number still measures as its visible text.
- **Age** — `format_age`, rose once `is_stale`. Owned rows only (blank for
  context ancestors — their children carry the dates).
- **Labels** — raw set, dimmed, `:emoji:` shortcodes stripped
  (`re.sub(r":[a-z0-9_+]+:\s*", "", name)`). Owned rows only. No meaning (D2).
  Packed *whole* into the 18-col cell (`format_labels`): labels are joined with
  ` · ` until the next won't fit, then the remainder shows as `+N` (e.g.
  `security +1`, `epic +2`) — an at-a-glance indicator, never a mid-word mash.
  Only a lone label longer than the column is ellipsis-truncated. Markdown shows
  the full set joined with `, ` (no width limit). **Special labels** (D5) are
  pulled to the front and shown bright in their style colour (`alert`=red,
  `warn`=gold, `info`=green); `hide` drops them. When a promoted label fills the
  cell, the remaining ordinary labels are dropped — the special label is the
  signal you asked to surface. Markdown bolds special labels (`**blocked**`).
- **Prog** — `completed/total` when the node has children, else blank. Shown for
  owned *and* context parents — the rollup is the useful bit on a context epic.
- **Cmt** — `comments.totalCount`. Owned rows only.

## Hierarchy — tree layout (indentation, not breadcrumb)

Each owned issue is a node placed **beneath its parent**, indented by depth.
Indentation is the honest encoding of parenthood — the parent really is the line
above — so there is no arrow glyph and no parent-id competing with the row's own
id. This is the full design's `--tree` view, promoted to the default for this
single-group slice (with only one group, there is no triage spine for the tree
to fight; the only thing traded away is the global staleness sort — see *Sort*).

**Context ancestors.** An ancestor not assigned to you is still shown, as a
**dimmed** node (`·` marker, no health glyph, no age), so a child never floats
parentless. Dim = not-yours reuses the weight-equals-attention axis. A parent
that *is* also yours simply appears at full weight as its own node — no special
marker needed, because the tree already shows it once, in place.

Worked example: the `#2312` program appears as a dim root carrying its
`1/10` rollup, with owned epics `#2353` (1/7) and `#2366` (0/10) nested beneath
it, each over their owned task children; `#2413` (not yours) sits dimmed among
them over its own children.

## Sort

**Active-subtree first.** Siblings (and roots) are ordered by the *minimum age
anywhere in their subtree* (the most recently touched issue beneath them),
ascending, ties broken by number. The cluster you are working in *now* floats to
the top as a whole unit — e.g. the active program `#2312` rises because its
tasks were touched within the last few weeks — while long-untouched clusters
sink intact to the bottom. A subtree whose every node lacks a timestamp sorts
last.

This is the deliberate trade for the tree layout: it is **not** a strict global
freshest-first order (a 6-week task can sit above a 2-week one when they're in
different subtrees), but it surfaces "what I'm working on now" at the group level
while keeping each epic's children together. The honest cost is that genuinely
neglected standalone issues sink to the bottom — the Age column (rose once stale)
is what flags them there.

## Architecture (as built)

Rather than one monolithic `cli.py`, the logic is split by responsibility so the
domain is unit-testable and the I/O is isolated:

| Module | Responsibility |
| --- | --- |
| `model.py` | Pure domain: raw JSON → `IssueRow` index (`build_index`) → sorted forest (`build_forest`), plus `issue_state`, `progress_text`, `dominant_pr`, emoji-stripping. No I/O. |
| `github.py` | An impure module: `git`/`gh` shelling, `current_repo`/`current_login`, `fetch_assigned_issues` (GraphQL + pagination). Failures raise `IssueCheckError`. |
| `config.py` | The other I/O: reads the JSON special-label config (`$ISSUE_CHECK_CONFIG` / XDG), validates it, resolves a label → style (case-insensitive + `*` globs). |
| `render.py` | Pure presentation: ANSI/width primitives, `terminal_tree`, `markdown_tree` (nested list), `symbol_key`, OSC-8 links. Takes a label-style resolver. |
| `cli.py` | argparse + wiring; loads config; catches `IssueCheckError` → stderr + exit 1. |

The core ideas (soft-tint colour constants, weight-equals-attention, `format_age`
+ staleness, ANSI-aware width handling, the `--repo`/`--format`/`--color`/
`--stale-days` flags) are organised into the layers above. Project tooling is
uv-native (`pyproject` + hatchling backend, dev deps in `[dependency-groups]`);
tests under `tests/`.

## Empty / edge output

- No assigned issues → `No open issues assigned to you in OWNER/REPO.`
- Login undeterminable → fail clearly (this slice is defined *by* the login, so
  it cannot degrade to an ungrouped table).

## Acceptance — how we'll know it's right

Run against a real repo and confirm:

- only my open issues (plus their ancestors as context) appear; the repo's
  ~500-issue total is irrelevant to runtime;
- one query (page-count logged if `>1`);
- owned issues nest under their parents; un-owned ancestors (`#2312`, `#2413`,
  `#884`) render dimmed with their `Prog` rollup and no health glyph;
- groups sort active-subtree-first (the recent `#2312` cluster floats to the top
  via its few-week-old tasks; long-untouched standalone issues sink to the bottom,
  flagged there by a rose Age);
- stale rows coloured against `--stale-days`; epics I own show a populated `Prog`;
- labels render without `:emoji:` shortcodes;
- `--format markdown` (nested list) and `--color never` are clean and copy-friendly.

## Explicitly out of scope for this slice

needs-triage group · the-rest group · the triage predicate · trees deeper than
three ancestor levels · the Assignee column · the `👍` column.
