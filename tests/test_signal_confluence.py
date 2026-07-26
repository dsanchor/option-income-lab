"""Unit tests for the canonical directional-signal confluence in the DPS scorer.

Pure, deterministic — no network, no LLM, no Cosmos. Verifies that the shared
``build_signal`` reading feeds the short-put/short-call scorers coherently.
"""

from src.dps_scorer import _signal_confluence
from src.price_forecast import build_signal


def test_confluence_none_without_reading():
    assert _signal_confluence("put", None) is None
    assert _signal_confluence("put", {}) is None
    assert _signal_confluence("call", {"reading": {}}) is None


def test_confluence_put_favorable_positive():
    sig = {"reading": {"label": "Bullish", "csp": "favorable", "cc": "avoid"}}
    entry = _signal_confluence("put", sig)
    assert entry["points"] == 6
    assert "CSP" in entry["reason"]


def test_confluence_call_avoid_negative():
    sig = {"reading": {"label": "Bullish", "csp": "favorable", "cc": "avoid"}}
    entry = _signal_confluence("call", sig)
    assert entry["points"] == -8
    assert "CC" in entry["reason"]


def test_confluence_caution_small_negative():
    sig = {"reading": {"label": "Topping", "csp": "caution", "cc": "favorable"}}
    assert _signal_confluence("put", sig)["points"] == -4
    assert _signal_confluence("call", sig)["points"] == 6


def test_confluence_consumes_build_signal_output():
    # End-to-end: the canonical builder output plugs straight into the scorer.
    closes = [100.0 + 0.5 * i for i in range(30)]
    tech = {"summary": {"recommendation": {"value": 0.4}}}
    sig = build_signal(tech, closes)  # bullish uptrend
    put_entry = _signal_confluence("put", sig)
    call_entry = _signal_confluence("call", sig)
    assert put_entry["points"] > 0    # bullish favours selling puts
    assert call_entry["points"] < 0   # bullish discourages selling calls
