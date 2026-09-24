# Orchestration Log: Manual Position-Agent Execution

**Time:** 2026-09-24T22:35:11Z
**Requested by:** Copilot

The severe same-symbol manual execution regression is fixed and independently
approved. Position identity now flows from the clicked dashboard row through
execution, persistence, status, and refresh; explicit identity fails closed,
legacy ambiguity returns 409, locks are position scoped, and scheduled runs
still iterate all positions. Scribe merged four inbox records, preserved the
rejection/revision/final-approval chronology in the canonical decision, and
prepared the approved product/test/documentation diff for commit and push.
