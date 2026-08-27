## Description

<!-- One sentence: what changes for whom, beyond the title. Then the issue:
     "Closes #N" (closes on merge) or "Refs #N" (leaves it open). -->

### What changed

<!-- Behaviour-level bullets. Separate behaviour changes to existing
     functionality from purely additive work. -->

### What did NOT change

<!-- Deliberate absences, deferred halves, out-of-scope items — with issue
     refs. If nothing notable, say "Nothing notable." -->

## Evidence

<!-- Exact commands and what they prove. For fixes, what fails without this
     change. End with what was NOT verified, or "Nothing outstanding." -->

- `uv run pytest -q` → _(paste the summary line)_
- `uv run ruff check && uv run ruff format --check && uv run mypy` → _(paste the results)_

## Review guide

<!-- Where the risk is; what to read first. For small mechanical changes:
     "Small enough to read whole." -->
