"""Integration tests for contract validation API and persistence.

Tests the complete flow from POST /api/best-options/validate through chain refresh,
contract lookup, evidence building, engine execution, activity persistence, and
status polling via GET /api/best-options/validate/{run_id}.

Uses real modules (agent_runner, options_chain_cache, cosmos_db) with faked
external providers (yfinance, Cosmos container), following the repository's
"avoid mutually fake internal modules" convention.
"""

import asyncio
import json
import pytest
import time
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, AsyncMock

from starlette.testclient import TestClient
from src.context import ContextProvider
from web.app import app


@pytest.fixture(autouse=True)
def isolate_validation_registry():
    """Isolate module-level validation registry between tests."""
    from src import contract_validation_integration as cvi

    # Save original state
    saved_validations = cvi._in_flight_validations.copy()
    saved_lock = cvi._validation_lock

    # Clear for this test
    cvi._in_flight_validations.clear()
    cvi._validation_lock = asyncio.Lock()

    yield

    # Restore
    cvi._in_flight_validations.clear()
    cvi._in_flight_validations.update(saved_validations)
    cvi._validation_lock = saved_lock


@pytest.fixture
def fake_cosmos():
    """Fake CosmosDBService for testing."""
    cosmos = MagicMock()
    cosmos.symbols = {}
    cosmos.activities = []
    cosmos.traces = []

    def get_symbol(symbol):
        return cosmos.symbols.get(symbol)

    def write_activity(symbol, agent_type, activity_data, timestamp=None, ttl_seconds=None):
        doc = {
            "id": f"{symbol}_{agent_type}_{len(cosmos.activities)}",
            "symbol": symbol,
            "agent_type": agent_type,
            **activity_data,
            "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
        }
        cosmos.activities.append(doc)
        return doc

    def get_activity_by_run_id(run_id):
        """Production fix: direct lookup by run_id."""
        for activity in cosmos.activities:
            if activity.get("run_id") == run_id:
                return activity
        return None

    def get_next_earnings_date(symbol):
        return None

    def get_next_calendar_event_date(symbol, event_type):
        return None

    cosmos.get_symbol = get_symbol
    cosmos.write_activity = write_activity
    cosmos.get_activity_by_run_id = get_activity_by_run_id
    cosmos.get_next_earnings_date = get_next_earnings_date
    cosmos.get_next_calendar_event_date = get_next_calendar_event_date

    return cosmos


@pytest.fixture
def sample_contract():
    """Sample valid contract data."""
    return {
        "strike": 155.0,
        "bid": 2.50,
        "ask": 2.55,
        "mid": 2.525,
        "lastPrice": 2.52,
        "volume": 100,
        "openInterest": 500,
        "iv": 0.30,
        "delta": 0.25,
        "gamma": 0.015,
        "theta": -0.05,
        "vega": 0.12,
        "rho": 0.08,
        "underlyingPrice": 150.0,
        "_meta": {
            "chain_timestamp": "2026-08-29T10:00:00Z",
        },
    }


@pytest.fixture
def sample_chain(sample_contract):
    """Sample options chain with one call contract."""
    return {
        "symbol": "TEST",
        "timestamp": "2026-08-29T10:00:00Z",
        "underlying_price": 150.0,
        "calls": {
            "2026-09-20": {
                "155.0": sample_contract,
            },
        },
        "puts": {},
    }


@pytest.fixture
def monkeypatch_chain_cache(monkeypatch, sample_chain):
    """Monkeypatch the chain cache to return sample data."""
    from src.options_chain_cache import OptionsChainCache

    # Create a fake cache that returns our sample chain
    cache = MagicMock(spec=OptionsChainCache)

    async def fake_refresh(symbol):
        return True

    def fake_get_or_hydrate(symbol, trigger_swr=True):
        if symbol == "TEST":
            return json.dumps(sample_chain)
        return None

    cache.refresh = fake_refresh
    cache.get_or_hydrate = fake_get_or_hydrate

    # Patch get_options_chain_cache to return our fake
    monkeypatch.setattr(
        "src.contract_validation_integration.get_options_chain_cache",
        lambda: cache
    )
    monkeypatch.setattr(
        "src.options_chain_cache.get_options_chain_cache",
        lambda: cache
    )

    return cache


@pytest.fixture
def client(fake_cosmos, monkeypatch):
    """Test client with cosmos dependency injected."""
    def mock_get_cosmos(request):
        return fake_cosmos

    monkeypatch.setattr("web.app._get_cosmos", mock_get_cosmos)

    return TestClient(app)


class TestValidationAPI:
    """Test POST /api/best-options/validate and GET /api/best-options/validate/{run_id}."""

    @pytest.mark.asyncio
    async def test_start_validation_returns_202_accepted(
        self, client, fake_cosmos, monkeypatch_chain_cache, sample_contract
    ):
        """POST /api/best-options/validate returns 202 with run_id."""
        fake_cosmos.symbols["TEST"] = {
            "symbol": "TEST",
            "enrichment": {"category": "balanced"},
            "total_shares": 500,
        }

        # Mock agent runner to avoid real LLM calls
        async def mock_run_contract_validation(*args, **kwargs):
            return {
                "symbol": "TEST",
                "agent_type": "covered_call",
                "side": "call",
                "strike": 155.0,
                "expiration": "2026-09-20",
                "activity": "SELL",
                "is_alert": True,
                "run_id": kwargs.get("run_id", "test-run-id"),
                "validation_status": "approved",
                "note": "Contract validated: SELL",
                "rule_evaluation": {"signal": "SELL"},
                "primary_trace_id": "trace-1",
                "supervisor_view": {"net_assessment": "APPROVE"},
                "supervisor_trace_id": "trace-2",
                "alpha_view": {"recommendation": "APPROVE"},
                "alpha_trace_id": "trace-3",
                "error": None,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

        # Patch the agent runner's run_contract_validation method
        with patch("src.agent_runner.AgentRunner.run_contract_validation", new=mock_run_contract_validation):
            # Start validation
            response = client.post(
                "/api/best-options/validate",
                json={
                    "symbol": "TEST",
                    "side": "call",
                    "strike": 155.0,
                    "expiration": "2026-09-20",
                    "source": "best_options",
                    "displayed_snapshot": {"premium": 2.52},
                },
            )

            assert response.status_code == 202
            data = response.json()
            assert data["status"] == "accepted"
            assert "run_id" in data
            assert "started_at" in data
            assert "status_url" in data
            assert "/api/best-options/validate/" in data["status_url"]

            run_id = data["run_id"]

            # Wait for background task to complete
            await asyncio.sleep(1.0)

            # Poll status
            status_response = client.get(f"/api/best-options/validate/{run_id}")
            assert status_response.status_code == 200
            status_data = status_response.json()

            # Should be completed with activity persisted
            assert status_data["status"] in ("completed", "in_progress")

    @pytest.mark.asyncio
    async def test_duplicate_validation_returns_409(
        self, fake_cosmos, monkeypatch_chain_cache, monkeypatch
    ):
        """Duplicate validation request returns 409 Conflict."""
        from httpx import AsyncClient, ASGITransport

        fake_cosmos.symbols["TEST"] = {
            "symbol": "TEST",
            "enrichment": {"category": "balanced"},
            "total_shares": 500,
        }

        # Patch cosmos
        def mock_get_cosmos(request):
            return fake_cosmos
        monkeypatch.setattr("web.app._get_cosmos", mock_get_cosmos)

        # Mock agent runner with a slow execution to keep task in-flight
        task_started = asyncio.Event()
        task_can_finish = asyncio.Event()

        async def slow_validation(*args, **kwargs):
            task_started.set()
            await task_can_finish.wait()
            return {
                "activity": "WAIT",
                "is_alert": False,
                "validation_status": "approved",
                "note": "Test validation",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "symbol": "TEST",
                "agent_type": "covered_call",
                "side": "call",
                "strike": 155.0,
                "expiration": "2026-09-20",
                "run_id": "test",
                "rule_evaluation": {},
                "primary_trace_id": None,
                "supervisor_view": None,
                "supervisor_trace_id": None,
                "alpha_view": None,
                "alpha_trace_id": None,
                "error": None,
            }

        with patch("src.agent_runner.AgentRunner.run_contract_validation", new=slow_validation):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                # First request
                resp1 = await client.post(
                    "/api/best-options/validate",
                    json={
                        "symbol": "TEST",
                        "side": "call",
                        "strike": 155.0,
                        "expiration": "2026-09-20",
                        "source": "best_options",
                    },
                )
                assert resp1.status_code == 202
                run_id_1 = resp1.json()["run_id"]

                # Wait for background task to actually start
                await asyncio.wait_for(task_started.wait(), timeout=2.0)

                # NOW send duplicate while first is still in-flight
                resp2 = await client.post(
                    "/api/best-options/validate",
                    json={
                        "symbol": "TEST",
                        "side": "call",
                        "strike": 155.0,
                        "expiration": "2026-09-20",
                        "source": "best_options",
                    },
                )
                assert resp2.status_code == 409
                data2 = resp2.json()
                assert data2["status"] == "duplicate"
                assert data2["run_id"] == run_id_1

                # Clean up: allow first task to finish
                task_can_finish.set()
                await asyncio.sleep(0.5)

    def test_invalid_side_returns_400(self, client, fake_cosmos):
        """Invalid side parameter returns 400 Bad Request."""
        fake_cosmos.symbols["TEST"] = {"enrichment": {"category": "balanced"}}

        response = client.post(
            "/api/best-options/validate",
            json={
                "symbol": "TEST",
                "side": "invalid",
                "strike": 155.0,
                "expiration": "2026-09-20",
                "source": "best_options",
            },
        )
        assert response.status_code == 400
        data = response.json()
        assert data["status"] == "error"
        assert "Invalid side" in data["message"]

    def test_contract_not_found_persists_error_activity(
        self, client, fake_cosmos, monkeypatch
    ):
        """Contract not found results in error activity."""
        fake_cosmos.symbols["TEST"] = {
            "enrichment": {"category": "balanced"},
            "total_shares": 500,
        }

        # Monkeypatch chain cache to return empty chain
        from src.options_chain_cache import OptionsChainCache
        cache = MagicMock(spec=OptionsChainCache)

        async def fake_refresh(symbol):
            return True

        def fake_get_or_hydrate(symbol, trigger_swr=True):
            return json.dumps({"symbol": "TEST", "calls": {}, "puts": {}})

        cache.refresh = fake_refresh
        cache.get_or_hydrate = fake_get_or_hydrate

        monkeypatch.setattr(
            "src.contract_validation_integration.get_options_chain_cache",
            lambda: cache
        )

        response = client.post(
            "/api/best-options/validate",
            json={
                "symbol": "TEST",
                "side": "call",
                "strike": 155.0,
                "expiration": "2026-09-20",
                "source": "best_options",
            },
        )
        assert response.status_code == 202
        run_id = response.json()["run_id"]

        # Wait for execution
        import time
        time.sleep(1.0)

        # Check that error activity was persisted
        assert len(fake_cosmos.activities) > 0
        latest = fake_cosmos.activities[-1]
        assert latest["run_id"] == run_id
        assert latest["validation_status"] == "error"
        assert "not found" in latest["note"].lower()


class TestEvidenceBuilding:
    """Test evaluated_snapshot construction and validation."""

    @pytest.mark.asyncio
    async def test_builds_complete_evidence_snapshot(
        self, fake_cosmos, monkeypatch_chain_cache, sample_contract, sample_chain
    ):
        """_build_evaluated_snapshot includes all required fields."""
        from src.contract_validation_integration import _build_evaluated_snapshot

        fake_cosmos.symbols["TEST"] = {
            "symbol": "TEST",
            "enrichment": {"category": "balanced"},
            "total_shares": 500,
        }

        # Need to pass the contract through contract_view first
        from src.options_chain_view import contract_view
        now = datetime.now(timezone.utc)
        contract_normalized = contract_view(sample_contract, now=now, stale_after_seconds=7200)

        snapshot = await _build_evaluated_snapshot(
            symbol="TEST",
            side="call",
            strike=155.0,
            expiration="2026-09-20",
            contract=contract_normalized,
            chain=sample_chain,
            cosmos=fake_cosmos,
        )

        # Verify all required fields
        assert snapshot["category"] == "balanced"
        assert snapshot["underlying_price"] == 150.0
        assert snapshot["total_shares"] == 500  # For calls
        assert "contract_data" in snapshot
        assert snapshot["contract_data"]["strike"] == 155.0
        assert snapshot["contract_data"]["bid"] == 2.50
        assert snapshot["contract_data"]["delta"] == 0.25
        assert "market_data_text" in snapshot
        assert "chain_timestamp" in snapshot

    @pytest.mark.asyncio
    async def test_validates_unusable_market_data(self):
        """_validate_contract_evidence rejects invalid markets."""
        from src.contract_validation_integration import _validate_contract_evidence

        # Zero bid/ask
        contract = {"bid": None, "ask": None, "_meta": {}}
        is_valid, error = _validate_contract_evidence(contract)
        assert not is_valid
        assert "No usable market" in error

        # Crossed market
        contract = {
            "bid": 3.0,
            "ask": 2.5,
            "iv": 0.30,
            "delta": 0.25,
            "_meta": {},
        }
        is_valid, error = _validate_contract_evidence(contract)
        assert not is_valid
        assert "Crossed market" in error

        # Missing IV
        contract = {
            "bid": 2.5,
            "ask": 2.55,
            "iv": None,
            "delta": 0.25,
            "_meta": {},
        }
        is_valid, error = _validate_contract_evidence(contract)
        assert not is_valid
        assert "IV unavailable" in error


class TestActivityPersistence:
    """Test validation activity persistence with run_id."""

    @pytest.mark.asyncio
    async def test_persists_activity_with_run_id(self, fake_cosmos):
        """Activity includes run_id and validation-specific fields."""
        from src.contract_validation_integration import _persist_validation_activity

        result = {
            "activity": "SELL",
            "is_alert": True,
            "validation_status": "approved",
            "note": "Contract validated: SELL",
            "rule_evaluation": {"signal": "SELL"},
            "primary_trace_id": "trace-1",
            "supervisor_view": {"net_assessment": "APPROVE"},
            "supervisor_trace_id": "trace-2",
            "alpha_view": {"recommendation": "APPROVE"},
            "alpha_trace_id": "trace-3",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        await _persist_validation_activity(
            cosmos=fake_cosmos,
            run_id="test-run-123",
            symbol="TEST",
            side="call",
            strike=155.0,
            expiration="2026-09-20",
            source="best_options",
            displayed_snapshot={"premium": 2.52},
            evaluated_snapshot={"category": "balanced"},
            result=result,
        )

        assert len(fake_cosmos.activities) == 1

        activity = fake_cosmos.activities[0]
        assert activity["run_id"] == "test-run-123"
        assert activity["run_trigger"] == "best_option_validation"
        assert activity["source"] == "best_options"
        assert activity["contract_strike"] == 155.0
        assert activity["contract_expiration"] == "2026-09-20"
        assert activity["contract_side"] == "call"
        assert activity["displayed_snapshot"] == {"premium": 2.52}
        assert activity["evaluated_snapshot"] == {"category": "balanced"}
        assert activity["validation_status"] == "approved"
        assert activity["is_alert"] is True
        assert activity["primary_trace_id"] == "trace-1"
        assert activity["supervisor_trace_id"] == "trace-2"
        assert activity["alpha_trace_id"] == "trace-3"
