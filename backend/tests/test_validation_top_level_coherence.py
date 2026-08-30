"""Regression tests for contract validation top-level field coherence.

Tests ensure that when selected_contract differs from requested_contract
(Cases B and C), all top-level result fields reflect the selected contract.

Also tests that incomplete Alpha alternatives are handled correctly:
- Primary SELL + incomplete alternative => SELL with requested contract
- Primary WAIT + incomplete alternative => WAIT
"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.agent_runner import AgentRunner
from src.llm import LlmConfig


@pytest.fixture
def mock_llm():
    """Mock LLM config."""
    return LlmConfig(provider="azure", api_key="mock-key", endpoint="https://mock.endpoint")


@pytest.fixture
def mock_cosmos():
    """Mock CosmosDBService."""
    cosmos = MagicMock()
    cosmos.log_agent_run = AsyncMock()
    cosmos.log_agent_trace = AsyncMock()
    return cosmos


@pytest.fixture
def mock_context_provider():
    """Mock ContextProvider."""
    provider = MagicMock()
    provider.get_context.return_value = ""
    return provider


@pytest.fixture
def evidence_snapshot():
    """Standard evidence snapshot for validation."""
    return {
        "category": "high_iv",
        "underlying_price": 100.0,
        "total_shares": 100,
        "contract_data": {
            "bid": 2.50,
            "ask": 2.60,
            "delta": 0.30,
            "iv": 0.35,
        },
        "market_data_text": "Mock market data",
        "chain_timestamp": "2024-01-15T10:00:00Z",
        "next_earnings_date": None,
    }


def make_mock_response(json_obj):
    """Helper to create a mock agent response with JSON."""
    mock_resp = MagicMock()
    mock_resp.text = f"```json\n{json.dumps(json_obj)}\n```"
    return mock_resp


def make_complete_supervisor():
    """Create a complete Supervisor ORIGINAL_HOLDS response."""
    return {
        "decision": "ORIGINAL_HOLDS",
        "rationale": "Approve requested",
        "challenge_strength": "WEAK",
        "counter_arguments": [],
        "net_assessment": "ORIGINAL_HOLDS",
    }


@pytest.mark.asyncio
async def test_case_b_alternative_updates_top_level_fields(mock_llm, mock_cosmos, mock_context_provider, evidence_snapshot):
    """Test Case B: when Alpha alternative is accepted, top-level strike/expiration/premium/delta are updated."""
    runner = AgentRunner(llm=mock_llm, model="gpt-4")

    primary_result = {"activity": "SELL", "timestamp": "2024-01-15T10:00:00Z", "rationale": "Good premium"}
    supervisor_result = make_complete_supervisor()
    alpha_result = {
        "opportunity_strength": "STRONG",
        "alternative": {"strike": 105.0, "expiration": "2024-02-16", "premium": 3.00, "delta": 0.35, "rationale": "Better premium at 105 strike"},
        "relaxed_parameter": "strike",
    }

    # D4 returns DIFFERENT values from Alpha to prove chain data wins
    def d4_callback(**kwargs):
        return (True, None, {"strike": 105.0, "expiration": "2024-02-16", "bid": 3.10, "delta": 0.36})

    mock_agent_instance = MagicMock()
    mock_agent_instance.run = AsyncMock(side_effect=[make_mock_response(primary_result), make_mock_response(supervisor_result), make_mock_response(alpha_result)])

    with patch("src.agent_runner.Agent", return_value=mock_agent_instance):
        result = await runner.run_contract_validation(symbol="AAPL", side="call", strike=100.0, expiration="2024-01-19", evidence_snapshot=evidence_snapshot, cosmos=mock_cosmos, context_provider=mock_context_provider, validated_alternative_callback=d4_callback)

    assert result["activity"] == "SELL"
    assert result["is_alert"] is True
    assert result["validation_status"] == "approved"
    assert result["selection_source"] == "alpha_alternative"

    # TOP-LEVEL fields must reflect SELECTED contract with D4-normalized values
    assert result["strike"] == 105.0, "Top-level strike must match selected_contract"
    assert result["expiration"] == "2024-02-16", "Top-level expiration must match selected_contract"
    assert result["premium"] == 3.10, "Top-level premium must match D4 bid (3.10), not Alpha (3.00)"
    assert result["delta"] == 0.36, "Top-level delta must match D4 delta (0.36), not Alpha (0.35)"

    # requested_contract remains unchanged
    assert result["requested_contract"]["strike"] == 100.0
    assert result["requested_contract"]["expiration"] == "2024-01-19"

    # selected_contract reflects the D4-normalized alternative
    assert result["selected_contract"]["strike"] == 105.0
    assert result["selected_contract"]["expiration"] == "2024-02-16"
    assert result["selected_contract"]["premium"] == 3.10  # From D4 bid
    assert result["selected_contract"]["delta"] == 0.36  # From D4 delta

    # activity_data must also reflect selected contract for persistence
    assert result["activity_data"]["strike"] == 105.0
    assert result["activity_data"]["expiration"] == "2024-02-16"
    assert result["activity_data"]["premium"] == 3.10
    assert result["activity_data"]["delta"] == 0.36


@pytest.mark.asyncio
async def test_case_c_alternative_updates_top_level_fields(mock_llm, mock_cosmos, mock_context_provider, evidence_snapshot):
    """Test Case C: when Alpha rescues WAIT with alternative, top-level strike/expiration/premium/delta are updated."""
    runner = AgentRunner(llm=mock_llm, model="gpt-4")

    primary_result = {"activity": "WAIT", "timestamp": "2024-01-15T10:00:00Z", "rationale": "Premium too low"}
    supervisor_result = make_complete_supervisor()
    alpha_result = {
        "opportunity_strength": "STRONG",
        "alternative": {"strike": 95.0, "expiration": "2024-02-16", "premium": 4.00, "delta": 0.40, "rationale": "Better premium at 95 strike"},
        "relaxed_parameter": "strike",
    }

    # D4 returns DIFFERENT values from Alpha to prove chain data wins
    def d4_callback(**kwargs):
        return (True, None, {"strike": 95.0, "expiration": "2024-02-16", "bid": 4.10, "delta": 0.41})

    mock_agent_instance = MagicMock()
    mock_agent_instance.run = AsyncMock(side_effect=[make_mock_response(primary_result), make_mock_response(supervisor_result), make_mock_response(alpha_result)])

    with patch("src.agent_runner.Agent", return_value=mock_agent_instance):
        result = await runner.run_contract_validation(symbol="AAPL", side="call", strike=100.0, expiration="2024-01-19", evidence_snapshot=evidence_snapshot, cosmos=mock_cosmos, context_provider=mock_context_provider, validated_alternative_callback=d4_callback)

    assert result["activity"] == "SELL"
    assert result["is_alert"] is True
    assert result["validation_status"] == "approved"
    assert result["selection_source"] == "alpha_alternative"

    # TOP-LEVEL fields must reflect SELECTED contract with D4-normalized values
    assert result["strike"] == 95.0, "Top-level strike must match selected_contract"
    assert result["expiration"] == "2024-02-16", "Top-level expiration must match selected_contract"
    assert result["premium"] == 4.10, "Top-level premium must match D4 bid (4.10), not Alpha (4.00)"
    assert result["delta"] == 0.41, "Top-level delta must match D4 delta (0.41), not Alpha (0.40)"

    # requested_contract remains unchanged
    assert result["requested_contract"]["strike"] == 100.0
    assert result["requested_contract"]["expiration"] == "2024-01-19"

    # selected_contract reflects the D4-normalized alternative
    assert result["selected_contract"]["strike"] == 95.0
    assert result["selected_contract"]["expiration"] == "2024-02-16"
    assert result["selected_contract"]["premium"] == 4.10  # From D4 bid
    assert result["selected_contract"]["delta"] == 0.41  # From D4 delta

    # activity_data must also reflect selected contract for persistence
    assert result["activity_data"]["strike"] == 95.0
    assert result["activity_data"]["expiration"] == "2024-02-16"
    assert result["activity_data"]["premium"] == 4.10
    assert result["activity_data"]["delta"] == 0.41


@pytest.mark.asyncio
async def test_primary_sell_incomplete_alternative_sells_requested(mock_llm, mock_cosmos, mock_context_provider, evidence_snapshot):
    """Test: Primary SELL + Alpha STRONG with incomplete alternative => SELL requested contract."""
    runner = AgentRunner(llm=mock_llm, model="gpt-4")

    primary_result = {"activity": "SELL", "timestamp": "2024-01-15T10:00:00Z", "rationale": "Good premium"}
    supervisor_result = make_complete_supervisor()
    alpha_result = {
        "opportunity_strength": "STRONG",
        "alternative": {"strike": 105.0, "expiration": None, "premium": 3.00, "delta": 0.35, "rationale": "Better premium but incomplete"},
        "relaxed_parameter": "strike",
    }

    mock_agent_instance = MagicMock()
    mock_agent_instance.run = AsyncMock(side_effect=[make_mock_response(primary_result), make_mock_response(supervisor_result), make_mock_response(alpha_result)])

    with patch("src.agent_runner.Agent", return_value=mock_agent_instance):
        result = await runner.run_contract_validation(symbol="AAPL", side="call", strike=100.0, expiration="2024-01-19", evidence_snapshot=evidence_snapshot, cosmos=mock_cosmos, context_provider=mock_context_provider)

    assert result["activity"] == "SELL"
    assert result["is_alert"] is True
    assert result["validation_status"] == "approved"
    assert result["selection_source"] == "requested_approved"
    assert result["strike"] == 100.0
    assert result["expiration"] == "2024-01-19"
    assert result["requested_contract"]["strike"] == 100.0
    assert result["selected_contract"]["strike"] == 100.0
    assert "incomplete_alternative" in result["note"]


@pytest.mark.asyncio
async def test_primary_wait_incomplete_alternative_waits(mock_llm, mock_cosmos, mock_context_provider, evidence_snapshot):
    """Test: Primary WAIT + Alpha STRONG with incomplete alternative => WAIT."""
    runner = AgentRunner(llm=mock_llm, model="gpt-4")

    primary_result = {"activity": "WAIT", "timestamp": "2024-01-15T10:00:00Z", "rationale": "Premium too low"}
    supervisor_result = make_complete_supervisor()
    alpha_result = {
        "opportunity_strength": "STRONG",
        "alternative": {"strike": None, "expiration": "2024-02-16", "premium": 4.00, "delta": 0.40, "rationale": "Better premium but incomplete"},
        "relaxed_parameter": "expiration",
    }

    mock_agent_instance = MagicMock()
    mock_agent_instance.run = AsyncMock(side_effect=[make_mock_response(primary_result), make_mock_response(supervisor_result), make_mock_response(alpha_result)])

    with patch("src.agent_runner.Agent", return_value=mock_agent_instance):
        result = await runner.run_contract_validation(symbol="AAPL", side="call", strike=100.0, expiration="2024-01-19", evidence_snapshot=evidence_snapshot, cosmos=mock_cosmos, context_provider=mock_context_provider)

    assert result["activity"] == "WAIT"
    assert result["is_alert"] is False
    assert result["validation_status"] == "approved"
    assert result["selection_source"] is None
    assert result["strike"] == 100.0
    assert result["expiration"] == "2024-01-19"
    assert "incomplete alternative" in result["note"]
