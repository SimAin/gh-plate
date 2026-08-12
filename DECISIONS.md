# Decision log

Design decisions for `plate` (formerly `issue-check`), with the reasoning behind each. Recorded during
the planning exploration; see `MVP.md` for the slice that shipped.

---

## D1 — Grouping is one stacked order, not a mode switch

**Decision:** Rows are grouped `yours` → `needs triage` → `the rest`, stacked top to
bottom. There is no `--triage` flag that flips which group leads.

**Why:** The question "maintainer triaging inbound vs. IC working assigned issues" came
up as a possible mode switch. The answer: one stacked order — mine on top,
your-next-move second, the settled rest last. It serves both roles because the
stack already puts each role's priority where it looks first.

---

## D2 — Label-agnostic

**Decision:** Show the raw label set in a dimmed, truncated column. Assume no label
naming conventions (`priority:*`, `type:*`, `status:blocked`, etc.).

**Why:** Issues are only as structured as a repo's labels, and conventions vary. Staying
agnostic works in any repo immediately. An opt-in label→semantics mapping was
considered and deferred — not worth the config surface for v1.

**Consequence:** The headline glyph cannot rely on a `blocked` / `needs-info` label, so
that state is dropped. The remaining neglect-vs-activity axis is the right one for
issues regardless. (Interacts with D3's untriaged definition, which is purely
structural — "zero labels" — and so survives agnosticism.)

---

## D3 — Hierarchy is core from day one

**Decision:** Parent/child issue relationships are part of the core data model and
triage logic from the first version — not a later increment.

**Why:** The team uses parent/child issue relationships enough that a flat view would
misrepresent reality — an epic and its children would read as N independent things
needing attention when they are one unit.

**Consequence:** Forces the GraphQL data layer (see D4) and the "tree as signal, not
layout" model: default view shows units of attention (top-level issues + independently
actionable children), with children rolled up via `subIssuesSummary` into a `Prog`
column. Full subtree state aggregation is explicitly *not* done (too expensive); the
`Prog` count plus an untriaged-suppression heuristic stand in for it.

---

## D4 — Native sub-issues only

**Decision:** Read GitHub's native (GA) sub-issue relationships via GraphQL. Ignore
legacy markdown task-list checkboxes (`- [ ] #123`) and project-board parent fields.

**Why:** Native sub-issues are the modern, canonical mechanism and what the team uses.
Task-list parsing is messy and project-board fields are a different system.

**Consequence:** The cheap single `gh issue list --json` REST call is abandoned —
sub-issue relationships are not in its field set. `issue-check` uses one repo-wide
GraphQL query from the start. `subIssuesSummary { total, completed }` gives the `Prog`
rollup without recursing the tree; `parent { number, title }` gives breadcrumbs.

---

## D5 — Configurable "special" labels (a deliberate, opt-in break from D2)

**Decision:** Specific labels can be named in a user config file and given a
*style* (`alert` / `warn` / `info` / `hide`). The renderer promotes a recognised
label to the front of the Labels cell and colours it bright (or, for `hide`,
drops it). Ships with one default — `blocked` → `alert` — which the file
overrides or extends.

**Why:** D2 (label-agnostic) keeps the tool working in any repo with no config.
But some labels (`blocked`, `needs-info`) carry real "this is stuck / needs
attention" signal worth calling out. Rather than hardcode label *meanings* (which
D2 rightly refuses), the meaning lives in **user config**: opt-in, per-user, easy
to update. The default behaviour stays agnostic apart from the one obvious case.

**Consequence:** A new `config.py` reads JSON (stdlib-only, preserving zero deps
and the 3.11 floor) from `$ISSUE_CHECK_CONFIG` or `~/.config/issue-check/config.json`.
Matching is case-insensitive with `*` globs (`status:*`). The model/render layers
stay pure — the resolver is passed in from the CLI. Colour is now spent in the
Labels cell as well as on health: an accepted, intentional widening of the colour
budget, justified because the signal is one the user explicitly asked to surface.

---

## D6 — Sprint view from a Projects v2 board (per-repo, server-side current iteration)

**Decision:** A second view, `issue-check --sprint`, renders one repo's GitHub
Projects v2 board scoped to its **current iteration** — a *team* view (every issue in
the sprint, not just mine), grouped `yours` → `others` → `unassigned`, sorted
active-first by board Status. The repo→project mapping lives in a per-repo `repos`
block in the existing global config; the default view is unchanged.

**Why:** A team uses a project board as a sprint board, and "what's in our current
sprint, and who has it?" is a distinct question from the default "what's on my plate?"
The board is the source of truth for sprint membership and status, so the view reads
it directly rather than re-deriving a sprint from issue fields. Scope is per-repo
(config, not hardcoded) because only some repos have such a board.

**Why server-side `iteration:@current`:** The board holds thousands of historical
items across past iterations and has no REST sub-issue data. Probing the live board
showed `projectV2.items(query:)` accepts the board's own filter syntax and that
`iteration:@current` resolves the active iteration from its dates GitHub-side — so the
view is **one cheap query** for ~15 items, with no client-side date math and no
scan of the full board. Including Done/closed items is then free (board items carry no
state filter). This mirrors D4's "let the API do the work" stance.

**Consequence:** `github.fetch_sprint_items` is a second impure entry point;
`model.build_sprint_view` (pure) buckets + sorts; `render.sprint_table` /
`sprint_markdown` reuse the existing primitives; `config` parses the `repos` block and
project URLs. A board can span repos, so items are filtered to the requested repo
client-side, and PR/draft board items are dropped (issue-centric view). The Assignee
column — cut from the default view as all-"me" (see `MVP.md`) — returns here as signal.
See `SPRINT.md` for the built slice.

---

## D7 — Owner-wide view (`--owner`): one owner-scoped search, repo sections, repository-local hierarchy

**Decision:** A third view, `issue-check --owner NAME`, renders every open issue
across all of an owner's repositories in one table, grouped into per-repository
sections (most recently active repo first), each section a normal issue tree. The
flag is `--owner`, not the issue's original `--org`: a personal account owns repos
too, and the view must cover both. The account type (organization vs. user) is
resolved by one `gh api users/NAME` call, which doubles as up-front validation.
The view shows **all** open issues by default, with `--mine` as opt-in to narrow to
your assignments; `--owner` needs no checkout and never calls `current_repo`.

**Why:** "What's open across everything this owner has?" is a distinct question
from the default per-repo "what's on my plate?" and the per-repo `--sprint` team
view. Generalizing the issue's `--org` sketch to `--owner` costs nothing and covers
personal accounts, which own repos just like orgs do; resolving the account type is
required anyway to pick the right search qualifier, so folding validation into that
same call is free. Defaulting to *all* open issues (not just mine) is deliberate:
personal-project issues are frequently unassigned, and an owner-wide view that hid
them would misrepresent the backlog — so unassigned rows render full-weight (open
work you could pick up) while others' rows are dimmed.

**Why one owner-scoped search:** rather than enumerate the owner's repos and query
each, the view issues a single owner-scoped GraphQL Issues search (`org:` / `user:`
+ `archived:false sort:updated-desc`), reusing the yours-view query shape. This is
D4/D6's "let the API do the work" again — zero per-repo requests. Archived repos are
excluded by design (done, not live work). GitHub's search API caps any query at 1000
results, so `total` can exceed what pagination retrieves; the CLI compares
`len(issues) < total` and prints a note ("showing N of M") whichever ceiling —
`--limit` or the 1000-result cap — did the truncating, so a partial result is
**never** silent (#43 acceptance criterion). `sort:updated-desc` means truncation
drops the *least* recently active issues, keeping the view active-first (D1, D6).

**Consequence:** Identity is repo-qualified `(repo, number)`, and hierarchy is
repository-local: a cross-repo parent is not materialised — its child renders as a
root in its own repo's section rather than nesting under a tree from elsewhere.
Rows are classified by assignment — yours (your login among the assignees),
someone else's (dimmed whole), unassigned (kept full-weight) — while `context`
(a structural ancestor) becomes its own axis rather than the inverse of `mine`. Config gains an `owners` alias table (alias →
owner name, case-insensitive, literal fallthrough, alias-shadows-literal); an alias
that fired is shown in the output (`work → company-org`). Built on four groundwork
PRs kept small and independently reviewable: repo-qualified model identity +
`group_by_repo` (#44), the owner-scoped search + `resolve_owner_type` (#45), the
`owners` config block + `resolve_owner` (#46), and the owner renderers (#47); this
view is the CLI wiring that ties them together.

---

## D8 — `core`/domain package split, enforced by a boundary test (issue #50)

**Decision:** The package is renamed `plate` and restructured into `plate/core/`
(shared, domain-agnostic plumbing: `gh.py`, `render.py`, `config.py`) plus one
directory per domain — today `plate/issues/` (`model.py`, `github.py`,
`render.py`, `cli.py`), with `plate/prs/` planned to sit beside it (issue #53).
The rule: a domain package may import `plate.core`, but never another domain
package, and `plate.core` never imports a domain package back. This is not a
convention left to reviewers to catch — `tests/test_boundaries.py` walks the
AST of every file under each domain directory and under `plate/core/` and
fails with the offending file + import if the rule is ever broken, discovering
new domain directories automatically (by scanning `src/plate/` for package
dirs other than `core`) so a future `plate/prs/` is covered with zero changes
to the test.

**Why:** This is the founding constraint of the `gh-pr-status` absorption epic
(#50): the plan is to fold a sibling PR-status tool into this repo as a second
domain (`plate/prs/`) rather than maintain two near-identical CLIs. That only
works if the two domains stay genuinely independent — sharing `git`/`gh`
plumbing and presentation primitives, never reaching into each other's models
or renderers. Deciding and enforcing the boundary now, while there is still
only one domain, is cheaper than retrofitting it once `plate/prs/` exists and
some shortcut has already grown a cross-domain import. `gh-issue-check` is
renamed to `plate` because the tool's identity is no longer "the issue
checker" once a second domain sits beside it. The rename is a hard switch —
no `issue-check` alias: rollback is git history plus versioned releases, and
the tool's consumers are known personally (epic #50, decision 2 as revised).

**Consequence:** `github.py` splits into `core/gh.py` (the shared
`run_command` subprocess chokepoint, `PlateError`, and repo/login/owner-type
resolution — nothing issue-specific) and `issues/github.py` (the Issues search
and Projects v2 board GraphQL, built on `core/gh.py`). `render.py` splits into
`core/render.py` (ANSI/width primitives with no opinion on what they're
rendering) and `issues/render.py` (the issue tree, sprint table, and
owner-wide view). `config.py` moves wholesale into `core/` since config is
domain-agnostic today; `model.py` moves wholesale into `issues/` since it's
entirely issue-shaped. `cli.py` splits into a thin top-level `plate/cli.py`
(parser assembly + dispatch only) and `issues/cli.py` (the `issues`
subcommand's flags and `run()` logic), following the subcommand skeleton
landed just before this split. `IssueCheckError` is renamed `PlateError` since
it is no longer issue-specific — every domain's failures surface through it.

---

## D9 — Owner-wide PR view: `--mine` means `author:LOGIN` (issue #54)

**Decision:** `plate prs --owner OWNER` lists every open PR across an owner's
repositories (mirroring D7's issues view: one owner-scoped search, per-repo
sections most-recently-active first, `archived:false sort:updated-desc`, the
1000-cap and `--limit` truncation notes). Its `--mine` flag narrows with the
single search qualifier **`author:LOGIN`** — "PRs you authored", not
"PRs you're involved in" and not the repo view's author-or-assignee "mine".

**Why `author:`:** PRs are authored artifacts — unlike issues (where work is
*assigned*, so D7's `--mine` is `assignee:LOGIN`), a PR's primary owner is the
person who opened it, so "my PRs across an owner" most naturally means
authored-by-me. The alternatives both lose:

- **`involves:LOGIN`** is too broad — it also matches PRs you merely commented
  on or were mentioned in, which is "PRs I've touched", not "my PRs".
- **The repo view's author-or-assignee "mine"** can't be expressed as a single
  search qualifier: GitHub search qualifiers AND together, so
  `author:X assignee:X` means *both*, and expressing the OR would take two
  separate searches merged client-side — double the request cost (and two
  1000-result ceilings to reconcile) for a rare need.

**Consequence:** The repo view's yours-grouping keeps its author-or-assignee
definition — the two answer different questions ("which of these rows is mine
to move?" vs. "fetch only mine, owner-wide"), so the asymmetry is deliberate.
The search pagination loop moved from `plate/issues/github.py` into
`plate/core/gh.py` as `search_paginated` (D8-legitimate: shared infra, not a
view change); the PR domain gets its own `PR_OWNER_QUERY` /
`owner_search_query` / `fetch_owner_prs` as parallel code, never importing
from `plate.issues`. The viewer's login comes from `gh.current_login()` at the
CLI layer (a top-level `search` query has no `viewer` root worth coupling to),
and `PrRow` gains a `repo` field read from each node's
`repository.nameWithOwner` so `group_by_repo` can section the flat list.

---

## D10 — Display width via stdlib `unicodedata`, no `wcwidth` dependency (issue #9)

**Decision:** The width math behind every table (`visible_length`, `truncate`,
`format_cell`) measures **display columns**, not code points, computed with a
small `char_width` helper over the stdlib: combining marks and the zero-width
code points (ZWJ, variation selectors, zero-width space) count 0, East Asian
`W`/`F` (CJK and modern emoji) count 2, everything else counts 1. No new
dependency and no vendored width table.

**Why:** Emoji- and CJK-prefixed titles are common, and code-point counting
shifted every column to their right — ragged tables in exactly the repos most
likely to adopt the tool (issue #9). The zero-dependency stance (D5/D8's 3.11
stdlib-only floor) rules out `wcwidth`; a vendored table is maintenance the
stdlib already carries. `east_asian_width` gets CJK and East-Asian-Wide emoji
right for one small function.

**Consequence:** Fixing the three `core/render.py` primitives fixes every view,
since all renderers funnel through them; `truncate` now budgets and slices in
columns, reserving one for the `…` and never splitting a double-width glyph.
**Known limitation, accepted:** multi-emoji ZWJ sequences (family emoji, etc.)
can still overcount versus some terminals — the same limit as `wcwidth`; no
grapheme clustering is attempted. The Status-cell `strip_emoji` workaround
stays (it serves statusOrder matching, #7); its retirement is a follow-up.

---

## D11 — PR Age/Last columns: whose move is it, carried by weight (issue #79)

**Decision:** The PR views' `Age` column is redefined to **days since
`createdAt`** (total time in flight — context, always dim), and a new 4-wide
**`Last`** column shows **days since the last *human* activity**, taken as the
max across three channels: the head commit's `committedDate` (already fetched
for the CI rollup), `reviews(last: 1)`, and `comments(last: 1)`. Direction is
viewer-relative and carried by **weight, not colour**: `Last` renders full
weight when the other side moved last (the days are *your* lag — respond on
your PR, review on a to-review PR) and dim when you moved last. Rose still
outranks weight for stale, and staleness now anchors on the last human move
rather than `updatedAt`. Bot actors (the D-bot conventions plus GitHub's own
`Bot` type) never count as activity; a bot-only PR falls back to `updatedAt`,
dim, with no direction claimed. The summary line gains `N your move`; the
markdown table carries both columns with direction in the Signal column.

**Why:** `updatedAt` was a blunt staleness proxy — it bumps on label churn and
is directionless: `2d` on your PR reads identically whether a reviewer
requested changes 2 days ago (you owe a response) or you pushed a fix (they
owe a review). The two questions worth answering are "how long since the last
move?" and "whose move is next?" — together, the waiting-on signal.
`reviews(last: 1)` is deliberately not `latestOpinionatedReviews`: a
comment-only review (an inline-feedback batch) is real activity. Weight over a
new hue keeps the colour budget rationed to health; a `2d ⇠them` text variant
was mocked and rejected as +5 columns on a table already flagged as tight.

**Consequence:** Both queries gain `createdAt` and the two trailing-event
connections (same round trip); `PrRow` gains `last_activity_days` /
`last_activity_mine` (`None` = no direction claimed: bot-only fallback,
missing commit author login, or unknown viewer) plus the per-channel lags
(`last_commit_days` / `last_review_days` / `last_comment_days`) — kept even
though the views render only their max, so a smarter court heuristic (the
accepted "ping problem": your own nudge comment flips `Last` to dim) needs no
refetch. **Known caveats, accepted:** `committedDate` survives rebases with
old dates, and timeline-only events (force-pushes, review requests) are not
counted — `timelineItems` was rejected as heavier for marginal coverage.

---

## D12 — `--timeline`: a strip-only sub-line, opt-in payload (issue #80)

**Decision:** `plate prs --timeline` adds one sub-line per row: a 28-day
activity strip (one cell per UTC day, rightmost = today) starting at the Title
column, bound to its row by a dim `↳` under the PR number. Vocabulary: `◆`
commit, `●` comment, `▲` review, `·` quiet day; your own events dim, other
people's gold — or the review verdict's colour (rose = changes requested,
green = approved). Within a day, one glyph wins by review > commit > comment
precedence (rank ties go to the later event). The events connection
(`timelineItems(last: 30)`, three item types) is added to the query **only
when the flag is set**; flag-off output and query are unchanged. Repo view,
terminal format only: `--format markdown` ignores the flag (documented in
``--help``), `--owner` rejects it. Bots are skipped via the shared
`is_bot_actor`, so a bot-only PR shows an all-quiet strip; muted rows dim
their sub-line whole.

**Why:** The Age/Last columns (D11) say who moved last and how long ago; the
strip answers the question they can't — "what has the rhythm been, and where's
the silence?" **Strip-only** is the load-bearing choice: earlier drafts paired
the strip with a plain-language annotation ("alice requested changes 2d ago"),
but the text duplicated D11's columns and every rendering of it fought the
table's idioms — dim text dissolved among dim columns, full weight scanned as
another PR row. Deleting it fixed what styling couldn't. A 14-wide `Activity`
*column* was also sketched and rejected: it pushed the table to 122 columns.
An opt-in flag pays the heavier fetch only when asked — the same
view-costs-what-it-shows stance as D6's single board query.

**Consequence:** The PR query becomes a template with an `__EXTRA_FIELDS__`
slot; `PR_QUERY` (slot empty) stays byte-identical to before, and
`PR_TIMELINE_QUERY` fills it with the events connection. `PrRow` gains
`timeline` (day buckets, oldest first, None when not fetched); `DayEvent`
carries kind/review-state/viewer-relative direction. **Accepted limits:** a
very chatty PR's 30-event fetch may not reach the window's left edge (older
days render falsely quiet), day buckets are UTC (a late-evening local commit
can land on "tomorrow"), and `committedDate` carries rebased-old dates as in
D11.

---

## D13 — `plate retro`: your own activity, split by owner, on the sources that can see it (issue #81)

**Decision:** A third domain, `plate/retro/`, renders a day-by-day panel of
the viewer's own activity — **reviews / commits / PRs opened** — over a 14-day
window (`--days`, bounded 7-30), as **one self-contained panel per repository
owner** (most active first), so a work org and personal repos read separately.
It is a **subcommand, not a `prs` flag**: different data, different subject
(you, not the work), different tense (retrospective), and it needs no
checkout — repo resolution is never touched. Each channel reads the best
source that can see private activity: reviews from the REST **events feed**,
PRs opened from **issue search** (`is:pr author:LOGIN created:>=`), and
commits from the feed's **push events expanded through the compare API** —
one `base...head` range per (repo, branch) chain, fetched in parallel,
keeping only commits the viewer authored, deduped by sha, bucketed by their
real committer dates.

**Why not the obvious sources — both probed live and rejected:**

- **GraphQL `contributionsCollection`** (the issue's original spec): its
  itemized connections expose *public* activity only — private work collapses
  into an opaque `restrictedContributionsCount` even for the authenticated
  user querying themselves (probed: totals 0, restricted 69, on an account
  whose work is all private). A retro blind to private work is an empty panel.
- **Commit search** (`search/commits`): sees private repos, but indexes
  **default branches only** — probed: 8 in-window pushes to work feature
  branches, 0 in-window search results. Branch work would stay invisible
  until merge, which defeats "I did a bunch of commits *today*".
- A pushes-per-day channel (no expansion) was built first and rejected in
  review: pushes flatten magnitude, and magnitude is the point.

**Panel:** per owner — divider, weekday ruler (weekends dim, today bold),
digits per active day with dim `·` for quiet ones (cells cap at 99, the `Σ`
totals column doesn't), and a per-row `today` / `last Nd ago` / `none in Nd`
annotation. The one tint is the gold nudge on a reviews row quiet ≥ 2 days —
the motivating glance; commits/opened are nobody's duty and stay dim.
Markdown gets a `## OWNER` heading + `channel | total | last` table per
section.

**Consequence & accepted limits:** the boundary test covers the new domain
automatically (it imports only `plate.core`; the compare fan-out uses a
stdlib thread pool). Reviews and commits inherit the events feed's caps (300
events / 90 days): when a capped feed can't reach the window's start, a
stderr note says early days may be undercounted rather than passing them off
as rest. A compare that can't resolve (rewritten history) falls back to one
commit per push on the push's day — counted, and reported via its own note.
Commits with an email not linked to the GitHub account don't match the
author filter and are dropped; rebase-rewritten shas can count twice across
branches. Day buckets are UTC.

---

## D14 — Retro `closed` channel: all closed, `closedAt` buckets, flow not tracking (issue #91)

**Decision:** A fourth retro channel, `closed`, follows `opened` in
`CHANNEL_ORDER`: PRs the viewer authored that left the plate — **merged and
closed-without-merge alike** — from one extra issue search
(`is:pr is:closed closed:>=`), bucketed by the UTC day of each item's
`closed_at`, attributed to owners via `repository_url` exactly like
`opened`. The channel answers "left my plate", not "succeeded": hiding
unmerged closes would make an abandoned PR look like one still waiting. The
row stays dim under any quiet-length (no gold nudge — closing PRs isn't a
social duty), and the markdown totals table picks it up for free.

**Rows don't reconcile within a window — by design.** A PR opened 20 days
ago can close today, so `closed` can exceed `opened`; the channels compare
flow rates, not track individual PRs (noted in the README so it doesn't come
back as a bug report). The model takes opened and closed items as **separate
inputs**: a PR opened *and* closed inside the window comes back from both
searches, and merging the lists would double-count it in each row.

**Consequence & accepted limits:** one more search per run, with its own
1000-result truncation note. A merged/abandoned split is deferred (issue
#91's "Later" menu).

---

## Standing decisions carried from the architecture discussion

- **Standalone, not a shared package.** The GraphQL fetch layer is specific enough
  that little is worth centralising into a shared package. Revisit only if a
  sibling tool ever appears.
- **`--tree` renderer deferred.** Data model supports it; renderer ships after the
  flat rolled-up view.
- **Linked-PR signal deferred.** High value but needs timeline/GraphQL; ship as opt-in.

---

## Open (not yet decided)

- Column load: are 👍 and Cmt always-on, or opt-in? (Nine columns is tight on narrow
  terminals.)
- Breadcrumb in Title cell vs. a dim trailing parent tag.
- Column drop order under narrow-terminal truncation.
