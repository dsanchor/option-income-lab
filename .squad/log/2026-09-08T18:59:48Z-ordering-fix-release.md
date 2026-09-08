# Ordering Fix Release Session Log

**Date:** 2026-09-08  
**Release:** fix: migrate holdings engine to FIFO and fix CA leg ordering  
**Commit:** f5ed79f  
**Status:** Released to main

## Summary

Commit f5ed79f merges holdings engine FIFO rewrite and corporate-action leg ordering fix, resolving Basher's 66% failure-rate bug found in Share Consolidation release validation (RKT share consolidation scenario).

**Changes:**
- Holdings engine: CMP → FIFO lot depletion by (trade_date, ca_group_id, ca_group_seq, id)
- CA leg ordering: deterministic sequence regardless of UUID order
- BUY gross/net semantics: aligned with 89a7c2a release
- Migration script: repair_buy_ledger_fields.py for correcting existing records

**Review:** Livingston (Persistence & Integration Engineer) — APPROVE WITH NOTES (no blocking defects)  
**Validation:** Targeted suite (portfolio/fifo/holdings/scrip/consolidation/ledger_fields) — 1119 passed, 0 failed

**Documentation:** Orchestration log created; .squad/decisions.md updated and staged (decision: "Basher's Review Finding: Ordering Invariant Violation" → RESOLVED)
