# Decision log

Design decisions for `issue-check`, with the reasoning behind each. Recorded during
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
