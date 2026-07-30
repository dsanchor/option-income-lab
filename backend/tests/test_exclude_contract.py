"""Unit tests for exclude_contract filter in options_chain_filters.py"""

import pytest
from src.options_chain_filters import exclude_contract


def test_exclude_contract_removes_exact_match():
    """Test that exclude_contract removes ONLY the exact strike+expiration combo."""
    # Build a simple chain with multiple strikes and expirations
    chain = {
        "symbol": "TEST",
        "timestamp": "2026-07-09T08:00:00Z",
        "calls": {
            "20260717": {
                "65.0": {"bid": 0.15, "ask": 0.20, "delta": 0.25},
                "60.0": {"bid": 0.50, "ask": 0.55, "delta": 0.35},
            },
            "20260821": {
                "65.0": {"bid": 0.30, "ask": 0.35, "delta": 0.28},
            },
        },
        "puts": {},
    }
    
    # Exclude the $65 call expiring 2026-07-17
    result = exclude_contract(chain, 65.0, "2026-07-17", "call")
    
    # The exact match should be removed
    assert "65.0" not in result["calls"]["20260717"]
    
    # Same expiration, different strike should be kept
    assert "60.0" in result["calls"]["20260717"]
    assert result["calls"]["20260717"]["60.0"]["bid"] == 0.50
    
    # Same strike, different expiration should be kept
    assert "20260821" in result["calls"]
    assert "65.0" in result["calls"]["20260821"]
    assert result["calls"]["20260821"]["65.0"]["bid"] == 0.30


def test_exclude_contract_strike_key_variants():
    """Test that exclude_contract matches strike by float value, handling different formats."""
    chain = {
        "symbol": "TEST",
        "timestamp": "2026-07-09T08:00:00Z",
        "calls": {
            "20260717": {
                "65.00": {"bid": 0.15, "ask": 0.20, "delta": 0.25},
                "60.0": {"bid": 0.50, "ask": 0.55, "delta": 0.35},
            },
        },
        "puts": {},
    }
    
    # Strike provided as "65" should match "65.00"
    result = exclude_contract(chain, 65, "2026-07-17", "call")
    
    # The 65.00 key should be removed (float match)
    assert "65.00" not in result["calls"]["20260717"]
    # The 60.0 key should remain
    assert "60.0" in result["calls"]["20260717"]


def test_exclude_contract_expiration_format_variants():
    """Test that exclude_contract handles both YYYY-MM-DD and YYYYMMDD expiration formats."""
    chain = {
        "symbol": "TEST",
        "timestamp": "2026-07-09T08:00:00Z",
        "calls": {
            "20260717": {
                "65.0": {"bid": 0.15, "ask": 0.20, "delta": 0.25},
            },
        },
        "puts": {},
    }
    
    # Test with hyphenated format
    result1 = exclude_contract(chain, 65.0, "2026-07-17", "call")
    assert "20260717" not in result1["calls"] or "65.0" not in result1["calls"].get("20260717", {})
    
    # Test with compact format
    result2 = exclude_contract(chain, 65.0, "20260717", "call")
    assert "20260717" not in result2["calls"] or "65.0" not in result2["calls"].get("20260717", {})


def test_exclude_contract_none_args():
    """Test that exclude_contract returns chain unchanged when strike or expiration is None."""
    chain = {
        "symbol": "TEST",
        "timestamp": "2026-07-09T08:00:00Z",
        "calls": {
            "20260717": {
                "65.0": {"bid": 0.15, "ask": 0.20, "delta": 0.25},
            },
        },
        "puts": {},
    }
    
    # None strike
    result1 = exclude_contract(chain, None, "2026-07-17", "call")
    assert result1 == chain
    
    # None expiration
    result2 = exclude_contract(chain, 65.0, None, "call")
    assert result2 == chain
    
    # Both None
    result3 = exclude_contract(chain, None, None, "call")
    assert result3 == chain


def test_exclude_contract_puts():
    """Test that exclude_contract works correctly for puts."""
    chain = {
        "symbol": "TEST",
        "timestamp": "2026-07-09T08:00:00Z",
        "calls": {},
        "puts": {
            "20260717": {
                "60.0": {"bid": 0.25, "ask": 0.30, "delta": -0.30},
                "55.0": {"bid": 0.50, "ask": 0.55, "delta": -0.45},
            },
            "20260821": {
                "60.0": {"bid": 0.35, "ask": 0.40, "delta": -0.32},
            },
        },
    }
    
    # Exclude the $60 put expiring 2026-07-17
    result = exclude_contract(chain, 60.0, "2026-07-17", "put")
    
    # The exact match should be removed
    assert "60.0" not in result["puts"]["20260717"]
    
    # Same expiration, different strike should be kept
    assert "55.0" in result["puts"]["20260717"]
    
    # Same strike, different expiration should be kept
    assert "60.0" in result["puts"]["20260821"]


def test_exclude_contract_removes_empty_expiration():
    """Test that exclude_contract removes the expiration key if it becomes empty."""
    chain = {
        "symbol": "TEST",
        "timestamp": "2026-07-09T08:00:00Z",
        "calls": {
            "20260717": {
                "65.0": {"bid": 0.15, "ask": 0.20, "delta": 0.25},
            },
            "20260821": {
                "60.0": {"bid": 0.30, "ask": 0.35, "delta": 0.28},
            },
        },
        "puts": {},
    }
    
    # Exclude the only strike in 20260717
    result = exclude_contract(chain, 65.0, "2026-07-17", "call")
    
    # The 20260717 expiration should be removed entirely
    assert "20260717" not in result["calls"]
    
    # The 20260821 expiration should remain
    assert "20260821" in result["calls"]


def test_exclude_contract_preserves_other_bucket():
    """Test that exclude_contract doesn't affect the opposite bucket (calls vs puts)."""
    chain = {
        "symbol": "TEST",
        "timestamp": "2026-07-09T08:00:00Z",
        "calls": {
            "20260717": {
                "65.0": {"bid": 0.15, "ask": 0.20, "delta": 0.25},
            },
        },
        "puts": {
            "20260717": {
                "60.0": {"bid": 0.25, "ask": 0.30, "delta": -0.30},
            },
        },
    }
    
    # Exclude a call — puts should be unchanged
    result = exclude_contract(chain, 65.0, "2026-07-17", "call")
    
    assert "20260717" not in result["calls"] or "65.0" not in result["calls"].get("20260717", {})
    assert result["puts"] == chain["puts"]


def test_exclude_contract_option_type_variants():
    """Test that exclude_contract handles option_type variants correctly."""
    chain = {
        "symbol": "TEST",
        "timestamp": "2026-07-09T08:00:00Z",
        "calls": {
            "20260717": {
                "65.0": {"bid": 0.15, "ask": 0.20, "delta": 0.25},
            },
        },
        "puts": {
            "20260717": {
                "60.0": {"bid": 0.25, "ask": 0.30, "delta": -0.30},
            },
        },
    }
    
    # Test call variants
    for opt_type in ["call", "covered_call", "open_call", "open_call_monitor"]:
        result = exclude_contract(chain, 65.0, "2026-07-17", opt_type)
        assert "20260717" not in result["calls"] or "65.0" not in result["calls"].get("20260717", {})
    
    # Test put variants
    for opt_type in ["put", "cash_secured_put", "open_put", "open_put_monitor"]:
        result = exclude_contract(chain, 60.0, "2026-07-17", opt_type)
        assert "20260717" not in result["puts"] or "60.0" not in result["puts"].get("20260717", {})


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
