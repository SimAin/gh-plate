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
