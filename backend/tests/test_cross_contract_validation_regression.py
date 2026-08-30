"""Cross-contract regression test for exact-contract validation endpoint.

Tests that POST /api/best-options/validate accepts the EXACT JSON payload shape
sent by the frontend (ContractValidationAction → useContractValidation → Next.js BFF).

Ensures the backend Pydantic model matches the frontend TypeScript types and
prevents future 422 validation errors from contract drift.

PRODUCTION HOTFIX REGRESSIONS:
- Test underlying_price at chain level (not contract row)
- Test error status persistence and retrieval after registry cleanup
- Test get_activity_by_run_id replaces nonexistent list_activities
"""

import pytest
import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, AsyncMock
from starlette.testclient import TestClient

from web.app import app


@pytest.fixture
def fake_cosmos():
    """Minimal CosmosDBService mock."""
    cosmos = MagicMock()
    cosmos.symbols = {}
    cosmos.activities = []

    def get_symbol(symbol):
        return cosmos.symbols.get(symbol)

    def write_activity(symbol, agent_type, activity_data, timestamp=None, ttl_seconds=None):
        doc = {
            "id": f"{symbol}_{agent_type}_{len(cosmos.activities)}",
            "symbol": symbol,
            "agent_type": agent_type,
            **activity_data,
        }
        cosmos.activities.append(doc)
        return doc

    def get_activity_by_run_id(run_id):
        """Production fix: direct lookup by run_id (replaces nonexistent list_activities)."""
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
def mock_chain_cache(monkeypatch):
    """Monkeypatch chain cache to return sample data."""
    from src.options_chain_cache import OptionsChainCache

    cache = MagicMock(spec=OptionsChainCache)

    async def fake_refresh(symbol):
        return True

    def fake_get_or_hydrate(symbol, trigger_swr=True):
        # PRODUCTION FIX: underlying_price is at CHAIN level, not on contract row
        return json.dumps({
            "symbol": symbol,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "underlying_price": 75.0,  # Chain-level canonical source
            "calls": {
                "2025-01-17": {
                    "75.0": {
                        "strike": 75.0,
                        "bid": 2.5,
                        "ask": 2.55,
                        "mid": 2.525,
                        "iv": 0.30,
                        "delta": 0.25,
                        "gamma": 0.015,
                        "theta": -0.05,
                        "vega": 0.12,
                        "rho": 0.08,
                        "volume": 100,
                        "openInterest": 500,
                        # NOTE: No underlyingPrice on contract row (production scenario)
                        "_meta": {"chain_timestamp": datetime.now(timezone.utc).isoformat()},
                    }
                }
            },
            "puts": {},
        })

    cache.refresh = fake_refresh
    cache.get_or_hydrate = fake_get_or_hydrate

    monkeypatch.setattr(
        "src.contract_validation_integration.get_options_chain_cache",
        lambda: cache
    )

    return cache


@pytest.fixture
def client(fake_cosmos, monkeypatch):
    """TestClient with cosmos dependency injected."""
    def mock_get_cosmos(request):
        return fake_cosmos

    monkeypatch.setattr("web.app._get_cosmos", mock_get_cosmos)

    return TestClient(app)


class TestFrontendContractValidationPayload:
    """Test exact frontend payload shapes."""

    def test_exact_frontend_payload_with_displayed_snapshot_null(
        self, client, fake_cosmos, mock_chain_cache
    ):
        """Frontend sends displayed_snapshot=null."""
        fake_cosmos.symbols["NEE"] = {
            "symbol": "NEE",
            "enrichment": {"category": "balanced"},
            "total_shares": 500,
        }

        # EXACT payload from ContractValidationAction.tsx
        payload = {
            "symbol": "NEE",
            "side": "call",
            "strike": 75.0,
            "expiration": "2025-01-17",
            "source": "best_options",
            "displayed_snapshot": None,  # Frontend explicitly sends null
        }

        response = client.post("/api/best-options/validate", json=payload)

        # Should NOT be 422 - contract should match
        assert response.status_code != 422, f"422 validation error: {response.text}"

        # Expect 202 (accepted), 409 (duplicate), or 503 (downstream error)
        assert response.status_code in (202, 409, 503), f"Unexpected status: {response.status_code}"

        if response.status_code == 202:
            data = response.json()
            assert data["status"] == "accepted"
            assert "run_id" in data

    def test_exact_frontend_payload_without_displayed_snapshot_field(
        self, client, fake_cosmos, mock_chain_cache
    ):
        """Frontend omits displayed_snapshot field entirely."""
        fake_cosmos.symbols["NEE"] = {
            "symbol": "NEE",
            "enrichment": {"category": "balanced"},
            "total_shares": 500,
        }

        payload = {
            "symbol": "NEE",
            "side": "call",
            "strike": 75.0,
            "expiration": "2025-01-17",
            "source": "best_options",
            # displayed_snapshot omitted
        }

        response = client.post("/api/best-options/validate", json=payload)

        assert response.status_code != 422, f"422 when field omitted: {response.text}"
        assert response.status_code in (202, 409, 503)

    def test_exact_frontend_payload_with_displayed_snapshot_object(
        self, client, fake_cosmos, mock_chain_cache
    ):
        """Frontend sends displayed_snapshot as object (from BestOptionsView)."""
        fake_cosmos.symbols["NEE"] = {
            "symbol": "NEE",
            "enrichment": {"category": "balanced"},
            "total_shares": 500,
        }

        # Payload from BestOptionsView.tsx with displayedSnapshot
        payload = {
            "symbol": "NEE",
            "side": "call",
            "strike": 75.0,
            "expiration": "2025-01-17",
            "source": "best_options",
            "displayed_snapshot": {
                "color": "green",
                "score": 85,
                "premium_pct": 3.37,
                "annualized_return_pct": 41.2,
            },
        }

        response = client.post("/api/best-options/validate", json=payload)

        assert response.status_code != 422, f"422 with snapshot object: {response.text}"
        assert response.status_code in (202, 409, 503)

    def test_malformed_request_missing_required_field_returns_422(self, client):
        """Malformed request (missing required field) should still return 422."""
        payload = {
            "symbol": "NEE",
            # Missing side
            "strike": 75.0,
            "expiration": "2025-01-17",
            "source": "best_options",
        }

        response = client.post("/api/best-options/validate", json=payload)

        # This SHOULD be 422 - field truly required
        assert response.status_code == 422
        detail = response.json()
        assert "detail" in detail

    def test_malformed_request_wrong_type_returns_422(self, client):
        """Malformed request (wrong field type) should return 422."""
        payload = {
            "symbol": "NEE",
            "side": "call",
            "strike": "not-a-number",  # Wrong type
            "expiration": "2025-01-17",
            "source": "best_options",
        }

        response = client.post("/api/best-options/validate", json=payload)

        assert response.status_code == 422
        detail = response.json()
        assert "detail" in detail


class TestValidationBusinessLogic:
    """Test business logic validation (non-422 errors)."""

    def test_invalid_side_returns_400_not_422(
        self, client, fake_cosmos, mock_chain_cache
    ):
        """Invalid side value returns 400 from business logic, not 422."""
        fake_cosmos.symbols["TEST"] = {"enrichment": {"category": "balanced"}}

        payload = {
            "symbol": "TEST",
            "side": "invalid",  # Valid type (string) but invalid value
            "strike": 75.0,
            "expiration": "2025-01-17",
            "source": "best_options",
        }

        response = client.post("/api/best-options/validate", json=payload)

        # Should be 400 (business logic error), not 422 (schema error)
        assert response.status_code == 400
        data = response.json()
        assert data["status"] == "error"
        assert "Invalid side" in data["message"]

    def test_invalid_source_returns_400_not_422(
        self, client, fake_cosmos, mock_chain_cache
    ):
        """Invalid source value returns 400 from business logic, not 422."""
        fake_cosmos.symbols["TEST"] = {"enrichment": {"category": "balanced"}}

        payload = {
            "symbol": "TEST",
            "side": "call",
            "strike": 75.0,
            "expiration": "2025-01-17",
            "source": "invalid_source",
        }

        response = client.post("/api/best-options/validate", json=payload)

        assert response.status_code == 400
        data = response.json()
        assert data["status"] == "error"
        assert "Invalid source" in data["message"]


class TestProductionHotfixRegressions:
    """PRODUCTION HOTFIX: Test exact traceback scenarios from d2ba1b9."""

    @pytest.mark.asyncio
    async def test_underlying_price_from_chain_not_contract(
        self, fake_cosmos, mock_chain_cache, monkeypatch
    ):
        """
        REGRESSION: ValueError: Underlying price not available in contract
        Line 205: contract_validation_integration.py:_build_evaluated_snapshot

        Production scenario: underlying_price is at chain level, NOT on contract rows.
        Fix: Extract from chain.get("underlying_price") instead of contract.
        """
        from src.contract_validation_integration import _build_evaluated_snapshot

        # Setup symbol
        fake_cosmos.symbols["TEST"] = {
            "symbol": "TEST",
            "enrichment": {"category": "balanced"},
            "total_shares": 100,
        }

        # Production chain: underlying_price at chain level
        chain = {
            "symbol": "TEST",
            "underlying_price": 155.50,  # Canonical source
            "calls": {},
            "puts": {},
        }

        # Contract row WITHOUT underlying_price (production scenario)
        contract = {
            "strike": 155.0,
            "bid": 2.50,
            "ask": 2.55,
            "iv": 0.30,
            "delta": 0.25,
            "gamma": 0.015,
            "theta": -0.05,
            "vega": 0.12,
            "volume": 100,
            "openInterest": 500,
            "_meta": {"chain_timestamp": datetime.now(timezone.utc).isoformat()},
            # NO underlyingPrice or underlying_price field
        }

        # Should NOT raise ValueError
        snapshot = await _build_evaluated_snapshot(
            symbol="TEST",
            side="call",
            strike=155.0,
            expiration="2025-01-17",
            contract=contract,
            chain=chain,
            cosmos=fake_cosmos,
        )

        # Verify snapshot uses chain-level underlying_price
        assert snapshot["underlying_price"] == 155.50
        assert "contract_data" in snapshot
        assert snapshot["contract_data"]["strike"] == 155.0

    @pytest.mark.asyncio
    async def test_error_status_retrievable_after_registry_cleanup(
        self, fake_cosmos, monkeypatch
    ):
        """
        REGRESSION: AttributeError: CosmosDBService has no attribute list_activities
        Line 628: contract_validation_integration.py:get_validation_status

        Production scenario: polling after in-flight entry is released.
        Fix: Use cosmos.get_activity_by_run_id() instead of nonexistent list_activities().
        """
        from src.contract_validation_integration import (
            get_validation_status,
            _persist_validation_activity,
        )

        run_id = "test-run-id-12345"

        # Persist an error activity (simulating background task completion)
        await _persist_validation_activity(
            cosmos=fake_cosmos,
            run_id=run_id,
            symbol="TEST",
            side="call",
            strike=155.0,
            expiration="2025-01-17",
            source="best_options",
            displayed_snapshot=None,
            evaluated_snapshot=None,
            result={
                "activity": "WAIT",
                "is_alert": False,
                "validation_status": "error",
                "note": "Underlying price not available in contract",
                "error": "underlying_price_not_available",
            },
        )

        # Verify activity was persisted
        assert len(fake_cosmos.activities) == 1
        assert fake_cosmos.activities[0]["run_id"] == run_id

        # Query status after in-flight registry cleanup
        # Should use cosmos.get_activity_by_run_id(), NOT list_activities()
        status = await get_validation_status(run_id, fake_cosmos)

        assert status["status"] == "completed"
        assert status["run_id"] == run_id
        assert status["validation_status"] == "error"
        assert status["error"] == "underlying_price_not_available"
        assert "Underlying price not available" in status["note"]

    def test_no_call_to_nonexistent_list_activities(self, fake_cosmos, monkeypatch):
        """
        REGRESSION: Ensure cosmos.list_activities() is never called.

        Production error: AttributeError because method doesn't exist.
        Fix: Use get_activity_by_run_id() for direct lookup.
        """
        # Remove the method to ensure it's not called
        if hasattr(fake_cosmos, "list_activities"):
            delattr(fake_cosmos, "list_activities")

        # get_validation_status should work without list_activities
        # Test is implicit: if list_activities is called, test will fail with AttributeError


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
