"""
Test suite for POST /api/symbols/{symbol}/positions/{position_id}/dps-insights endpoint (web/app.py:1286).

Hermetic tests: NO network, NO real LLM, NO real Cosmos.
Reuses web-endpoint test pattern from test_activity_chat.py with TestClient + monkeypatch + FakeCosmos.

Pattern:
  1. TestClient(app) for endpoint access
  2. FakeCosmos() for data layer
  3. monkeypatch for late-imported symbols (Agent, Config, create_async_chat_client)
  4. Capture LLM message to assert contract (exact headers + ordering)
"""

import json
import pytest
from starlette.testclient import TestClient


# ===========================================================================
# Test Fixtures & Fakes
# ===========================================================================

class FakeCosmos:
    """In-memory fake for CosmosDBService."""
    
    def __init__(self):
        self.symbols = {}
        self.snapshots = {}
        self.get_position_snapshots_calls = []
        
    def get_symbol(self, symbol):
        return self.symbols.get(symbol)
    
    def get_position_snapshots(self, symbol, position_id, limit=100):
        """Track calls and return fake snapshots."""
        self.get_position_snapshots_calls.append({
            "symbol": symbol,
            "position_id": position_id,
            "limit": limit
        })
        key = (symbol, position_id)
        return self.snapshots.get(key, [])


class FakeAgent:
    """Fake Agent that captures message and returns mock insights."""
    
    captured_messages = []  # Module-level storage for assertions
    
    def __init__(self, client, name, instructions):
        self.client = client
        self.name = name
        self.instructions = instructions
    
    async def run(self, message):
        """Capture message and return mock result."""
        FakeAgent.captured_messages.append(message)
        
        # Return object with .text attribute
        class FakeResult:
            text = "MOCK DPS SUMMARY"
        
        return FakeResult()


class FakeConfig:
    """Fake Config with dps_insights_model and llm_config."""
    
    @property
    def dps_insights_model(self):
        return "gpt-5.4-mini"
    
    @property
    def model_deployment(self):
        return "gpt-5.4-mini"
    
    def llm_config(self):
        return {"endpoint": "fake", "key": "fake"}


def fake_create_async_chat_client(model, config):
    """Fake LLM client factory."""
    return object()  # Agent is mocked, so this is unused


@pytest.fixture
def fake_cosmos():
    """Provide a fresh FakeCosmos for each test."""
    cosmos = FakeCosmos()
    
    # Default symbol with position
    cosmos.symbols["AAPL"] = {
        "symbol": "AAPL",
        "positions": [
            {
                "position_id": "pos_123",
                "strike": 185.0,
                "type": "put",
                "expiration": "2026-07-18",
                "quantity": 1,
                "cost_basis": 2.50
            }
        ]
    }
    
    # Default snapshots (newest first as returned by Cosmos)
    cosmos.snapshots[("AAPL", "pos_123")] = [
        {
            "timestamp": "2026-07-09T10:00:00Z",
            "underlying_price": 188.5,
            "strike": 185.0,
            "gap_percent": 1.9,
            "rsi_14": 62,
            "macd_level": "bullish",
            "adx": 28,
            "midprice": 1.85,
            "pnl_pct": -26.0,
            "dps_score": 72
        },
        {
            "timestamp": "2026-07-08T10:00:00Z",
            "underlying_price": 187.0,
            "strike": 185.0,
            "gap_percent": 1.1,
            "rsi_14": 58,
            "macd_level": "neutral",
            "adx": 25,
            "midprice": 2.10,
            "pnl_pct": -16.0,
            "dps_score": 65
        },
        {
            "timestamp": "2026-07-07T10:00:00Z",
            "underlying_price": 185.5,
            "strike": 185.0,
            "gap_percent": 0.3,
            "rsi_14": 52,
            "macd_level": "neutral",
            "adx": 22,
            "midprice": 2.35,
            "pnl_pct": -6.0,
            "dps_score": 58
        }
    ]
    
    return cosmos


@pytest.fixture
def test_app(fake_cosmos, monkeypatch):
    """Create TestClient with mocked dependencies."""
    # Import app
    from web.app import app
    
    # Clear captured messages
    FakeAgent.captured_messages.clear()
    
    # Monkeypatch late-imported symbols
    monkeypatch.setattr("agent_framework.Agent", FakeAgent)
    monkeypatch.setattr("src.llm.create_async_chat_client", fake_create_async_chat_client)
    monkeypatch.setattr("src.config.Config", FakeConfig)
    
    # Disable startup event (prevent cosmos initialization)
    app.router.on_startup = []
    
    # Set cosmos state before creating client
    app.state.cosmos = fake_cosmos
    app.state.yf_provider = None  # Not needed for this endpoint
    
    # Create client (will skip startup events)
    client = TestClient(app, raise_server_exceptions=False)
    
    return client, fake_cosmos


# ===========================================================================
# Tests
# ===========================================================================

def test_symbol_not_found_returns_404(test_app):
    """Symbol not found in cosmos should return 404."""
    client, _ = test_app
    
    response = client.post(
        "/api/symbols/UNKNOWN/positions/pos_123/dps-insights"
    )
    
    assert response.status_code == 404
    assert "not found" in response.json()["error"].lower()
    assert "UNKNOWN" in response.json()["error"]


def test_position_not_found_returns_404(test_app):
    """Position not found in symbol doc should return 404."""
    client, _ = test_app
    
    response = client.post(
        "/api/symbols/AAPL/positions/unknown_pos/dps-insights"
    )
    
    assert response.status_code == 404
    assert "not found" in response.json()["error"].lower()
    assert "unknown_pos" in response.json()["error"]


def test_happy_path_returns_200_with_insights(test_app):
    """Happy path should return 200 with insights from mocked Agent."""
    client, cosmos = test_app
    
    response = client.post(
        "/api/symbols/AAPL/positions/pos_123/dps-insights"
    )
    
    assert response.status_code == 200
    data = response.json()
    assert "insights" in data
    assert data["insights"] == "MOCK DPS SUMMARY"
    
    # Verify get_position_snapshots was called with correct args
    assert len(cosmos.get_position_snapshots_calls) == 1
    call = cosmos.get_position_snapshots_calls[0]
    assert call["symbol"] == "AAPL"
    assert call["position_id"] == "pos_123"
    assert call["limit"] == 30


def test_contract_exact_headers_present(test_app):
    """The message passed to Agent.run must contain exact headers."""
    client, _ = test_app
    
    response = client.post(
        "/api/symbols/AAPL/positions/pos_123/dps-insights"
    )
    
    assert response.status_code == 200
    
    # Extract captured message
    assert len(FakeAgent.captured_messages) == 1
    message = FakeAgent.captured_messages[0]
    
    # Assert exact headers
    assert "=== POSITION ===" in message
    assert "=== DPS SNAPSHOT HISTORY (oldest first) ===" in message
    
    # Assert trailing instruction line
    assert "Summarize this position's DPS:" in message


def test_contract_position_data_in_message(test_app):
    """The message should contain position JSON."""
    client, _ = test_app
    
    response = client.post(
        "/api/symbols/AAPL/positions/pos_123/dps-insights"
    )
    
    assert response.status_code == 200
    
    message = FakeAgent.captured_messages[0]
    
    # Position fields should be in the message
    assert '"position_id": "pos_123"' in message or "'position_id': 'pos_123'" in message
    assert "185.0" in message  # strike
    assert "put" in message  # type
    assert "2026-07-18" in message  # expiration


def test_contract_snapshots_oldest_first(test_app):
    """Snapshots should be reversed to oldest first in the message."""
    client, cosmos = test_app
    
    # Set snapshots newest first (as returned by Cosmos)
    cosmos.snapshots[("AAPL", "pos_123")] = [
        {"timestamp": "2026-07-09T10:00:00Z", "dps_score": 72},
        {"timestamp": "2026-07-08T10:00:00Z", "dps_score": 65},
        {"timestamp": "2026-07-07T10:00:00Z", "dps_score": 58}
    ]
    
    response = client.post(
        "/api/symbols/AAPL/positions/pos_123/dps-insights"
    )
    
    assert response.status_code == 200
    
    message = FakeAgent.captured_messages[0]
    
    # Find snapshot section
    snapshot_section_start = message.find("=== DPS SNAPSHOT HISTORY (oldest first) ===")
    assert snapshot_section_start != -1, "Snapshot section not found"
    
    snapshot_section = message[snapshot_section_start:]
    
    # Find positions of timestamps in message (should be oldest first)
    pos_07 = snapshot_section.find("2026-07-07")
    pos_08 = snapshot_section.find("2026-07-08")
    pos_09 = snapshot_section.find("2026-07-09")
    
    # All should be found
    assert pos_07 != -1 and pos_08 != -1 and pos_09 != -1, "Not all timestamps found"
    
    # Order should be 07 < 08 < 09 (oldest first)
    assert pos_07 < pos_08 < pos_09, f"Snapshots not in oldest-first order: {pos_07}, {pos_08}, {pos_09}"


def test_empty_snapshots_still_returns_200(test_app):
    """Empty snapshots should still return 200 with insights (LLM called regardless)."""
    client, cosmos = test_app
    
    # Set empty snapshots
    cosmos.snapshots[("AAPL", "pos_123")] = []
    
    response = client.post(
        "/api/symbols/AAPL/positions/pos_123/dps-insights"
    )
    
    assert response.status_code == 200
    data = response.json()
    assert "insights" in data
    assert data["insights"] == "MOCK DPS SUMMARY"
    
    # Verify LLM was still called
    assert len(FakeAgent.captured_messages) == 1


def test_readonly_no_live_fetches(test_app, monkeypatch):
    """Endpoint should NOT call options chain cache or run_dps_analysis."""
    client, _ = test_app
    
    # Monkeypatch to raise if called
    def should_not_be_called(*args, **kwargs):
        raise AssertionError("Live fetch method called during read-only endpoint")
    
    monkeypatch.setattr(
        "src.options_chain_cache.get_options_chain_cache",
        should_not_be_called
    )
    
    # Mock run_dps_analysis at module level
    import src.dps_scorer
    original_run_dps = getattr(src.dps_scorer, "run_dps_analysis", None)
    src.dps_scorer.run_dps_analysis = should_not_be_called
    
    try:
        response = client.post(
            "/api/symbols/AAPL/positions/pos_123/dps-insights"
        )
        
        # Should succeed without calling live fetch methods
        assert response.status_code == 200
    finally:
        # Restore original
        if original_run_dps is not None:
            src.dps_scorer.run_dps_analysis = original_run_dps


def test_cosmos_unavailable_returns_503(test_app):
    """Cosmos unavailable (None) should return 503."""
    client, _ = test_app
    
    # Import app and set cosmos to None
    from web.app import app
    app.state.cosmos = None
    
    response = client.post(
        "/api/symbols/AAPL/positions/pos_123/dps-insights"
    )
    
    assert response.status_code == 503
    assert "error" in response.json()


def test_symbol_case_insensitive(test_app):
    """Symbol should be uppercased (lowercase input works)."""
    client, cosmos = test_app
    
    response = client.post(
        "/api/symbols/aapl/positions/pos_123/dps-insights"
    )
    
    assert response.status_code == 200
    
    # Verify cosmos was queried with uppercase
    call = cosmos.get_position_snapshots_calls[0]
    assert call["symbol"] == "AAPL"


# ===========================================================================
# score_short_put / score_short_call — direct unit tests (Z5-Z9,
# .squad/decisions/inbox/danny-zero-free-agent-option-chains.md, §6.1
# Z-S1 through Z-S5)
#
# These exercise the deterministic scorer functions directly (no HTTP
# layer), independent of the endpoint tests above.
# ===========================================================================

import re

from src.dps_scorer import score_short_put, score_short_call

_SNAPSHOTS = [
    {"timestamp": "2026-01-05 10:00:00", "rsi_14": 45.0, "macd_level": 0.1,
     "adx": 18.0, "gap_percent": -2.0, "underlying_price": 98.0},
    {"timestamp": "2026-01-06 10:00:00", "rsi_14": 42.0, "macd_level": 0.05,
     "adx": 19.0, "gap_percent": -2.5, "underlying_price": 97.5},
    {"timestamp": "2026-01-07 10:00:00", "rsi_14": 38.0, "macd_level": -0.02,
     "adx": 21.0, "gap_percent": -3.0, "underlying_price": 97.0},
]


def _full_greeks(**overrides):
    base = {"delta": 0.28, "gamma": 0.015, "theta": -0.04, "iv": 0.32,
            "mid": 1.6, "ask": 1.6, "bid": 1.5}
    base.update(overrides)
    return base


class TestZS1DeltaNoneNoRiskZoneContamination:
    """Z-S1: delta=None -> risk_zone UNKNOWN, Delta factor 0 points,
    "delta" in data_quality.missing_fields, confidence insufficient,
    status NO_DATA, and never the old delta=0-coerced +13/"SAFE"."""

    def test_put_delta_none(self):
        greeks = _full_greeks()
        del greeks["delta"]
        result = score_short_put(greeks, _SNAPSHOTS, 100.0, "20260601", 97.0, premium_received=2.0)
        assert result["risk_zone"] == "UNKNOWN"
        delta_entry = next(e for e in result["score_breakdown"] if e["factor"] == "Delta")
        assert delta_entry["points"] == 0
        assert delta_entry["points"] != 13
        assert "delta" in result["data_quality"]["missing_fields"]
        assert result["data_quality"]["confidence"] == "insufficient"
        assert result["status"] == "NO_DATA"

    def test_call_delta_none(self):
        greeks = _full_greeks()
        del greeks["delta"]
        result = score_short_call(greeks, _SNAPSHOTS, 100.0, "20260601", 103.0, premium_received=2.0)
        assert result["risk_zone"] == "UNKNOWN"
        delta_entry = next(e for e in result["score_breakdown"] if e["factor"] == "Delta")
        assert delta_entry["points"] == 0
        assert "delta" in result["data_quality"]["missing_fields"]
        assert result["data_quality"]["confidence"] == "insufficient"
        assert result["status"] == "NO_DATA"


class TestZS2IVNoneNoPointsEitherDirection:
    """Z-S2: iv=None -> no IV points (neither +7 nor -7)."""

    def test_put_iv_none(self):
        greeks = _full_greeks()
        del greeks["iv"]
        result = score_short_put(greeks, _SNAPSHOTS, 100.0, "20260601", 97.0, premium_received=2.0)
        iv_entry = next(e for e in result["score_breakdown"] if e["factor"] == "IV level")
        assert iv_entry["points"] == 0
        assert "unavailable" in iv_entry["reason"].lower()
        assert "iv" in result["data_quality"]["missing_fields"]

    def test_call_iv_none(self):
        greeks = _full_greeks()
        del greeks["iv"]
        result = score_short_call(greeks, _SNAPSHOTS, 100.0, "20260601", 103.0, premium_received=2.0)
        iv_entry = next(e for e in result["score_breakdown"] if e["factor"] == "IV level")
        assert iv_entry["points"] == 0
        assert "unavailable" in iv_entry["reason"].lower()


class TestZS3GammaThetaNoneNoPenaltyNoFabricatedString:
    """Z-S3: gamma/theta None -> no penalty, and never a "Γ 0.000" /
    "Θ 0.0000" fabricated-zero string in any breakdown reason."""

    def test_put_gamma_theta_none(self):
        greeks = _full_greeks()
        del greeks["gamma"]
        del greeks["theta"]
        result = score_short_put(greeks, _SNAPSHOTS, 100.0, "20260601", 97.0, premium_received=2.0)
        gamma_entry = next(e for e in result["score_breakdown"] if e["factor"] == "Gamma")
        theta_entry = next(e for e in result["score_breakdown"] if e["factor"] == "Theta")
        assert gamma_entry["points"] == 0
        assert theta_entry["points"] == 0
        assert "unavailable" in gamma_entry["reason"].lower()
        assert "unavailable" in theta_entry["reason"].lower()
        all_reasons = " ".join(e["reason"] for e in result["score_breakdown"])
        assert not re.search(r"Γ\s*0\.000", all_reasons)
        assert not re.search(r"Θ\s*0\.0000", all_reasons)
        assert "gamma" in result["data_quality"]["missing_fields"]
        assert "theta" in result["data_quality"]["missing_fields"]

    def test_call_gamma_theta_none(self):
        greeks = _full_greeks()
        del greeks["gamma"]
        del greeks["theta"]
        result = score_short_call(greeks, _SNAPSHOTS, 100.0, "20260601", 103.0, premium_received=2.0)
        gamma_entry = next(e for e in result["score_breakdown"] if e["factor"] == "Gamma")
        theta_entry = next(e for e in result["score_breakdown"] if e["factor"] == "Theta")
        assert gamma_entry["points"] == 0
        assert theta_entry["points"] == 0
        all_reasons = " ".join(e["reason"] for e in result["score_breakdown"])
        assert not re.search(r"Γ\s*0\.000", all_reasons)
        assert not re.search(r"Θ\s*0\.0000", all_reasons)


class TestZS4PutPnlUsesExecutableBuybackAsk:
    """Z-S4: put P&L uses executable_buyback_ask; ask unusable ->
    pnl_pct None even when mid is present and positive (Z7)."""

    def test_zero_ask_positive_mid_yields_no_pnl_points(self):
        greeks = _full_greeks(mid=1.5, ask=0.0)
        result = score_short_put(greeks, _SNAPSHOTS, 100.0, "20260601", 97.0, premium_received=2.0)
        pnl_entry = next(e for e in result["score_breakdown"] if e["factor"] == "P&L")
        assert pnl_entry["points"] == 0
        assert pnl_entry["reason"] == "P&L unavailable (no positive finite executable ask or premium data)"
        assert result["inputs"]["buyback_ask"] is None
        assert result["inputs"]["buyback_available"] is False
        assert result["inputs"]["incomplete_data"] is True

    def test_usable_ask_yields_pnl_points(self):
        greeks = _full_greeks(mid=1.5, ask=1.6)
        result = score_short_put(greeks, _SNAPSHOTS, 100.0, "20260601", 97.0, premium_received=2.0)
        pnl_entry = next(e for e in result["score_breakdown"] if e["factor"] == "P&L")
        assert pnl_entry["reason"] != "P&L unavailable (no positive finite executable ask or premium data)"
        assert result["inputs"]["buyback_ask"] == 1.6


class TestZS5HappyPathGoldenRegression:
    """Z-S5: all-available happy path -> identical score and breakdown to
    the pre-Z1-Z10 baseline (git HEAD, commit c7313e9) for a fixture where
    `mid` and `executable_buyback_ask(ask)` coincide (isolating the golden
    check from the *intentional* Z7 put P&L divergence, which is exercised
    separately in TestZS4)."""

    def test_put_golden_score_and_breakdown(self):
        result = score_short_put(_full_greeks(), _SNAPSHOTS, 100.0, "20260601", 97.0, premium_received=2.0)
        assert result["score"] == 47
        assert result["status"] == "ROLL"
        assert result["risk_zone"] == "SAFE"
        assert result["data_quality"]["confidence"] == "full"
        assert result["data_quality"]["missing_fields"] == []
        points_by_factor = {e["factor"]: e["points"] for e in result["score_breakdown"]}
        assert points_by_factor == {
            "Base": 50, "Delta": 13, "GAP": 0, "RSI level": 4, "RSI trend": -10,
            "MACD trend": -13, "ADX": 4, "DTE": 8, "Gamma": 0, "Theta": 0,
            "IV level": 0, "P&L": 0, "Combo: P&L+DTE": 0, "Combo: IV+DTE": 0,
            "Combo: MACD+RSI": -9, "Combo: Delta+ADX": 0,
        }

    def test_call_golden_score_and_breakdown(self):
        greeks = {"delta": 0.22, "gamma": 0.012, "theta": -0.035, "iv": 0.29,
                  "mid": 1.35, "ask": 1.4, "bid": 1.3}
        result = score_short_call(greeks, _SNAPSHOTS, 100.0, "20260601", 103.0, premium_received=2.0)
        assert result["score"] == 100
        assert result["status"] == "HOLD"
        assert result["data_quality"]["confidence"] == "full"
        assert result["data_quality"]["missing_fields"] == []
