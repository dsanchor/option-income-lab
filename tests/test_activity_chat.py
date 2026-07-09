"""
Test suite for POST /api/activities/{activity_id}/chat endpoint (web/app.py:2817).

Hermetic tests: NO network, NO real LLM, NO real Cosmos.
Establishes web-endpoint test pattern with TestClient + monkeypatch + FakeCosmos.

Pattern:
  1. TestClient(app) for endpoint access
  2. FakeCosmos() for data layer
  3. monkeypatch for late-imported symbols (Agent, Config, get_options_chain_cache)
  4. Capture LLM message to assert contract (all 5 section headers present)
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
        self.activities = {}
        self.symbols = {}
        self.technical_docs = {}
        
    def get_activity_by_id(self, activity_id):
        return self.activities.get(activity_id)
    
    def get_symbol(self, symbol):
        return self.symbols.get(symbol)
    
    @property
    def container(self):
        """Returns a fake container with query_items method."""
        return FakeContainer(self.technical_docs)


class FakeContainer:
    """Fake container for query_items calls."""
    
    def __init__(self, technical_docs):
        self.technical_docs = technical_docs
    
    def query_items(self, query, parameters, partition_key):
        """Return technical_analysis docs if they exist."""
        symbol = partition_key
        if symbol in self.technical_docs:
            return [self.technical_docs[symbol]]
        return []


class FakeOptionsChainCache:
    """Fake options chain cache."""
    
    def __init__(self, chain_data=None, should_raise=False):
        self.chain_data = chain_data or self._default_chain()
        self.should_raise = should_raise
    
    def get_or_load(self, symbol):
        if self.should_raise:
            raise RuntimeError("Chain unavailable")
        return json.dumps(self.chain_data)
    
    @staticmethod
    def _default_chain():
        """Small valid chain structure."""
        return {
            "calls": {
                "20260717": {
                    "65.0": {"bid": 1.80, "ask": 1.90, "delta": 0.45},
                    "67.5": {"bid": 1.20, "ask": 1.30, "delta": 0.30},
                    "70.0": {"bid": 0.70, "ask": 0.80, "delta": 0.15}
                }
            },
            "puts": {
                "20260717": {
                    "60.0": {"bid": 0.90, "ask": 1.00, "delta": -0.20}
                }
            }
        }


class FakeAgent:
    """Fake Agent that captures message and returns mock answer."""
    
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
            text = "MOCK ANSWER"
        
        return FakeResult()


class FakeConfig:
    """Fake Config with activity_chat_model and llm_config."""
    
    @property
    def activity_chat_model(self):
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
    
    # Default activity
    cosmos.activities["act_123"] = {
        "id": "act_123",
        "symbol": "AAPL",
        "position_id": "pos_456",
        "agent_type": "monitor",
        "decision": "WAIT",
        "reason": "Holding for more premium decay",
        "timestamp": "2026-07-09T10:00:00Z"
    }
    
    # Default symbol with position
    cosmos.symbols["AAPL"] = {
        "symbol": "AAPL",
        "positions": [
            {
                "position_id": "pos_456",
                "strike": 185.0,
                "type": "call",
                "expiration": "2026-07-18",
                "quantity": 1,
                "cost_basis": 2.50
            }
        ]
    }
    
    # Default technical analysis doc
    cosmos.technical_docs["AAPL"] = {
        "doc_type": "technical_analysis",
        "symbol": "AAPL",
        "analysis": "RSI: 65 (NEUTRAL). SMA(10): 183.50 (BUY). Support: 180, Resistance: 190.",
        "timestamp": "2026-07-09T09:00:00Z"
    }
    
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
    
    # Monkeypatch options chain cache
    fake_cache = FakeOptionsChainCache()
    monkeypatch.setattr(
        "src.options_chain_cache.get_options_chain_cache",
        lambda: fake_cache
    )
    
    # Disable startup event (prevent cosmos initialization)
    app.router.on_startup = []
    
    # Set cosmos state before creating client
    app.state.cosmos = fake_cosmos
    app.state.yf_provider = None  # Not needed for chat endpoint
    
    # Create client (will skip startup events)
    client = TestClient(app, raise_server_exceptions=False)
    
    return client, fake_cosmos, fake_cache


# ===========================================================================
# Tests
# ===========================================================================

def test_empty_message_returns_400(test_app):
    """Empty message should return 400."""
    client, _, _ = test_app
    
    response = client.post(
        "/api/activities/act_123/chat",
        json={"message": ""}
    )
    
    assert response.status_code == 400
    assert "empty" in response.json()["error"].lower()


def test_blank_message_returns_400(test_app):
    """Blank (whitespace-only) message should return 400."""
    client, _, _ = test_app
    
    response = client.post(
        "/api/activities/act_123/chat",
        json={"message": "   \n  "}
    )
    
    assert response.status_code == 400
    assert "empty" in response.json()["error"].lower()


def test_unknown_activity_returns_404(test_app):
    """Unknown activity ID should return 404."""
    client, _, _ = test_app
    
    response = client.post(
        "/api/activities/unknown_id/chat",
        json={"message": "Why did this happen?"}
    )
    
    assert response.status_code == 404
    assert "not found" in response.json()["error"].lower()


def test_happy_path_returns_200_with_answer(test_app):
    """Happy path should return 200 with answer from mocked Agent."""
    client, _, _ = test_app
    
    response = client.post(
        "/api/activities/act_123/chat",
        json={"message": "Why did the agent choose WAIT?"}
    )
    
    assert response.status_code == 200
    data = response.json()
    assert "answer" in data
    assert data["answer"] == "MOCK ANSWER"


def test_contract_all_sections_present(test_app):
    """The message passed to Agent.run must contain all 5 section headers."""
    client, _, _ = test_app
    
    response = client.post(
        "/api/activities/act_123/chat",
        json={
            "message": "What should I do now?",
            "history": []
        }
    )
    
    assert response.status_code == 200
    
    # Extract captured message
    assert len(FakeAgent.captured_messages) == 1
    message = FakeAgent.captured_messages[0]
    
    # Assert all 5 exact section headers
    required_headers = [
        "=== AGENT DECISION (historical, exact — what the agents actually decided) ===",
        "=== POSITION ===",
        "=== CURRENT MARKET DATA (LIVE NOW — NOT what the agents used) ===",
        "=== CONVERSATION SO FAR ===",
        "=== USER QUESTION ==="
    ]
    
    for header in required_headers:
        assert header in message, f"Missing header: {header}"
    
    # Assert user question text is present
    assert "What should I do now?" in message
    
    # Assert activity JSON is present (check for activity_id)
    assert '"id": "act_123"' in message or "'id': 'act_123'" in message
    
    # Assert chain data is present (check for "calls" or "puts")
    assert "calls" in message or "puts" in message.lower()


def test_contract_activity_data_in_message(test_app):
    """The message should contain the full activity JSON."""
    client, cosmos, _ = test_app
    
    # Add more fields to activity
    cosmos.activities["act_123"]["monitor_output"] = "Full analysis here"
    cosmos.activities["act_123"]["alpha_output"] = "Alpha perspective"
    
    response = client.post(
        "/api/activities/act_123/chat",
        json={"message": "Tell me about this decision"}
    )
    
    assert response.status_code == 200
    
    message = FakeAgent.captured_messages[0]
    
    # Activity fields should be in the message
    assert "monitor_output" in message
    assert "alpha_output" in message
    assert "WAIT" in message  # decision field


def test_contract_live_chain_data_in_message(test_app):
    """The message should contain current (live) option chain data."""
    client, _, fake_cache = test_app
    
    # Set specific chain data
    fake_cache.chain_data = {
        "calls": {
            "20260801": {
                "190.0": {"bid": 3.20, "ask": 3.30, "delta": 0.55}
            }
        },
        "puts": {}
    }
    
    response = client.post(
        "/api/activities/act_123/chat",
        json={"message": "What's the current chain?"}
    )
    
    assert response.status_code == 200
    
    message = FakeAgent.captured_messages[0]
    
    # Check for chain data
    assert "190.0" in message
    assert "3.20" in message or "3.2" in message
    assert "delta" in message.lower() or "0.55" in message


def test_conversation_history_included(test_app):
    """Given conversation history, it should appear in CONVERSATION SO FAR section."""
    client, _, _ = test_app
    
    history = [
        {"role": "user", "content": "What is the current strike?"},
        {"role": "assistant", "content": "The strike is $185."},
        {"role": "user", "content": "What about delta?"}
    ]
    
    response = client.post(
        "/api/activities/act_123/chat",
        json={
            "message": "And what's the expiration?",
            "history": history
        }
    )
    
    assert response.status_code == 200
    
    message = FakeAgent.captured_messages[0]
    
    # History should be formatted and present
    assert "What is the current strike?" in message
    assert "The strike is $185." in message
    assert "What about delta?" in message
    
    # Check for role labels (uppercased)
    assert "USER:" in message
    assert "ASSISTANT:" in message


def test_empty_history_shows_none(test_app):
    """With empty history, CONVERSATION SO FAR should show '(none)'."""
    client, _, _ = test_app
    
    response = client.post(
        "/api/activities/act_123/chat",
        json={
            "message": "What happened?",
            "history": []
        }
    )
    
    assert response.status_code == 200
    
    message = FakeAgent.captured_messages[0]
    
    # Should show (none) for empty history
    assert "=== CONVERSATION SO FAR ===" in message
    assert "(none)" in message


def test_chain_unavailable_degradation(test_app, monkeypatch):
    """If chain fetch fails, endpoint should still return 200 with unavailable note."""
    client, _, _ = test_app
    
    # Make get_or_load raise
    fake_cache_fail = FakeOptionsChainCache(should_raise=True)
    monkeypatch.setattr(
        "src.options_chain_cache.get_options_chain_cache",
        lambda: fake_cache_fail
    )
    
    response = client.post(
        "/api/activities/act_123/chat",
        json={"message": "What's the chain?"}
    )
    
    assert response.status_code == 200
    
    message = FakeAgent.captured_messages[0]
    
    # Should contain unavailable note
    assert "unavailable" in message.lower()


def test_no_linked_position_graceful(test_app):
    """If position_id is absent/null, POSITION section should show '(no linked position)'."""
    client, cosmos, _ = test_app
    
    # Activity without position_id
    cosmos.activities["act_789"] = {
        "id": "act_789",
        "symbol": "TSLA",
        "agent_type": "monitor",
        "decision": "BUY",
        "reason": "Good setup"
    }
    
    cosmos.symbols["TSLA"] = {
        "symbol": "TSLA",
        "positions": []
    }
    
    cosmos.technical_docs["TSLA"] = {
        "doc_type": "technical_analysis",
        "symbol": "TSLA",
        "analysis": "Strong buy signal",
        "timestamp": "2026-07-09T09:00:00Z"
    }
    
    response = client.post(
        "/api/activities/act_789/chat",
        json={"message": "What position is this?"}
    )
    
    assert response.status_code == 200
    
    message = FakeAgent.captured_messages[0]
    
    # Should show no linked position
    assert "(no linked position)" in message


def test_readonly_no_cosmos_writes(test_app):
    """Endpoint should NOT call any cosmos write/delete methods."""
    client, cosmos, _ = test_app
    
    # Track if any writes occur
    cosmos.write_called = False
    cosmos.delete_called = False
    
    def fake_write(*args, **kwargs):
        cosmos.write_called = True
        raise AssertionError("Cosmos write called during read-only endpoint")
    
    def fake_delete(*args, **kwargs):
        cosmos.delete_called = True
        raise AssertionError("Cosmos delete called during read-only endpoint")
    
    # Add methods and track
    cosmos.write_activity = fake_write
    cosmos.delete_activity = fake_delete
    cosmos.close_position = fake_write
    cosmos.roll_position = fake_write
    
    response = client.post(
        "/api/activities/act_123/chat",
        json={"message": "Tell me about this"}
    )
    
    assert response.status_code == 200
    assert not cosmos.write_called
    assert not cosmos.delete_called


def test_missing_position_in_symbol_doc(test_app):
    """If position_id exists but not found in symbol.positions, should be graceful."""
    client, cosmos, _ = test_app
    
    # Activity references position_id that doesn't exist in symbol doc
    cosmos.activities["act_999"] = {
        "id": "act_999",
        "symbol": "AAPL",
        "position_id": "missing_pos",
        "agent_type": "monitor",
        "decision": "WAIT"
    }
    
    response = client.post(
        "/api/activities/act_999/chat",
        json={"message": "What's my position?"}
    )
    
    assert response.status_code == 200
    
    message = FakeAgent.captured_messages[0]
    
    # Should show no linked position (since match failed)
    assert "(no linked position)" in message
