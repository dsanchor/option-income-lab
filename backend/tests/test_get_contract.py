"""Unit tests for get_contract lookup helper in options_chain_filters.py"""

import pytest
from src.options_chain_filters import get_contract


def test_get_contract_exact_match():
    """Test that get_contract returns the contract dict for an exact match."""
    chain = {
        "symbol": "TEST",
        "timestamp": "2026-07-09T08:00:00Z",
        "calls": {
            "20260717": {
                "65.0": {"bid": 0.15, "ask": 0.20, "delta": 0.25, "last": 0.18},
                "60.0": {"bid": 0.50, "ask": 0.55, "delta": 0.35},
            },
            "20260821": {
                "65.0": {"bid": 0.30, "ask": 0.35, "delta": 0.28},
            },
        },
        "puts": {},
    }
    
    # Find the $65 call expiring 2026-07-17
    result = get_contract(chain, 65.0, "2026-07-17", "call")
    
    assert result is not None
    assert result["bid"] == 0.15
    assert result["ask"] == 0.20
    assert result["delta"] == 0.25
    assert result["last"] == 0.18


def test_get_contract_strike_key_variants():
    """Test that get_contract matches strike by float value, handling different formats."""
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
    
    # Strike provided as 65 should match "65.00"
    result = get_contract(chain, 65, "2026-07-17", "call")
    
    assert result is not None
    assert result["bid"] == 0.15
    assert result["ask"] == 0.20
    
    # Strike provided as 60.0 should match "60.0"
    result2 = get_contract(chain, 60.0, "2026-07-17", "call")
    assert result2 is not None
    assert result2["bid"] == 0.50


def test_get_contract_expiration_format_variants():
    """Test that get_contract handles both YYYY-MM-DD and YYYYMMDD expiration formats."""
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
    result1 = get_contract(chain, 65.0, "2026-07-17", "call")
    assert result1 is not None
    assert result1["bid"] == 0.15
    
    # Test with compact format
    result2 = get_contract(chain, 65.0, "20260717", "call")
    assert result2 is not None
    assert result2["bid"] == 0.15


def test_get_contract_none_args():
    """Test that get_contract returns None when strike or expiration is None."""
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
    result1 = get_contract(chain, None, "2026-07-17", "call")
    assert result1 is None
    
    # None expiration
    result2 = get_contract(chain, 65.0, None, "call")
    assert result2 is None
    
    # Both None
    result3 = get_contract(chain, None, None, "call")
    assert result3 is None


def test_get_contract_missing_contract():
    """Test that get_contract returns None when the contract is not found."""
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
    
    # Missing strike
    result1 = get_contract(chain, 70.0, "2026-07-17", "call")
    assert result1 is None
    
    # Missing expiration
    result2 = get_contract(chain, 65.0, "2026-08-21", "call")
    assert result2 is None


def test_get_contract_wrong_bucket():
    """Test that get_contract returns None when searching the wrong bucket."""
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
    
    # Looking for a call in the puts bucket
    result = get_contract(chain, 60.0, "2026-07-17", "call")
    assert result is None


def test_get_contract_puts():
    """Test that get_contract works correctly for puts."""
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
    
    # Find the $60 put expiring 2026-07-17
    result = get_contract(chain, 60.0, "2026-07-17", "put")
    
    assert result is not None
    assert result["bid"] == 0.25
    assert result["ask"] == 0.30
    assert result["delta"] == -0.30


def test_get_contract_option_type_variants():
    """Test that get_contract handles option_type variants correctly."""
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
        result = get_contract(chain, 65.0, "2026-07-17", opt_type)
        assert result is not None
        assert result["bid"] == 0.15
    
    # Test put variants
    for opt_type in ["put", "cash_secured_put", "open_put", "open_put_monitor"]:
        result = get_contract(chain, 60.0, "2026-07-17", opt_type)
        assert result is not None
        assert result["bid"] == 0.25
    
    # Test unknown type
    result = get_contract(chain, 65.0, "2026-07-17", "unknown_type")
    assert result is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
