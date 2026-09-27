# Basher — lot average price review

**Timestamp:** 2026-09-27T05:26:57Z
**Requested by:** Copilot
**Mode:** Background reviewer

Basher added backend and frontend contract coverage and initially rejected the
implementation because a BUY `CASH_TOP_UP` leg received an average price. The
shared helper was corrected to exclude non-share corporate-action legs, after
which Basher approved with 106/106 backend and 125/125 frontend targeted tests
passing.
