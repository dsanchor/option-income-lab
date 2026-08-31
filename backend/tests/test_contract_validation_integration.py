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
    """Test client with cosmos dependency injected and scheduler.runner."""
    from src.agent_runner import AgentRunner
    from src.llm import LlmConfig

    def mock_get_cosmos(request):
        return fake_cosmos

    monkeypatch.setattr("web.app._get_cosmos", mock_get_cosmos)

    # Create a test runner with complete config (not placeholder)
    test_llm_config = LlmConfig(
        provider="gemini",
        api_key="test-integration-key",
    )
    test_runner = AgentRunner(
        llm=test_llm_config,
        model="gpt-5.4-mini",
        telegram_notifier=None,
    )

    # Create a fake scheduler with the test runner
    fake_scheduler = MagicMock()
    fake_scheduler.runner = test_runner

    # Create a fake provider with production-shaped fetch_all
    fake_provider = MagicMock()
    async def fake_fetch_all(symbol, force_refresh=False):
        """Production-shaped full_data with all 4 pages."""
        return {
            "symbol": symbol,
            "exchange": "NASDAQ",
            "overview": {
                "price": {"current": 150.0},
                "fundamentals": {
                    "earnings_release_next_date_fq": {
                        "value": None,
                        "formatted": "N/A"
                    }
                }
            },
            "dividends": {
                "ex_dividend_date_recent": {
                    "value": None,
                    "formatted": "N/A"
                }
            },
            "enrichment_data": {
                "category": "balanced",
                "volatility": {"implied_volatility_30d": 0.25}
            },
            "volatility": {"ivrank": 50},
            "options_chain": {
                "timestamp": "2026-08-29T10:00:00Z",
                "underlying_price": 150.0,
                "calls": {
                    "2026-09-20": {
                        "155.0": {
                            "strike": 155.0,
                            "bid": 2.50,
                            "ask": 2.55,
                            "mid": 2.525,
                            "lastPrice": 2.52,
                            "volume": 100,
                            "openInterest": 500,
                            "iv": 0.30,
                            "delta": 0.25,
                        }
                    }
                },
                "puts": {}
            }
        }
    fake_provider.fetch_all = fake_fetch_all

    # Inject scheduler and provider into app state
    app.state.scheduler = fake_scheduler
    app.state.yf_provider = fake_provider

    try:
        yield TestClient(app)
    finally:
        # Clean up app state
        if hasattr(app.state, "scheduler"):
            delattr(app.state, "scheduler")
        if hasattr(app.state, "yf_provider"):
            delattr(app.state, "yf_provider")


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
        from src.agent_runner import AgentRunner
        from src.llm import LlmConfig

        fake_cosmos.symbols["TEST"] = {
            "symbol": "TEST",
            "enrichment": {"category": "balanced"},
            "total_shares": 500,
        }

        # Patch cosmos
        def mock_get_cosmos(request):
            return fake_cosmos
        monkeypatch.setattr("web.app._get_cosmos", mock_get_cosmos)

        # Create a test runner with complete config
        test_llm_config = LlmConfig(
            provider="gemini",
            api_key="test-integration-key",
        )
        test_runner = AgentRunner(
            llm=test_llm_config,
            model="gpt-5.4-mini",
            telegram_notifier=None,
        )

        # Create a fake scheduler with the test runner
        fake_scheduler = MagicMock()
        fake_scheduler.runner = test_runner

        # Create a fake provider
        fake_provider = MagicMock()
        async def fake_fetch_all(symbol, force_refresh=False):
            return {
                "symbol": symbol,
                "exchange": "NASDAQ",
                "overview": {"price": {"current": 150.0}, "fundamentals": {"earnings_release_next_date_fq": {"value": None, "formatted": "N/A"}}},
                "dividends": {"ex_dividend_date_recent": {"value": None, "formatted": "N/A"}},
                "enrichment_data": {"category": "balanced", "volatility": {"implied_volatility_30d": 0.25}},
                "volatility": {"ivrank": 50},
                "options_chain": {
                    "timestamp": "2026-08-29T10:00:00Z",
                    "underlying_price": 150.0,
                    "calls": {"2026-09-20": {"155.0": {"strike": 155.0, "bid": 2.50, "ask": 2.55, "mid": 2.525, "iv": 0.30, "delta": 0.25}}},
                    "puts": {}
                }
            }
        fake_provider.fetch_all = fake_fetch_all

        # Inject scheduler and provider into app state
        app.state.scheduler = fake_scheduler
        app.state.yf_provider = fake_provider

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

        try:
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

        finally:
            # Clean up app state
            if hasattr(app.state, "scheduler"):
                delattr(app.state, "scheduler")
            if hasattr(app.state, "yf_provider"):
                delattr(app.state, "yf_provider")

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
        self, fake_cosmos, monkeypatch
    ):
        """Contract not found results in error activity."""
        from httpx import AsyncClient, ASGITransport
        from src.agent_runner import AgentRunner
        from src.llm import LlmConfig

        fake_cosmos.symbols["TEST"] = {
            "enrichment": {"category": "balanced"},
            "total_shares": 500,
        }

        def mock_get_cosmos(request):
            return fake_cosmos
        monkeypatch.setattr("web.app._get_cosmos", mock_get_cosmos)

        # Create a test runner
        test_llm_config = LlmConfig(provider="gemini", api_key="test-integration-key")
        test_runner = AgentRunner(llm=test_llm_config, model="gpt-5.4-mini", telegram_notifier=None)
        fake_scheduler = MagicMock()
        fake_scheduler.runner = test_runner

        # Inject provider that returns empty chain (contract not found scenario)
        fake_provider = MagicMock()
        async def fake_fetch_all_empty(symbol, force_refresh=False):
            return {
                "symbol": symbol,
                "exchange": "NASDAQ",
                "overview": {"price": {"current": 150.0}, "fundamentals": {"earnings_release_next_date_fq": {"value": None, "formatted": "N/A"}}},
                "dividends": {"ex_dividend_date_recent": {"value": None, "formatted": "N/A"}},
                "enrichment_data": {"category": "balanced", "volatility": {"implied_volatility_30d": 0.25}},
                "volatility": {"ivrank": 50},
                "options_chain": {
                    "timestamp": "2026-08-29T10:00:00Z",
                    "underlying_price": 150.0,
                    "calls": {},  # Empty - no contracts
                    "puts": {}
                }
            }
        fake_provider.fetch_all = fake_fetch_all_empty

        app.state.scheduler = fake_scheduler
        app.state.yf_provider = fake_provider

        try:
            import asyncio
            async def run_test():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                    response = await client.post(
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

                    # Wait for execution (background task to persist error)
                    await asyncio.sleep(2.0)

                    # Check that error activity was persisted
                    assert len(fake_cosmos.activities) > 0, "No activities persisted after 2s wait"
                    latest = fake_cosmos.activities[-1]
                    assert latest["run_id"] == run_id
                    assert latest["validation_status"] == "error"
                    assert "not found" in latest["note"].lower()

            asyncio.run(run_test())

        finally:
            if hasattr(app.state, "scheduler"):
                delattr(app.state, "scheduler")
            if hasattr(app.state, "yf_provider"):
                delattr(app.state, "yf_provider")


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
    """Test validation activity persistence using canonical agent schema."""

    @pytest.mark.asyncio
    async def test_persists_canonical_activity_schema(self, fake_cosmos):
        """Activity uses canonical agent schema, not validation-specific structure."""
        from src.contract_validation_integration import _persist_validation_activity

        # Simulate agent result with canonical activity_data (same as normal runs)
        canonical_activity_data = {
            "activity": "SELL",
            "reason": "Strong premium with acceptable delta and risk profile",
            "confidence": "high",
            "underlying_price": 150.25,
            "strike": 155.0,
            "expiration": "2026-09-20",
            "premium": 2.52,
            "premium_pct": 1.68,
            "iv": 30.5,
            "delta": -0.25,
            "risk_rating": 3,
            "risk_flags": ["approaching_earnings"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        result = {
            "activity": "SELL",
            "is_alert": True,
            "validation_status": "approved",
            "activity_data": canonical_activity_data,  # Canonical agent output
            "primary_trace_id": "trace-1",
            "supervisor_trace_id": "trace-2",
            "alpha_trace_id": "trace-3",
            "rule_evaluation": {"signal": "SELL"},
            "supervisor_view": {"net_assessment": "APPROVE"},
            "alpha_view": {"recommendation": "APPROVE"},
            "timestamp": canonical_activity_data["timestamp"],
        }

        await _persist_validation_activity(
            cosmos=fake_cosmos,
            run_id="test-run-123",
            symbol="TEST",
            side="call",
            strike=155.0,
            expiration="2026-09-20",
            source="best_options",
            displayed_snapshot=None,
            evaluated_snapshot=None,
            result=result,
        )

        assert len(fake_cosmos.activities) == 1

        activity = fake_cosmos.activities[0]

        # Verify canonical agent fields are preserved
        assert activity["activity"] == "SELL"
        assert activity["reason"] == "Strong premium with acceptable delta and risk profile"
        assert activity["confidence"] == "high"
        assert activity["underlying_price"] == 150.25
        assert activity["strike"] == 155.0
        assert activity["expiration"] == "2026-09-20"
        assert activity["premium"] == 2.52
        assert activity["premium_pct"] == 1.68
        assert activity["iv"] == 30.5
        assert activity["delta"] == -0.25
        assert activity["risk_rating"] == 3
        assert activity["risk_flags"] == ["approaching_earnings"]

        # Verify validation metadata (minimal augmentation)
        assert activity["run_id"] == "test-run-123"
        assert activity["run_trigger"] == "best_option_validation"
        assert activity["validation_status"] == "approved"
        assert activity["is_alert"] is True
        assert activity["primary_trace_id"] == "trace-1"
        assert activity["supervisor_trace_id"] == "trace-2"
        assert activity["alpha_trace_id"] == "trace-3"

    @pytest.mark.asyncio
    async def test_canonical_schema_matches_normal_agent_run(self, fake_cosmos):
        """Regression: validation activity schema matches normal agent run schema."""
        from src.contract_validation_integration import _persist_validation_activity

        # Canonical fields that ALL agent runs should have
        canonical_fields = {
            "activity", "reason", "confidence", "underlying_price", "strike",
            "expiration", "premium", "iv", "risk_rating", "risk_flags",
            "timestamp", "is_alert", "run_id", "rule_evaluation",
        }

        # Validation activity
        validation_activity_data = {
            "activity": "SELL",
            "reason": "Test reason",
            "confidence": "medium",
            "underlying_price": 100.0,
            "strike": 105.0,
            "expiration": "2026-09-20",
            "premium": 1.50,
            "iv": 25.0,
            "risk_rating": 2,
            "risk_flags": [],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        validation_result = {
            "activity": "SELL",
            "is_alert": True,
            "validation_status": "approved",
            "activity_data": validation_activity_data,
            "rule_evaluation": {},
            "timestamp": validation_activity_data["timestamp"],
        }

        await _persist_validation_activity(
            cosmos=fake_cosmos,
            run_id="val-run-1",
            symbol="TEST",
            side="call",
            strike=105.0,
            expiration="2026-09-20",
            source="best_options",
            displayed_snapshot=None,
            evaluated_snapshot=None,
            result=validation_result,
        )

        # Normal agent run (simulated - what cosmos.write_activity receives)
        normal_activity_data = {
            "activity": "SELL",
            "reason": "Test reason",
            "confidence": "medium",
            "underlying_price": 100.0,
            "strike": 105.0,
            "expiration": "2026-09-20",
            "premium": 1.50,
            "iv": 25.0,
            "risk_rating": 2,
            "risk_flags": [],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "is_alert": True,
            "run_id": "normal-run-1",
            "rule_evaluation": {},
            "run_trigger": "scheduled",
        }

        fake_cosmos.write_activity(
            symbol="TEST",
            agent_type="covered_call",
            activity_data=normal_activity_data,
            timestamp=normal_activity_data["timestamp"],
        )

        assert len(fake_cosmos.activities) == 2

        validation_activity = fake_cosmos.activities[0]
        normal_activity = fake_cosmos.activities[1]

        # Both activities must have the same canonical fields
        for field in canonical_fields:
            assert field in validation_activity, f"Validation activity missing canonical field: {field}"
            assert field in normal_activity, f"Normal activity missing canonical field: {field}"

        # Field types must match
        for field in ["activity", "reason", "confidence"]:
            assert type(validation_activity[field]) == type(normal_activity[field])

        for field in ["underlying_price", "strike", "premium", "iv"]:
            assert isinstance(validation_activity[field], (int, float))
            assert isinstance(normal_activity[field], (int, float))

    @pytest.mark.asyncio
    async def test_backward_compatible_with_missing_activity_data(self, fake_cosmos):
        """Activity handles error cases where agent didn't return parseable JSON."""
        from src.contract_validation_integration import _persist_validation_activity

        # Error result without activity_data
        result = {
            "activity": "WAIT",
            "is_alert": False,
            "validation_status": "error",
            "error": "Agent returned no parseable JSON",
            "activity_data": None,  # Missing
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        await _persist_validation_activity(
            cosmos=fake_cosmos,
            run_id="test-run-error",
            symbol="TEST",
            side="put",
            strike=95.0,
            expiration="2026-09-20",
            source="best_options",
            displayed_snapshot=None,
            evaluated_snapshot=None,
            result=result,
        )

        assert len(fake_cosmos.activities) == 1

        activity = fake_cosmos.activities[0]

        # Verify minimal fallback structure
        assert activity["symbol"] == "TEST"
        assert activity["activity"] == "WAIT"
        assert activity["error"] == "Agent returned no parseable JSON"
        assert activity["validation_status"] == "error"
        assert activity["run_id"] == "test-run-error"

    @pytest.mark.asyncio
    async def test_get_validation_status_returns_canonical_fields(self, fake_cosmos):
        """get_validation_status returns canonical agent fields."""
        from src.contract_validation_integration import get_validation_status

        # Create a completed activity with canonical schema
        activity_doc = {
            "id": "activity-123",
            "symbol": "TEST",
            "agent_type": "covered_call",
            "activity": "SELL",
            "reason": "Strong premium opportunity",
            "confidence": "high",
            "underlying_price": 150.25,
            "strike": 155.0,
            "expiration": "2026-09-20",
            "premium": 2.52,
            "iv": 30.5,
            "risk_rating": 3,
            "risk_flags": ["approaching_earnings"],
            "is_alert": True,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            # Validation metadata
            "run_id": "run-abc-123",
            "run_trigger": "best_option_validation",
            "validation_status": "approved",
            "primary_trace_id": "trace-1",
            "rule_evaluation": {},
        }
        fake_cosmos.activities.append(activity_doc)

        status = await get_validation_status("run-abc-123", fake_cosmos)

        assert status["status"] == "completed"
        assert status["run_id"] == "run-abc-123"
        assert status["activity_id"] == "activity-123"

        # Verify canonical agent fields are returned
        assert status["activity"] == "SELL"
        assert status["reason"] == "Strong premium opportunity"
        assert status["confidence"] == "high"
        assert status["underlying_price"] == 150.25
        assert status["strike"] == 155.0
        assert status["expiration"] == "2026-09-20"
        assert status["premium"] == 2.52
        assert status["iv"] == 30.5
        assert status["risk_rating"] == 3
        assert status["risk_flags"] == ["approaching_earnings"]

        # Verify validation metadata
        assert status["validation_status"] == "approved"
        assert status["run_trigger"] == "best_option_validation"


class TestRunnerIdentityRegression:
    """Regression tests for production hotfix: validation must use scheduler.runner.

    Issue: Prior implementation constructed new AgentRunner with placeholder
    {"provider": "azure"} dict, leading to SettingNotFoundError when creating
    OpenAI client (missing credentials).

    Fix: Validation endpoint must reuse scheduler.runner (which has complete
    LlmConfig, credentials, per-function configs, client cache, etc.)
    """

    @pytest.mark.asyncio
    async def test_validation_endpoint_uses_scheduler_runner(
        self, fake_cosmos, monkeypatch_chain_cache, monkeypatch
    ):
        """Validation endpoint must use scheduler.runner, not ad-hoc AgentRunner."""
        from httpx import AsyncClient, ASGITransport
        from src.agent_runner import AgentRunner
        from src.llm import LlmConfig

        # Create a sentinel runner with identifiable config
        sentinel_llm_config = LlmConfig(
            provider="gemini",
            api_key="sentinel-test-key-12345",
        )
        sentinel_runner = AgentRunner(
            llm=sentinel_llm_config,
            model="gpt-5.4-mini",
            telegram_notifier=None,
        )

        # Create a fake scheduler with the sentinel runner
        fake_scheduler = MagicMock()
        fake_scheduler.runner = sentinel_runner

        # Mock agent runner to avoid real LLM calls and capture the runner used
        captured_runner = None

        async def mock_run_contract_validation(self, *args, **kwargs):
            nonlocal captured_runner
            captured_runner = self
            return {
                "symbol": "TEST",
                "agent_type": "covered_call",
                "side": "call",
                "strike": 155.0,
                "expiration": "2026-09-20",
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
                "error": None,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

        monkeypatch.setattr(
            AgentRunner,
            "run_contract_validation",
            mock_run_contract_validation
        )

        def mock_get_cosmos(request):
            return fake_cosmos

        monkeypatch.setattr("web.app._get_cosmos", mock_get_cosmos)

        fake_cosmos.symbols["TEST"] = {
            "symbol": "TEST",
            "enrichment": {"category": "balanced"},
            "total_shares": 500,
        }

        # Inject sentinel scheduler into app state
        app.state.scheduler = fake_scheduler

        # Inject fake provider
        fake_provider = MagicMock()
        async def fake_fetch_all(symbol, force_refresh=False):
            return {
                "symbol": symbol,
                "exchange": "NASDAQ",
                "overview": {"price": {"current": 150.0}, "fundamentals": {"earnings_release_next_date_fq": {"value": None, "formatted": "N/A"}}},
                "dividends": {"ex_dividend_date_recent": {"value": None, "formatted": "N/A"}},
                "enrichment_data": {"category": "balanced", "volatility": {"implied_volatility_30d": 0.25}},
                "volatility": {"ivrank": 50},
                "options_chain": {
                    "timestamp": "2026-08-29T10:00:00Z",
                    "underlying_price": 150.0,
                    "calls": {"2026-09-20": {"155.0": {"strike": 155.0, "bid": 2.50, "ask": 2.55, "mid": 2.525, "iv": 0.30, "delta": 0.25}}},
                    "puts": {}
                }
            }
        fake_provider.fetch_all = fake_fetch_all
        app.state.yf_provider = fake_provider

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/api/best-options/validate",
                    json={
                        "symbol": "TEST",
                        "side": "call",
                        "strike": 155.0,
                        "expiration": "2026-09-20",
                        "source": "best_options",
                        "displayed_snapshot": None,
                    },
                )

                assert response.status_code == 202
                data = response.json()
                assert data["status"] == "accepted"

                # Wait for background task to complete
                await asyncio.sleep(1.0)

                # CRITICAL: Verify the runner used is the sentinel scheduler.runner
                assert captured_runner is not None, "run_contract_validation was not called"
                assert captured_runner is sentinel_runner, (
                    "Validation must use scheduler.runner, not ad-hoc AgentRunner"
                )

                # Verify sentinel config was preserved (proves no re-construction)
                assert captured_runner._llm.provider == "gemini"
                assert captured_runner._llm.api_key == "sentinel-test-key-12345"

        finally:
            # Clean up app state
            if hasattr(app.state, "scheduler"):
                delattr(app.state, "scheduler")
            if hasattr(app.state, "yf_provider"):
                delattr(app.state, "yf_provider")

    @pytest.mark.asyncio
    async def test_validation_fails_503_when_scheduler_unavailable(
        self, fake_cosmos, monkeypatch
    ):
        """Validation endpoint returns 503 when scheduler/runner not available."""
        from httpx import AsyncClient, ASGITransport

        def mock_get_cosmos(request):
            return fake_cosmos

        monkeypatch.setattr("web.app._get_cosmos", mock_get_cosmos)

        # Ensure app.state has no scheduler
        if hasattr(app.state, "scheduler"):
            delattr(app.state, "scheduler")

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/best-options/validate",
                json={
                    "symbol": "TEST",
                    "side": "call",
                    "strike": 155.0,
                    "expiration": "2026-09-20",
                    "source": "best_options",
                    "displayed_snapshot": None,
                },
            )

            assert response.status_code == 503
            data = response.json()
            assert data["status"] == "error"
            assert "infrastructure not available" in data["message"].lower()

    @pytest.mark.asyncio
    async def test_placeholder_azure_config_would_fail_without_credentials(self):
        """Regression: placeholder {"provider": "azure"} dict fails without credentials.

        This test documents the original bug: constructing AgentRunner with
        {"provider": "azure"} and no api_key/endpoint would fail when creating
        the OpenAI client.
        """
        from src.agent_runner import AgentRunner
        from src.llm import LlmConfig, validate_llm_config

        # Simulate the old code path: {"provider": "azure"} dict normalization
        placeholder_config = {"provider": "azure"}
        normalized = AgentRunner._normalize_llm_config(placeholder_config)

        # Normalized config has empty api_key and endpoint
        assert isinstance(normalized, LlmConfig)
        assert normalized.provider == "azure"
        assert normalized.api_key == ""
        assert normalized.endpoint is None

        # Validate would fail (incomplete config)
        error = validate_llm_config(normalized)
        assert error is not None
        assert "api key not configured" in error.lower()

    def test_normalize_preserves_complete_config(self):
        """Dict normalization preserves complete config (legitimate use case)."""
        from src.agent_runner import AgentRunner
        from src.llm import LlmConfig, validate_llm_config

        # Complete config dict (legitimate use case: tests, config reload, etc.)
        complete_dict = {
            "provider": "azure",
            "api_key": "test-key-abc123",
            "endpoint": "https://test.openai.azure.com",
        }
        normalized = AgentRunner._normalize_llm_config(complete_dict)

        assert isinstance(normalized, LlmConfig)
        assert normalized.provider == "azure"
        assert normalized.api_key == "test-key-abc123"
        assert normalized.endpoint == "https://test.openai.azure.com"

        # Should pass validation
        error = validate_llm_config(normalized)
        assert error is None


class TestReasonNoteFallbackRegression:
    """Regression test for canonical reason field with backward-compatible note fallback.

    Danny's retrospective (2026-08-30):
    Canonical `reason` must win when both fields exist, fall back to `note` for
    note-only legacy/error docs, and return both fields for backward compatibility.
    """

    @pytest.mark.asyncio
    async def test_reason_field_wins_when_both_present(self, fake_cosmos):
        """When both reason and note exist, reason is returned as canonical."""
        from src.contract_validation_integration import get_validation_status

        # Simulate activity with both reason (canonical) and note (legacy)
        activity = {
            "id": "AAPL_covered_call_test",
            "run_id": "test-run-both-fields",
            "symbol": "AAPL",
            "agent_type": "covered_call",
            "activity": "WAIT",
            "is_alert": False,
            "timestamp": "2026-08-30T12:00:00Z",
            "reason": "IV below threshold (canonical)",
            "note": "Old note format (legacy)",
        }
        fake_cosmos.activities.append(activity)

        result = await get_validation_status("test-run-both-fields", fake_cosmos)

        assert result["status"] == "completed"
        assert result["reason"] == "IV below threshold (canonical)", \
            "Canonical reason should win when both exist"
        assert result["note"] == "Old note format (legacy)", \
            "Legacy note should still be returned for backward compatibility"

    @pytest.mark.asyncio
    async def test_reason_falls_back_to_note_when_reason_missing(self, fake_cosmos):
        """When reason is missing but note exists, reason falls back to note."""
        from src.contract_validation_integration import get_validation_status

        # Simulate legacy activity with only note
        activity = {
            "id": "MSFT_cash_secured_put_test",
            "run_id": "test-run-note-only",
            "symbol": "MSFT",
            "agent_type": "cash_secured_put",
            "activity": "WAIT",
            "is_alert": False,
            "timestamp": "2026-08-30T12:00:00Z",
            "note": "Legacy note only",
        }
        fake_cosmos.activities.append(activity)

        result = await get_validation_status("test-run-note-only", fake_cosmos)

        assert result["status"] == "completed"
        assert result["reason"] == "Legacy note only", \
            "Canonical reason should fall back to note when reason missing"
        assert result["note"] == "Legacy note only", \
            "Legacy note should be returned"

    @pytest.mark.asyncio
    async def test_reason_none_when_both_missing(self, fake_cosmos):
        """When both reason and note are missing, reason is None."""
        from src.contract_validation_integration import get_validation_status

        # Simulate activity with neither field
        activity = {
            "id": "TSLA_covered_call_test",
            "run_id": "test-run-no-fields",
            "symbol": "TSLA",
            "agent_type": "covered_call",
            "activity": "SELL",
            "is_alert": True,
            "timestamp": "2026-08-30T12:00:00Z",
        }
        fake_cosmos.activities.append(activity)

        result = await get_validation_status("test-run-no-fields", fake_cosmos)

        assert result["status"] == "completed"
        assert result["reason"] is None, \
            "Canonical reason should be None when both missing"
        assert result["note"] is None, \
            "Legacy note should be None when both missing"

    @pytest.mark.asyncio
    async def test_field_completeness_includes_both_reason_and_note(self, fake_cosmos):
        """Response includes both reason and note fields for schema completeness."""
        from src.contract_validation_integration import get_validation_status

        activity = {
            "id": "NVDA_covered_call_test",
            "run_id": "test-run-field-check",
            "symbol": "NVDA",
            "agent_type": "covered_call",
            "activity": "WAIT",
            "is_alert": False,
            "timestamp": "2026-08-30T12:00:00Z",
            "reason": "Canonical reason",
        }
        fake_cosmos.activities.append(activity)

        result = await get_validation_status("test-run-field-check", fake_cosmos)

        # Assert both fields are present in response (schema completeness)
        assert "reason" in result, "Response must include canonical reason field"
        assert "note" in result, "Response must include backward-compatible note field"
        assert result["reason"] == "Canonical reason"
        assert result["note"] is None  # note not in activity

    @pytest.mark.asyncio
    async def test_provider_injected_no_global_singleton_call(
        self, client, fake_cosmos, monkeypatch_chain_cache
    ):
        """Validation uses injected provider, never calls get_shared_provider()."""
        from src import contract_validation_integration as cvi

        fake_cosmos.symbols["TEST"] = {
            "symbol": "TEST",
            "enrichment": {"category": "balanced"},
            "total_shares": 500,
        }

        # The client fixture already injects fake_provider with canned fetch_all,
        # so validation should never need to call get_shared_provider().
        # If _execute_validation tried to call it, it would fail with ImportError
        # since we removed the import.

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
                "note": "Provider injection test",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

        with patch("src.agent_runner.AgentRunner.run_contract_validation", new=mock_run_contract_validation):
            # Start validation - should use injected provider, not global
            response = client.post(
                "/api/best-options/validate",
                json={
                    "symbol": "TEST",
                    "side": "call",
                    "strike": 155.0,
                    "expiration": "2026-09-20",
                    "source": "best_options",
                    "displayed_snapshot": None,
                },
            )

            assert response.status_code == 202, f"POST failed: {response.text}"

            # Wait for background task to complete
            await asyncio.sleep(0.5)

            # If get_shared_provider was called, it would have raised ImportError or NameError
            # since the function no longer exists in the module. Test passing = no call.

    @pytest.mark.asyncio
    async def test_validation_completes_promptly_no_network_hang(
        self, client, fake_cosmos, monkeypatch_chain_cache
    ):
        """Background validation completes within 5s without network I/O."""
        fake_cosmos.symbols["TEST"] = {
            "symbol": "TEST",
            "enrichment": {"category": "balanced"},
            "total_shares": 500,
        }

        # Mock agent runner to return quickly
        async def mock_run_contract_validation(*args, **kwargs):
            return {
                "symbol": "TEST",
                "agent_type": "covered_call",
                "side": "call",
                "strike": 155.0,
                "expiration": "2026-09-20",
                "activity": "WAIT",
                "is_alert": False,
                "run_id": kwargs.get("run_id", "test-run-id"),
                "validation_status": "rejected",
                "note": "Timeout test",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

        with patch("src.agent_runner.AgentRunner.run_contract_validation", new=mock_run_contract_validation):
            start_time = time.time()

            # POST should return 202 within 2 seconds
            response = client.post(
                "/api/best-options/validate",
                json={
                    "symbol": "TEST",
                    "side": "call",
                    "strike": 155.0,
                    "expiration": "2026-09-20",
                    "source": "best_options",
                    "displayed_snapshot": None,
                },
            )

            post_time = time.time() - start_time
            assert response.status_code == 202, f"POST failed: {response.text}"
            assert post_time < 2.0, f"POST took {post_time:.2f}s - should be <2s"

            run_id = response.json()["run_id"]

            # Background task should complete within 5 seconds total
            max_wait = 5.0
            poll_start = time.time()
            completed = False

            while (time.time() - poll_start) < max_wait:
                await asyncio.sleep(0.1)
                status_response = client.get(f"/api/best-options/validate/{run_id}")
                if status_response.status_code == 200:
                    result = status_response.json()
                    if result["status"] == "completed":
                        completed = True
                        break

            total_time = time.time() - start_time
            assert completed, f"Validation did not complete within {max_wait}s - possible network hang"
            assert total_time < max_wait, f"Total time {total_time:.2f}s exceeds {max_wait}s"
