# Orchestration — Danny User Directive & Design (2026-08-18T09:09:45Z)

**Agent:** Danny (Lead)  
**Duration:** Full session  
**Activity:** Received user directive, authored comprehensive persistent option-chain design doc, escalated rejection to new specialist.

## Summary

User requested: persistent option chain must preserve last-known-good quotes; invalid Yahoo data must not overwrite valid stored data; TradingView must only enrich with valid fields; contracts must not expire on TTL but on real expiration dates.

**Artifact:** `.squad/decisions/inbox/copilot-directive-2026-08-18T09-09-45.md` (user directive)

Authored frozen design doc `.squad/decisions/inbox/danny-persistent-option-chain-merge.md`:
- Diagnosed 5 structural gaps in existing `OptionsChainCache`: no persistence (G1), TradingView destroys valid Yahoo fields (G2), derived fields merge incorrectly (G3), bid-zero classification wrong (G4), malformed expiration keys immortal (G5).
- Specified exact validity rules per field (§2), three-phase merge (§3), retention/pruning policy (§4), atomicity/concurrency design (§5), and 21 test scenarios (§7).
- Assigned strict ownership: Linus (pure merge logic), Rusty (persistence/lifecycle).

**Gate:** Design accepted by Danny at end of session, ready for implementation.

## Handoff

Two parallel work streams:
1. **Linus:** Implement frozen seven-function interface in `src/options_chain_merge.py` + provider normalizer fixes.
2. **Rusty:** Implement store/cache integration (hydrate, persistence, locking) + concurrency tests.
3. **Basher:** Review merge semantics and concurrency/failure paths before either merges.

**Escalation (same day):** Initial rejection D1-D5 prompted casting of Livingston (new Persistence & Integration Engineer) with explicit revision directive.
