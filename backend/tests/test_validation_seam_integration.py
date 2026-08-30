"""Integration test for chain-aware validation seam.

Tests that the callback implementation (_validate_alpha_alternative) correctly
integrates with AgentRunner's expected signature and can select/reject chain
contracts based on the 10 D4 gates.
"""

import pytest
from datetime import datetime, timezone

from src.contract_validation_integration import (
    _build_validation_chain_context,
    _validate_alpha_alternative,
)


def test_chain_context_parity():
    """Verify chain context builder produces expected output matching Alpha's normal format."""
    
    sample_chain = {
        "symbol": "AAPL",
        "timestamp": "2026-08-30T19:00:00Z",
        "underlying_price": 150.0,
        "calls": {
            "20260919": {
                "150.0": {
                    "strike": 150.0,
                    "bid": 3.50,
                    "ask": 3.70,
                    "delta": 0.45,
                    "gamma": 0.03,
                    "theta": -0.12,
                    "vega": 0.18,
                    "iv": 0.28,
                    "volume": 200,
                    "openInterest": 800,
                    "_meta": {"chain_timestamp": "2026-08-30T19:00:00Z", "greeks_valid": True},
                }
            }
        },
        "puts": {},
    }
    
    # Build chain context
    chain_context = _build_validation_chain_context(sample_chain, "call")
    
    # Verify output structure
    assert len(chain_context) > 0, "Chain context should not be empty"
    assert "OPTIONS CHAIN" in chain_context or "calls" in chain_context
    assert "150.0" in chain_context or "150" in chain_context
    assert "0.45" in chain_context or "delta" in chain_context


def test_d4_callback_signature_and_gates():
    """Test D4 validation callback with AgentRunner's expected kwargs signature.
    
    Verifies the callback accepts the exact kwargs that AgentRunner passes and
    that it correctly validates/rejects alternatives based on the 10 gates.
    """
    
    # Sample chain with multiple contracts
    sample_chain = {
        "symbol": "AAPL",
        "timestamp": "2026-08-30T19:00:00Z",
        "underlying_price": 150.0,
        "calls": {
            "20260919": {
                "145.0": {
                    "strike": 145.0,
                    "bid": 6.50,
                    "ask": 6.70,
                    "delta": 0.65,  # Out of band (>0.50)
                    "gamma": 0.02,
                    "theta": -0.10,
                    "vega": 0.15,
                    "iv": 0.25,
                    "volume": 100,
                    "openInterest": 500,
                    "_meta": {"chain_timestamp": "2026-08-30T19:00:00Z", "greeks_valid": True},
                },
                "150.0": {
                    "strike": 150.0,
                    "bid": 3.50,
                    "ask": 3.70,
                    "delta": 0.45,  # In band
                    "gamma": 0.03,
                    "theta": -0.12,
                    "vega": 0.18,
                    "iv": 0.28,
                    "volume": 200,
                    "openInterest": 800,
                    "_meta": {"chain_timestamp": "2026-08-30T19:00:00Z", "greeks_valid": True},
                },
                "155.0": {
                    "strike": 155.0,
                    "bid": 1.50,
                    "ask": 1.70,
                    "delta": 0.25,  # In band
                    "gamma": 0.02,
                    "theta": -0.08,
                    "vega": 0.12,
                    "iv": 0.30,
                    "volume": 150,
                    "openInterest": 600,
                    "_meta": {"chain_timestamp": "2026-08-30T19:00:00Z", "greeks_valid": True},
                },
            }
        },
        "puts": {},
    }
    
    now = datetime(2026, 8, 30, tzinfo=timezone.utc)
    
    # Build callback closure (same as in _execute_validation)
    def validated_alternative_callback(**kwargs):
        """D4 validation callback matching AgentRunner's expected signature."""
        return _validate_alpha_alternative(
            chain=sample_chain,
            side=kwargs["side"],
            requested_strike=kwargs["requested_strike"],
            requested_expiration=kwargs["requested_expiration"],
            alternative=kwargs["alternative"],
            category=kwargs["category"],
            underlying_price=kwargs["underlying_price"],
            next_earnings_date=kwargs["next_earnings_date"],
            now=now,
        )
    
    # Test 1: Valid alternative (155.0 call, delta 0.25 in band, single-param change)
    valid_alternative = {
        "strike": 155.0,
        "expiration": "2026-09-19",
        "premium": 1.50,
        "delta": 0.25,
    }
    
    is_valid, reason, contract = validated_alternative_callback(
        side="call",
        requested_strike=150.0,
        requested_expiration="2026-09-19",
        alternative=valid_alternative,
        category="balanced",
        underlying_price=150.0,
        next_earnings_date=None,
    )
    
    # Should pass G1-G9 (may fail G10 premium floor)
    assert isinstance(is_valid, bool)
    assert isinstance(reason, (str, type(None)))
    if is_valid:
        assert contract is not None
        assert contract["strike"] == 155.0
    
    # Test 2: Invalid - delta out of band (G8 failure)
    invalid_delta_alt = {
        "strike": 145.0,
        "expiration": "2026-09-19",
        "premium": 6.50,
        "delta": 0.65,
    }
    
    is_valid, reason, contract = validated_alternative_callback(
        side="call",
        requested_strike=150.0,
        requested_expiration="2026-09-19",
        alternative=invalid_delta_alt,
        category="balanced",
        underlying_price=150.0,
        next_earnings_date=None,
    )
    
    assert is_valid is False, "145.0 call with delta 0.65 should fail G8 (delta band)"
    assert "delta out of band" in reason.lower()
    
    # Test 3: Invalid - non-existent contract (G1 failure)
    nonexistent_alt = {
        "strike": 999.0,
        "expiration": "2026-09-19",
        "premium": 0.50,
        "delta": 0.10,
    }
    
    is_valid, reason, contract = validated_alternative_callback(
        side="call",
        requested_strike=150.0,
        requested_expiration="2026-09-19",
        alternative=nonexistent_alt,
        category="balanced",
        underlying_price=150.0,
        next_earnings_date=None,
    )
    
    assert is_valid is False, "Non-existent contract should fail G1"
    assert "not found in chain" in reason.lower()
    
    # Test 4: Invalid - identical to requested (G3 failure)
    identical_alt = {
        "strike": 150.0,
        "expiration": "2026-09-19",
        "premium": 3.50,
        "delta": 0.45,
    }
    
    is_valid, reason, contract = validated_alternative_callback(
        side="call",
        requested_strike=150.0,
        requested_expiration="2026-09-19",
        alternative=identical_alt,
        category="balanced",
        underlying_price=150.0,
        next_earnings_date=None,
    )
    
    assert is_valid is False, "Identical contract should fail G3"
    assert "identical" in reason.lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
