# Session Log: Dashboard Concurrency and Scrip FMV Fixes

**Timestamp:** 2026-09-22T14:38:07Z

The dashboard manual-trigger path now uses run-ID-owned slots, atomic
process-state initialization, deterministic run aggregation, and transactional
thread startup cleanup. These rules prevent stale cleanup and initialization
races while preserving the last successful completion through later failures.

Scrip-dividend share acquisitions now treat positive authoritative EUR FMV as
FIFO cost basis, explicit zero as a real zero-cost lot, and blank FMV as
incomplete. Backend create/correction normalization is authoritative and share
FMV remains separate from dividend income.

Basher approved both integrated fixes after the rejected intermediate revisions
were corrected and independently validated.
