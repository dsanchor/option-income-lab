# Orchestration — Livingston: Canonical Activity Schema Implementation (Rejected)

**Agent:** Livingston (Persistence & Integration)  
**Timestamp:** 2026-08-30T16:51:17Z  
**Task:** Implement canonical agent schema for validation activities  
**Status:** ❌ REJECTED (strict lockout)  

## Outcome Summary

Implemented canonical activity schema for validation activities to ensure uniformity with normal agent runs. Schema correctly uses identical canonical fields (reason, confidence, underlying_price, strike, expiration, etc.) from agent output. However, artifact was rejected due to production data loss in error-only fallback path. Agent locked out from revision per team protocol.

## Work Completed

1. **Schema Implementation**
   - Validation activities use canonical agent_data as base document (from `agent_runner._extract_activity_line`)
   - Augmented with minimal validation metadata (run_id, run_trigger, validation_status)
   - No custom validation-specific fields in main schema
   - Backward compatible with existing normal agent activities

2. **Field Design**
   - Canonical: activity, reason, confidence, underlying_price, strike, expiration, premium, iv, risk_rating, risk_flags
   - Metadata: run_id, run_trigger, validation_status
   - Debug-only: `_validation_meta` with snapshots (underscore-prefixed, non-canonical)

3. **Integration Points**
   - `agent_runner.py`: Added activity_data to run_contract_validation return
   - `contract_validation_integration.py._persist_validation_activity`: Uses canonical base, augments with metadata
   - `get_validation_status`: Returns canonical fields matching normal agent activities

4. **Test Coverage**
   - 14 contract validation integration tests passing
   - `test_canonical_schema_matches_normal_agent_run`: Proves schema parity
   - Regression test for error-fallback backward compatibility

## Rejection Reason

**Production Data Loss in Error Path:** Legacy error-only fallback (minimal {symbol, activity, timestamp, note, reason}) loses canonical fields when agent execution fails. No field recovery mechanism from evaluated_snapshot. Danny's retrospective confirmed this is production-critical code path with real data loss.

## Lock-Out Status

Strictly locked out from revision per Danny's protocol. Reassigned to Rusty for error-path redesign.

## References

**Decision:** `.squad/decisions/inbox/livingston-canonical-validation-schema.md`  
**Rejection:** Danny retrospective + lock-out directive  
**Impact:** Canonical schema design sound; error-handling requires architectural revision before merge

---

**Handoff:** Rejection documented. Rusty reassigned for error-path fix.
