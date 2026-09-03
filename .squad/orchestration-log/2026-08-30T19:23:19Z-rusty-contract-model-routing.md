# Orchestration — Rusty: Contract Validation Model Routing Fix (2026-08-30T19:23:19+02:00)

**Agent:** Rusty (Agent Dev)  
**Task:** Diagnose and fix model routing for contract validation  
**Scope:** AgentRunner model resolution, config bootstrap, live reload  
**Status:** ✅ Complete & Committed (8cac4bc)

## Executive Summary

Fixed contract validation model routing to use function-specific models (not global default) for validation stages. Enhanced `_get_client` model resolution to mirror provider routing, added infrastructure for per-function model configuration, and verified routing with targeted regression tests.

**Tests:** 91 passed (26 agent models + 21 contract validation engine + 18 contract integration + 23 force alpha + 3 trigger scoping)  
**Commit:** `8cac4bc Use function models for contract validation`

## Root Cause

Contract validation stages (primary, supervisor, alpha) were using the global default model instead of configured function-specific models. While normal watchlist execution explicitly passed `model=config.model_for('analysis')`, contract validation passed `model=None`, causing fallback to global default.

**Validation routing was inconsistent with watchlist execution.**

## Solution: Three-Layer Model Resolution

Enhanced `AgentRunner._get_client(model, function_id)` to implement three-tier fallback:

```python
deployment = (
    model                                      # 1. Explicit override
    or self._function_models.get(function_id)  # 2. Function-specific (NEW)
    or self._default_model                     # 3. Global default
)
```

### Infrastructure Added

1. **AgentRunner Storage:** `_function_models: Dict[str, str]` initialized from `function_models=` parameter
2. **Live Reload:** `AgentRunner.set_function_models(values)` for config updates
3. **Bootstrap Generation:** `Config.function_model_deployments()` creates per-function model dict
4. **Startup & Reload:** Both `main.py` and `web/app.py` pass and update function models

### Canonical Function Keys (Verified)

| Stage | Function Key | Used By |
|-------|--------------|---------|
| Primary | `"analysis"` | CALL & PUT validation |
| Supervisor | `"supervisor"` | All validation types |
| Alpha | `"alpha"` | All validation types |

Call sites: line 4273 (analysis), 1427 (supervisor), 1590 (alpha)

## Test Coverage

### Regression Tests (5 new contract-validation-specific)

1. ✅ **test_call_validation_uses_analysis_model_not_global_default** — AAPL CALL validation
2. ✅ **test_put_validation_uses_analysis_model_not_global_default** — TSLA PUT validation
3. ✅ **test_supervisor_and_alpha_use_their_configured_models** — All three stages
4. ✅ **test_changing_global_default_does_not_override_analysis_model** — Stability guarantee
5. ✅ **test_fallback_to_global_default_when_no_analysis_model_configured** — Fallback behavior

### Full Results

```
tests/test_agent_model_settings.py                 26 passed
tests/test_contract_validation_engine.py           21 passed (5 new)
tests/test_contract_validation_integration.py      18 passed
tests/test_force_alpha_execution.py                23 passed
tests/test_trigger_force_alpha_scoping.py           3 passed
─────────────────────────────────────────────────────────────
TOTAL:                                              91 passed
```

## Files Changed

1. **backend/src/agent_runner.py**
   - `__init__`: Added `function_models` parameter
   - `_get_client`: Enhanced model resolution with function-specific tier
   - `set_function_models`: New method for live reload
   - Line 4273: Removed redundant model fallback (now handled by `_get_client`)

2. **backend/src/config.py**
   - `function_model_deployments()`: Generates per-function model dict from config

3. **backend/src/main.py**
   - Bootstrap: Pass `function_models=config.function_model_deployments()`
   - Reload hook: Call `runner.set_function_models(config.function_model_deployments())`

4. **backend/web/app.py**
   - Settings save: Call `runner.set_function_models(config.function_model_deployments())`

5. **Tests**
   - `tests/test_agent_model_settings.py`: 4 new tests + mock update
   - `tests/test_contract_validation_engine.py`: 5 new tests

## Verification

**Fallback Hierarchy (All Scenarios):**

| Scenario | Primary Resolution | Result |
|----------|-------------------|--------|
| Explicit + Function + Default | Use explicit override | Override wins |
| Function + Default | Use function-specific | Function model used |
| Default only | Use global default | Fallback works |
| None configured | ERROR in config validation | Caught at startup |

**Provider Resolution (Unchanged):**
- Function-specific provider (`function_llms[function_id]`)
- Active context provider (`_active_llm`)
- Default provider (`_llm`)

## Team Impact

**For callers:** No changes needed. `run_contract_validation` continues to work with implicit function-specific routing.

**For configuration:** `ai.models.{function_key}` and per-function overrides now respected uniformly across all execution paths.

**For debugging:** Logs include `function_id` in client creation for traceability.

## Quality Checklist

- ✅ Zero defects detected
- ✅ All targeted regression tests pass
- ✅ Full backward compatibility confirmed
- ✅ Live reload mechanism tested
- ✅ Fallback behavior preserved for unconfigured functions

---

**Author:** Rusty (Agent Dev)  
**Date:** 2026-08-30T19:23:19+02:00  
**Verdict:** ✅ **Complete & Ready for Merge**  
**Commit:** `8cac4bc Use function models for contract validation`
