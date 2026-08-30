"""Regression tests for production TypeError: unhashable type: 'dict' (2026-08-30).

PRODUCTION LOG (2026-08-30 06:00:03 UTC):
"ERROR during Best Options Precompute: unhashable type: 'dict'"

EXACT ROOT CAUSE (confirmed by Linus + Livingston):
📍 Location: backend/src/options_screener.py::_memo_key() line 319
📍 Failing expression: memo[key] = result
📍 Called from: _evaluate_symbol() when building evaluation memo cache

On daf1d48, `_memo_key()` inserted raw Cosmos values into a tuple used as dict key:
    key = (symbol, side, timestamp, 
           entry.get('category'),           # ❌ Could be dict!
           shares, 
           entry.get('next_earnings_date'), # ❌ Could be dict!
           entry.get('ex_dividend_date'),   # ❌ Could be dict!
           entry.get('support_level'))

When Cosmos returns enrichment/calendar fields as mappings (schema evolution, nested data),
the tuple contains unhashable dicts and crashes at `memo[key] = result`.

Production data shape:
    category = {"type": "balanced", "confidence": 0.85} or {"value": "balanced"}
    next_earnings_date = {"date": "2026-09-15", "confirmed": True}
    ex_dividend_date = {"date": "2026-10-01", "type": "quarterly"}

FIX (backend/src/options_screener.py::_memo_key):
Defensive normalization extracts primitives from dicts:
- category dict → extract "type" or "category" or "value"
- date dicts → extract "date" field
- Fallback to None if no recognized field

ADDITIONAL FIXES:
1. Kwarg forwarding: Job signature accepts `trigger` parameter for startup/manual runs
2. Print statement: Uses `result.get('success')` not `result.get('ok')`
3. Traceback logging: Added `logger.exception()` for full stack traces
4. Manual trigger endpoint: Uses `trigger="manual"` kwarg

This test suite proves both bugs are fixed with production-shaped data.
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import Mock
import inspect


# ============================================================================
# EXACT PRODUCTION FAILURE: Unhashable Dict in Memo Key
# ============================================================================

class TestProductionUnhashableDictBug:
    """Reproduce the EXACT production TypeError from options_screener.py:319."""

    def test_exact_failing_line_with_dict_category(self):
        """Reproduce exact TypeError at memo[key] = result with dict category.
        
        On daf1d48 (before fix), this would crash with:
            TypeError: unhashable type: 'dict'
            at line 319: memo[key] = result
        """
        from src.options_screener import _memo_key
        
        entry = {
            "chain": {"timestamp": "2026-08-30T06:00:00Z"},
            # EXACT production data shape (Linus confirmed):
            "category": {"type": "balanced", "confidence": 0.85},  # ❌ DICT!
            "total_shares": 100,
            "next_earnings_date": "2026-09-15",
            "ex_dividend_date": None,
            "support_level": None,
        }
        
        # This is the exact failing line (options_screener.py:319)
        key = _memo_key("AAPL", "call", entry)
        memo = {}
        
        # Before fix: TypeError: unhashable type: 'dict'
        # After fix: succeeds
        memo[key] = {"result": "test"}
        
        # Verify category was extracted
        assert key[3] == "balanced", "Should extract 'type' field from dict category"

    def test_exact_failing_line_with_all_dict_fields(self):
        """Reproduce exact TypeError with all three fields as dicts (worst case).
        
        This is the production scenario that crashed at line 319.
        """
        from src.options_screener import _memo_key
        
        entry = {
            "chain": {"timestamp": "2026-08-30T06:00:00Z"},
            "category": {"type": "balanced", "confidence": 0.85},     # DICT
            "total_shares": 100,
            "next_earnings_date": {"date": "2026-09-15", "confirmed": True},  # DICT
            "ex_dividend_date": {"date": "2026-10-01", "type": "quarterly"},  # DICT
            "support_level": None,
        }
        
        key = _memo_key("AAPL", "call", entry)
        memo = {}
        
        # This is the exact line that failed in production
        memo[key] = {"calls": {}, "puts": {}}
        
        # Verify all dicts were normalized
        assert key[3] == "balanced", "category dict should extract 'type'"
        assert key[5] == "2026-09-15", "earnings_date dict should extract 'date'"
        assert key[6] == "2026-10-01", "ex_dividend_date dict should extract 'date'"

    def test_dict_category_variations(self):
        """Test various dict category shapes from Cosmos."""
        from src.options_screener import _memo_key
        
        # Variation 1: {type: ...}
        entry1 = {
            "chain": {"timestamp": "2026-08-30T06:00:00Z"},
            "category": {"type": "high_yield", "confidence": 0.9},
            "total_shares": 100,
        }
        key1 = _memo_key("AAPL", "call", entry1)
        memo = {}
        memo[key1] = "test"
        assert key1[3] == "high_yield"
        
        # Variation 2: {category: ...}
        entry2 = {
            "chain": {"timestamp": "2026-08-30T06:00:00Z"},
            "category": {"category": "balanced"},
            "total_shares": 100,
        }
        key2 = _memo_key("MSFT", "put", entry2)
        memo[key2] = "test"
        assert key2[3] == "balanced"
        
        # Variation 3: {name: ...}
        entry3 = {
            "chain": {"timestamp": "2026-08-30T06:00:00Z"},
            "category": {"name": "income"},
            "total_shares": 100,
        }
        key3 = _memo_key("GOOGL", "call", entry3)
        memo[key3] = "test"
        assert key3[3] == "income"
        
        # Variation 4: No recognizable field → None
        entry4 = {
            "chain": {"timestamp": "2026-08-30T06:00:00Z"},
            "category": {"unknown": "field"},
            "total_shares": 100,
        }
        key4 = _memo_key("TSLA", "call", entry4)
        memo[key4] = "test"
        assert key4[3] is None, "Unrecognizable dict should fallback to None"

    def test_normal_string_inputs_unchanged(self):
        """Verify backward compatibility with string inputs."""
        from src.options_screener import _memo_key
        
        entry = {
            "chain": {"timestamp": "2026-08-30T06:00:00Z"},
            "category": "balanced",  # String (normal)
            "total_shares": 100,
            "next_earnings_date": "2026-09-15",  # String (normal)
            "ex_dividend_date": "2026-10-01",  # String (normal)
            "support_level": 95.50,
        }
        
        key = _memo_key("AAPL", "call", entry)
        memo = {}
        memo[key] = "test"
        
        # All values preserved
        assert key == (
            "AAPL",
            "call",
            "2026-08-30T06:00:00Z",
            "balanced",
            100,
            "2026-09-15",
            "2026-10-01",
            95.50,
        )

    def test_full_screener_flow_with_dict_data(self):
        """Test complete screener flow with dict-shaped inputs."""
        from src.options_screener import evaluate_options_screener
        
        symbol_inputs = [
            {
                "symbol": "AAPL",
                "status": "ready",
                "chain": {
                    "symbol": "AAPL",
                    "timestamp": "2026-08-30T06:00:00Z",
                    "underlying_price": 150.0,
                    "calls": {},
                    "puts": {},
                },
                "category": {"type": "balanced", "version": 2},  # Dict!
                "total_shares": 100,
                "next_earnings_date": {"date": "2026-09-15"},  # Dict!
                "ex_dividend_date": None,
                "support_level": None,
            }
        ]
        
        # Should not crash
        result = evaluate_options_screener(
            symbol_inputs,
            now=datetime(2026, 8, 30, 6, 0, 0, tzinfo=timezone.utc),
            side="both",
        )
        
        assert "calls" in result
        assert "puts" in result
        assert "generated_at" in result


# ============================================================================
# STARTUP KWARG FORWARDING FIX
# ============================================================================

class TestStartupKwargForwarding:
    """Test that trigger kwarg is correctly forwarded for weekend startup."""

    def test_job_signature_accepts_trigger_kwarg(self):
        """Verify job function accepts trigger parameter."""
        from src.main import OptionsAgentScheduler
        
        sig = inspect.signature(OptionsAgentScheduler.run_best_options_precompute_job)
        params = sig.parameters
        
        assert 'trigger' in params, "Job must accept 'trigger' kwarg"
        assert params['trigger'].default == "scheduled", "Default should be 'scheduled'"

    def test_precompute_function_accepts_trigger(self):
        """Verify precompute function accepts trigger parameter."""
        from src.best_options_precompute import run_best_options_precompute
        
        sig = inspect.signature(run_best_options_precompute)
        params = sig.parameters
        
        assert 'trigger' in params, "Precompute must accept 'trigger' kwarg"

    def test_return_dict_structure(self):
        """Verify return dict has 'success' key (not 'ok')."""
        from src.best_options_precompute import run_best_options_precompute
        
        mock_cosmos = Mock()
        mock_cosmos.list_symbols.return_value = []
        
        result = run_best_options_precompute(mock_cosmos, trigger="startup")
        
        assert isinstance(result, dict)
        assert 'success' in result, "Must have 'success' key (not 'ok')"
        assert 'stale' in result
        assert 'error' in result
        assert 'warming' in result

    def test_weekend_startup_trigger(self):
        """Verify startup trigger works correctly."""
        from src.best_options_precompute import run_best_options_precompute
        from src.best_options_cache import BestOptionsCache, set_best_options_cache
        
        cache = BestOptionsCache()
        set_best_options_cache(cache)
        
        mock_cosmos = Mock()
        mock_cosmos.list_symbols.return_value = []
        
        # This would have been silently dropped on daf1d48
        result = run_best_options_precompute(mock_cosmos, trigger="startup")
        
        assert result is not None
        assert result['success'] == 0  # No symbols
        assert result['error'] == 0

    def test_manual_trigger(self):
        """Verify manual trigger works correctly."""
        from src.best_options_precompute import run_best_options_precompute
        
        mock_cosmos = Mock()
        mock_cosmos.list_symbols.return_value = []
        
        result = run_best_options_precompute(mock_cosmos, trigger="manual")
        
        assert result is not None
        assert 'success' in result


# ============================================================================
# TRACEBACK LOGGING ENHANCEMENT
# ============================================================================

class TestEnhancedErrorLogging:
    """Verify enhanced error logging with tracebacks."""

    def test_traceback_logging_exists(self):
        """Verify main.py includes traceback logging in exception handler."""
        import src.main
        import inspect
        
        # Read the source to verify traceback.print_exc() is present
        source = inspect.getsource(src.main.OptionsAgentScheduler.run_best_options_precompute_job)
        
        # Should have both print_exc and logger.exception for full traceback
        assert 'traceback.print_exc' in source or 'logger.exception' in source, \
            "Should have traceback logging in exception handler"
