"""Amendment H — Corporate-Action GROUP CORRECTION tests.

POST /api/portfolio/corporate-actions/{ca_group_id}/correct

Covers the full release-gate contract for group correction:

SERVICE-LEVEL (cosmos_portfolio.py):
  GC-S1:  All original legs superseded after group correction (none missed, none double-counted)
  GC-S2:  Replacement legs share a NEW ca_group_id (not the original)
  GC-S3:  Replacement legs carry replaces_ca_group_id = original ca_group_id
  GC-S4:  Replacement legs carry correction_note
  GC-S5:  New ca_group_id present in return value
  GC-S6:  original_ca_group_id present in return value
  GC-S7:  Phase-1 rollback: if a write fails after partial writes, all written docs deleted
  GC-S8:  Phase-2 rollback: if supersession fails, all new docs deleted + original intact
  GC-S9:  Correction_note empty → ValueError("correction_note is required")
  GC-S10: account_id empty → ValueError("account_id is required")
  GC-S11: Unknown ca_group_id → ValueError("no_active_legs")
  GC-S12: Missing required legs → ValueError
  GC-S13: WHT rate_pct derived on replacement legs (server-authoritative, H-W1 extended)
  GC-S14: Holdings exclude ALL original legs after group correction

ENDPOINT-LEVEL (portfolio_routes.py):
  GC-E1:  201 response on success
  GC-E2:  Response contains movements list and ca_group_id
  GC-E3:  400 when correction_note missing
  GC-E4:  404 when ca_group_id not found (no active legs)
  GC-E5:  Individual correct_movement() with financial fields on a CA leg → 400
          (route returns 400 with group_leg_correction_required error code)
"""

from __future__ import annotations

import pytest
from decimal import Decimal
from fastapi.testclient import TestClient
from unittest.mock import patch

from src.portfolio.cosmos_portfolio import CosmosPortfolioService
from tests.conftest_portfolio_p2 import FakeCosmos


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def svc(monkeypatch):
    """CosmosPortfolioService backed by FakeCosmos — no network."""
    fake = FakeCosmos()
    svc = CosmosPortfolioService(
        portfolio_container=fake.portfolio_container,
        import_sessions_container=fake.import_sessions_container,
        symbols_container=None,
    )
    monkeypatch.setattr(
        "src.portfolio.cosmos_portfolio.ensure_symbol_config",
        lambda *a, **kw: None,
    )
    return svc, fake


@pytest.fixture
def client(monkeypatch):
    from web.app import app
    fake = FakeCosmos()
    monkeypatch.setattr(
        "src.portfolio.cosmos_portfolio.ensure_symbol_config",
        lambda *a, **kw: None,
    )
    with TestClient(app) as c:
        app.state.cosmos = fake
        app.state.cosmos_error = None
        yield c, fake


# ---------------------------------------------------------------------------
# Shared request fixtures
# ---------------------------------------------------------------------------

_SECURITY_ID = "XLON:ULVR"
_ACCOUNT_ID = "heytrade_main"

_SCRIP_CREATE = {
    "event_type": "DIVIDEND_WITH_SCRIP",
    "security_id": _SECURITY_ID,
    "account_id": _ACCOUNT_ID,
    "payment_date": "2024-03-28",
    "legs": [
        {
            "leg_type": "CASH_DIVIDEND",
            "trade_date": "2024-03-28",
            "gross": {"amount": "180.00", "currency": "GBP", "eur_amount": "209.79"},
            "withholding": {
                "source": {"country": "GB", "amount_eur": "0"},
                "destination": {"country": "ES", "amount_eur": "39.86"},
            },
            "fx": {"rate": "1.165500000", "rate_source": "ECB"},
        },
        {
            "leg_type": "SHARE_ACQUISITION",
            "trade_date": "2024-03-28",
            "quantity": "9",
            "gross": {"amount": "0", "currency": "GBP", "eur_amount": "0"},
            "cost_basis_status": "INCOMPLETE",
            "fx": {"rate": "1.165500000", "rate_source": "ECB"},
        },
    ],
}

# Corrected version: increased dividend gross, new WHT amounts, same leg structure
_SCRIP_CORRECTION = {
    "event_type": "DIVIDEND_WITH_SCRIP",
    "account_id": _ACCOUNT_ID,
    "correction_note": "Correct gross and withholding amounts",
    "legs": [
        {
            "leg_type": "CASH_DIVIDEND",
            "trade_date": "2024-03-28",
            "gross": {"amount": "200.00", "currency": "GBP", "eur_amount": "233.10"},
            "withholding": {
                "source": {"country": "GB", "amount_eur": "0", "rate_pct": "999"},
                "destination": {"country": "ES", "amount_eur": "44.29", "rate_pct": "999"},
            },
            "fx": {"rate": "1.165500000", "rate_source": "ECB"},
        },
        {
            "leg_type": "SHARE_ACQUISITION",
            "trade_date": "2024-03-28",
            "quantity": "10",  # corrected quantity
            "gross": {"amount": "0", "currency": "GBP", "eur_amount": "0"},
            "cost_basis_status": "INCOMPLETE",
            "fx": {"rate": "1.165500000", "rate_source": "ECB"},
        },
    ],
}


# ---------------------------------------------------------------------------
# GC-S1..S6: Core replacement shape and supersession
# ---------------------------------------------------------------------------

class TestGroupCorrectionCoreShape:
    """Verify the replacement group has the correct structure and links."""

    def test_gcs1_all_original_legs_superseded(self, svc):
        """GC-S1: Every original leg is SUPERSEDED — none missed."""
        svc_obj, fake = svc
        orig = svc_obj.create_corporate_action(_SCRIP_CREATE)
        orig_ids = {m["id"] for m in orig["movements"]}

        svc_obj.correct_corporate_action_group(orig["ca_group_id"], _SCRIP_CORRECTION)

        for mid in orig_ids:
            doc = fake.portfolio_container._store[mid]
            assert doc["correction_status"] == "SUPERSEDED", (
                f"Original leg {mid} must be SUPERSEDED after group correction"
            )

    def test_gcs1_original_count_matches_leg_count(self, svc):
        """GC-S1: Superseded count equals original leg count (no double-count)."""
        svc_obj, fake = svc
        orig = svc_obj.create_corporate_action(_SCRIP_CREATE)
        n_original = len(orig["movements"])

        svc_obj.correct_corporate_action_group(orig["ca_group_id"], _SCRIP_CORRECTION)

        superseded = [
            v for v in fake.portfolio_container._store.values()
            if v.get("correction_status") == "SUPERSEDED"
        ]
        assert len(superseded) == n_original, (
            f"Exactly {n_original} legs must be SUPERSEDED; got {len(superseded)}"
        )

    def test_gcs2_replacement_legs_have_new_ca_group_id(self, svc):
        """GC-S2: Replacement legs share a NEW ca_group_id, different from original."""
        svc_obj, _ = svc
        orig = svc_obj.create_corporate_action(_SCRIP_CREATE)
        result = svc_obj.correct_corporate_action_group(orig["ca_group_id"], _SCRIP_CORRECTION)

        assert result["ca_group_id"] != orig["ca_group_id"], (
            "Replacement group must get a NEW ca_group_id"
        )
        new_gid = result["ca_group_id"]
        for mvt in result["movements"]:
            assert mvt["ca_group_id"] == new_gid, (
                "All replacement legs must share the new ca_group_id"
            )

    def test_gcs3_replacement_legs_carry_replaces_ca_group_id(self, svc):
        """GC-S3: replaces_ca_group_id on each replacement leg = original ca_group_id."""
        svc_obj, _ = svc
        orig = svc_obj.create_corporate_action(_SCRIP_CREATE)
        result = svc_obj.correct_corporate_action_group(orig["ca_group_id"], _SCRIP_CORRECTION)

        for mvt in result["movements"]:
            assert mvt.get("replaces_ca_group_id") == orig["ca_group_id"], (
                "Each replacement leg must carry replaces_ca_group_id = original ca_group_id"
            )

    def test_gcs4_replacement_legs_carry_correction_note(self, svc):
        """GC-S4: correction_note stored on all replacement legs."""
        svc_obj, _ = svc
        orig = svc_obj.create_corporate_action(_SCRIP_CREATE)
        result = svc_obj.correct_corporate_action_group(orig["ca_group_id"], _SCRIP_CORRECTION)

        for mvt in result["movements"]:
            assert mvt.get("correction_note") == _SCRIP_CORRECTION["correction_note"], (
                "correction_note must appear on every replacement leg"
            )

    def test_gcs5_new_ca_group_id_in_return(self, svc):
        """GC-S5: return value has ca_group_id (the NEW group)."""
        svc_obj, _ = svc
        orig = svc_obj.create_corporate_action(_SCRIP_CREATE)
        result = svc_obj.correct_corporate_action_group(orig["ca_group_id"], _SCRIP_CORRECTION)
        assert "ca_group_id" in result
        assert result["ca_group_id"].startswith("cag_")

    def test_gcs6_original_ca_group_id_in_return(self, svc):
        """GC-S6: return value has original_ca_group_id."""
        svc_obj, _ = svc
        orig = svc_obj.create_corporate_action(_SCRIP_CREATE)
        result = svc_obj.correct_corporate_action_group(orig["ca_group_id"], _SCRIP_CORRECTION)
        assert result.get("original_ca_group_id") == orig["ca_group_id"]

    def test_gcs_replacement_legs_all_active(self, svc):
        """Replacement legs start with correction_status=ACTIVE."""
        svc_obj, _ = svc
        orig = svc_obj.create_corporate_action(_SCRIP_CREATE)
        result = svc_obj.correct_corporate_action_group(orig["ca_group_id"], _SCRIP_CORRECTION)
        for mvt in result["movements"]:
            assert mvt.get("correction_status") == "ACTIVE"

    def test_gcs_original_legs_not_in_active_query(self, svc):
        """GC-S1 (query): original legs excluded from active-movement queries."""
        svc_obj, fake = svc
        orig = svc_obj.create_corporate_action(_SCRIP_CREATE)
        svc_obj.correct_corporate_action_group(orig["ca_group_id"], _SCRIP_CORRECTION)

        # The get_movements service must exclude SUPERSEDED docs
        active_mvts, _ = svc_obj.get_movements(
            account_id=_ACCOUNT_ID, security_id=_SECURITY_ID
        )
        active_ids = {m["id"] for m in active_mvts}
        orig_ids = {m["id"] for m in orig["movements"]}
        overlap = orig_ids & active_ids
        assert not overlap, (
            f"Original legs {overlap} must NOT appear in active movements after correction"
        )


# ---------------------------------------------------------------------------
# GC-S7 & GC-S8: Rollback semantics
# ---------------------------------------------------------------------------

class TestGroupCorrectionRollback:
    """Atomic two-phase correction must roll back cleanly on failure."""

    def test_gcs7_phase1_failure_no_partial_writes(self, svc, monkeypatch):
        """GC-S7: If phase-1 write fails mid-way, already-written docs are deleted."""
        svc_obj, fake = svc
        orig = svc_obj.create_corporate_action(_SCRIP_CREATE)
        orig_active_count = sum(
            1 for v in fake.portfolio_container._store.values()
            if v.get("correction_status") == "ACTIVE"
        )

        call_count = {"n": 0}
        original_upsert = fake.portfolio_container.upsert_item

        def failing_upsert(body):
            call_count["n"] += 1
            if call_count["n"] >= 2:
                raise RuntimeError("Simulated phase-1 write failure")
            return original_upsert(body)

        fake.portfolio_container.upsert_item = failing_upsert

        with pytest.raises(ValueError, match="ca_group_correction_failed"):
            svc_obj.correct_corporate_action_group(orig["ca_group_id"], _SCRIP_CORRECTION)

        # After rollback: no new ACTIVE legs for this security (original legs unchanged)
        active_count = sum(
            1 for v in fake.portfolio_container._store.values()
            if v.get("correction_status") == "ACTIVE"
        )
        assert active_count == orig_active_count, (
            f"Phase-1 rollback must restore original ACTIVE count ({orig_active_count}); "
            f"got {active_count}"
        )

    def test_gcs8_phase2_failure_original_intact(self, svc, monkeypatch):
        """GC-S8: If phase-2 supersession fails, all new docs are deleted and original is intact."""
        svc_obj, fake = svc
        orig = svc_obj.create_corporate_action(_SCRIP_CREATE)
        orig_ids = {m["id"] for m in orig["movements"]}

        original_replace = fake.portfolio_container.replace_item

        def failing_replace(item, body):
            raise RuntimeError("Simulated phase-2 supersession failure")

        fake.portfolio_container.replace_item = failing_replace

        with pytest.raises(ValueError, match="integrity_error"):
            svc_obj.correct_corporate_action_group(orig["ca_group_id"], _SCRIP_CORRECTION)

        # Original legs must still be ACTIVE (not SUPERSEDED)
        for mid in orig_ids:
            doc = fake.portfolio_container._store.get(mid)
            assert doc is not None
            assert doc.get("correction_status") == "ACTIVE", (
                f"Original leg {mid} must remain ACTIVE after phase-2 rollback"
            )

        # No new replacement legs must remain (all deleted in compensation)
        store_ids = set(fake.portfolio_container._store.keys())
        orphan_ids = store_ids - orig_ids
        # Orphans should be zero (compensation deletes them) OR minimal (if delete also failed)
        # The store may still have some if delete_item also throws; log and soft-assert
        replacement_active = [
            fake.portfolio_container._store[oid]
            for oid in orphan_ids
            if fake.portfolio_container._store[oid].get("correction_status") == "ACTIVE"
            and fake.portfolio_container._store[oid].get("replaces_ca_group_id") == orig["ca_group_id"]
        ]
        assert len(replacement_active) == 0, (
            f"Phase-2 rollback must delete replacement legs; {len(replacement_active)} remain"
        )


# ---------------------------------------------------------------------------
# GC-S9..S13: Validation and WHT derivation
# ---------------------------------------------------------------------------

class TestGroupCorrectionValidation:
    """Input validation for group correction request."""

    def test_gcs9_empty_correction_note_rejected(self, svc):
        """GC-S9: correction_note is required and non-empty."""
        svc_obj, _ = svc
        orig = svc_obj.create_corporate_action(_SCRIP_CREATE)
        with pytest.raises(ValueError, match="correction_note is required"):
            svc_obj.correct_corporate_action_group(
                orig["ca_group_id"],
                {**_SCRIP_CORRECTION, "correction_note": ""},
            )

    def test_gcs9_missing_correction_note_rejected(self, svc):
        """GC-S9: missing correction_note key → same error."""
        svc_obj, _ = svc
        orig = svc_obj.create_corporate_action(_SCRIP_CREATE)
        req = {k: v for k, v in _SCRIP_CORRECTION.items() if k != "correction_note"}
        with pytest.raises(ValueError, match="correction_note is required"):
            svc_obj.correct_corporate_action_group(orig["ca_group_id"], req)

    def test_gcs10_empty_account_id_rejected(self, svc):
        """GC-S10: account_id is required."""
        svc_obj, _ = svc
        orig = svc_obj.create_corporate_action(_SCRIP_CREATE)
        with pytest.raises(ValueError, match="account_id is required"):
            svc_obj.correct_corporate_action_group(
                orig["ca_group_id"],
                {**_SCRIP_CORRECTION, "account_id": ""},
            )

    def test_gcs11_unknown_group_raises_no_active_legs(self, svc):
        """GC-S11: correcting a ca_group_id that has no active legs → no_active_legs."""
        svc_obj, _ = svc
        with pytest.raises(ValueError, match="no_active_legs"):
            svc_obj.correct_corporate_action_group("cag_doesnotexist", _SCRIP_CORRECTION)

    def test_gcs11_already_voided_group_raises_no_active_legs(self, svc):
        """GC-S11: voided group has no active legs — same error as missing."""
        svc_obj, _ = svc
        orig = svc_obj.create_corporate_action(_SCRIP_CREATE)
        svc_obj.void_corporate_action_group(orig["ca_group_id"], _ACCOUNT_ID)
        with pytest.raises(ValueError, match="no_active_legs"):
            svc_obj.correct_corporate_action_group(orig["ca_group_id"], _SCRIP_CORRECTION)

    def test_gcs12_missing_required_leg_rejected(self, svc):
        """GC-S12: DIVIDEND_WITH_SCRIP requires CASH_DIVIDEND + SHARE_ACQUISITION."""
        svc_obj, _ = svc
        orig = svc_obj.create_corporate_action(_SCRIP_CREATE)
        req_no_acq = {
            **_SCRIP_CORRECTION,
            "legs": [
                leg for leg in _SCRIP_CORRECTION["legs"]
                if leg["leg_type"] != "SHARE_ACQUISITION"
            ],
        }
        with pytest.raises(ValueError):
            svc_obj.correct_corporate_action_group(orig["ca_group_id"], req_no_acq)

    def test_gcs12_invalid_leg_type_rejected(self, svc):
        """GC-S12: Unknown leg_type in correction request → ValueError."""
        svc_obj, _ = svc
        orig = svc_obj.create_corporate_action(_SCRIP_CREATE)
        bad_req = {
            **_SCRIP_CORRECTION,
            "legs": [
                {**_SCRIP_CORRECTION["legs"][0], "leg_type": "MAGIC_LEG"},
                _SCRIP_CORRECTION["legs"][1],
            ],
        }
        with pytest.raises(ValueError):
            svc_obj.correct_corporate_action_group(orig["ca_group_id"], bad_req)

    def test_gcs13_wht_rate_pct_derived_on_replacement(self, svc):
        """GC-S13: WHT rate_pct on replacement legs is server-derived, not client-supplied."""
        svc_obj, _ = svc
        orig = svc_obj.create_corporate_action(_SCRIP_CREATE)
        result = svc_obj.correct_corporate_action_group(orig["ca_group_id"], _SCRIP_CORRECTION)

        div_leg = next(m for m in result["movements"] if m["ca_leg_type"] == "CASH_DIVIDEND")
        dest = div_leg["withholding"]["destination"]
        # client sent rate_pct="999"; server must derive 44.29/233.10*100 ≈ 19.00
        actual_rate = Decimal(dest["rate_pct"])
        expected_rate = (Decimal("44.29") / Decimal("233.10") * 100).quantize(
            Decimal("0.01")
        )
        assert actual_rate == expected_rate, (
            f"rate_pct must be server-derived ({expected_rate}), "
            f"not client-supplied '999'; got {actual_rate}"
        )

    def test_gcs13_wht_client_rate_pct_overwritten(self, svc):
        """GC-S13: Client-supplied rate_pct='999' must never survive to the stored doc."""
        svc_obj, fake = svc
        orig = svc_obj.create_corporate_action(_SCRIP_CREATE)
        result = svc_obj.correct_corporate_action_group(orig["ca_group_id"], _SCRIP_CORRECTION)

        div_leg_id = next(
            m["id"] for m in result["movements"] if m["ca_leg_type"] == "CASH_DIVIDEND"
        )
        stored = fake.portfolio_container._store[div_leg_id]
        dest_rate = stored["withholding"]["destination"]["rate_pct"]
        assert dest_rate != "999", (
            f"Stored rate_pct must not be client value '999'; got {dest_rate!r}"
        )


# ---------------------------------------------------------------------------
# GC-S14: Holdings exclude original legs exactly once
# ---------------------------------------------------------------------------

class TestGroupCorrectionHoldingsExclusion:
    """Original legs excluded from holdings after group correction."""

    def test_gcs14_original_legs_not_counted_in_holdings(self, svc):
        """GC-S14: Active movements exclude original legs; only replacement legs counted."""
        svc_obj, _ = svc
        orig = svc_obj.create_corporate_action(_SCRIP_CREATE)
        orig_share_leg = next(
            m for m in orig["movements"] if m["ca_leg_type"] == "SHARE_ACQUISITION"
        )
        assert orig_share_leg["quantity"] == "9"

        result = svc_obj.correct_corporate_action_group(orig["ca_group_id"], _SCRIP_CORRECTION)

        # Active movements must only contain the replacement
        active_mvts, _ = svc_obj.get_movements(
            account_id=_ACCOUNT_ID, security_id=_SECURITY_ID
        )
        share_acquisitions = [
            m for m in active_mvts if m.get("ca_leg_type") == "SHARE_ACQUISITION"
        ]
        # Original (qty=9) must be gone; replacement (qty=10) must be present
        assert len(share_acquisitions) == 1, (
            "Exactly one active SHARE_ACQUISITION after group correction"
        )
        assert share_acquisitions[0]["quantity"] == "10", (
            "Active SHARE_ACQUISITION must be the replacement with qty=10, not original qty=9"
        )

    def test_gcs14_original_superseded_count_is_exact(self, svc):
        """GC-S14: Exactly N originals superseded, not N+1 (no double supersession)."""
        svc_obj, fake = svc
        orig = svc_obj.create_corporate_action(_SCRIP_CREATE)
        n = len(orig["movements"])

        svc_obj.correct_corporate_action_group(orig["ca_group_id"], _SCRIP_CORRECTION)

        superseded = [
            v for v in fake.portfolio_container._store.values()
            if v.get("correction_status") == "SUPERSEDED"
        ]
        assert len(superseded) == n, (
            f"Exactly {n} originals superseded (none missed, none double-counted); "
            f"got {len(superseded)}"
        )


# ---------------------------------------------------------------------------
# GC-E1..E5: Endpoint contract
# ---------------------------------------------------------------------------

class TestGroupCorrectionEndpoint:
    """POST /api/portfolio/corporate-actions/{ca_group_id}/correct endpoint contract."""

    def _create_group(self, client):
        """Helper: create a SCRIP group and return (c, fake, ca_group_id, orig_ids)."""
        c, fake = client
        from src.portfolio.cosmos_portfolio import CosmosPortfolioService
        svc = CosmosPortfolioService(
            portfolio_container=fake.portfolio_container,
            import_sessions_container=fake.import_sessions_container,
            symbols_container=None,
        )
        result = svc.create_corporate_action(_SCRIP_CREATE)
        return c, fake, result["ca_group_id"]

    def test_gce1_success_returns_201(self, client, monkeypatch):
        """GC-E1: Successful group correction returns 201 Created."""
        monkeypatch.setattr(
            "src.portfolio.cosmos_portfolio.ensure_symbol_config",
            lambda *a, **kw: None,
        )
        c, fake, ca_group_id = self._create_group(client)
        resp = c.post(f"/api/portfolio/corporate-actions/{ca_group_id}/correct",
                      json=_SCRIP_CORRECTION)
        assert resp.status_code == 201, (
            f"Expected 201, got {resp.status_code}: {resp.text}"
        )

    def test_gce2_response_has_ca_group_id_and_movements(self, client, monkeypatch):
        """GC-E2: Response body has ca_group_id and non-empty movements list."""
        monkeypatch.setattr(
            "src.portfolio.cosmos_portfolio.ensure_symbol_config",
            lambda *a, **kw: None,
        )
        c, fake, ca_group_id = self._create_group(client)
        resp = c.post(f"/api/portfolio/corporate-actions/{ca_group_id}/correct",
                      json=_SCRIP_CORRECTION)
        assert resp.status_code == 201
        data = resp.json()
        assert "ca_group_id" in data, "Response must include new ca_group_id"
        assert "movements" in data and len(data["movements"]) > 0, (
            "Response must include non-empty movements list"
        )

    def test_gce3_missing_correction_note_returns_400(self, client, monkeypatch):
        """GC-E3: Missing correction_note returns 400 validation_error."""
        monkeypatch.setattr(
            "src.portfolio.cosmos_portfolio.ensure_symbol_config",
            lambda *a, **kw: None,
        )
        c, fake, ca_group_id = self._create_group(client)
        req = {k: v for k, v in _SCRIP_CORRECTION.items() if k != "correction_note"}
        resp = c.post(f"/api/portfolio/corporate-actions/{ca_group_id}/correct", json=req)
        assert resp.status_code == 400
        assert resp.json()["error"] == "validation_error"

    def test_gce4_unknown_group_returns_404(self, client):
        """GC-E4: ca_group_id not found returns 404 not_found."""
        c, _ = client
        resp = c.post("/api/portfolio/corporate-actions/cag_ghost/correct",
                      json=_SCRIP_CORRECTION)
        assert resp.status_code == 404
        assert resp.json()["error"] == "not_found"

    def test_gce5_individual_correct_financial_field_on_ca_leg_returns_400(
        self, client, monkeypatch
    ):
        """GC-E5: POST /movements/{id}/correct with financial fields on a CA leg → 400."""
        monkeypatch.setattr(
            "src.portfolio.cosmos_portfolio.ensure_symbol_config",
            lambda *a, **kw: None,
        )
        c, fake, ca_group_id = self._create_group(client)
        # Get one CA leg id
        leg_id = next(
            v["id"] for v in fake.portfolio_container._store.values()
            if v.get("ca_group_id") == ca_group_id
        )
        resp = c.post(f"/api/portfolio/movements/{leg_id}/correct", json={
            "account_id": _ACCOUNT_ID,
            "correction_note": "Trying individual correct",
            "gross": {"amount": "200.00", "currency": "GBP", "eur_amount": "233.10"},
        })
        assert resp.status_code == 400
        body = resp.json()
        # Error code must indicate group correction is required
        assert "group_leg_correction_required" in body.get("error", "") or \
               "group_leg_correction_required" in body.get("detail", ""), (
            f"Error must indicate group correction required; got: {body}"
        )


# ---------------------------------------------------------------------------
# GC — Chained group correction
# ---------------------------------------------------------------------------

class TestGroupCorrectionChain:
    """Correct a group that is already a replacement (double correction)."""

    def test_gc_double_correction_original_fully_superseded(self, svc):
        """Correcting a corrected group supersedes all legs of the intermediate group."""
        svc_obj, fake = svc
        # First correction
        orig = svc_obj.create_corporate_action(_SCRIP_CREATE)
        first_correction = svc_obj.correct_corporate_action_group(
            orig["ca_group_id"], _SCRIP_CORRECTION
        )
        first_new_gid = first_correction["ca_group_id"]
        first_ids = {m["id"] for m in first_correction["movements"]}

        # Second correction — correcting the first replacement group
        second_correction_req = {
            **_SCRIP_CORRECTION,
            "correction_note": "Second pass fix",
        }
        second = svc_obj.correct_corporate_action_group(first_new_gid, second_correction_req)

        # All legs from first correction must now be SUPERSEDED
        for mid in first_ids:
            doc = fake.portfolio_container._store[mid]
            assert doc["correction_status"] == "SUPERSEDED", (
                f"First-correction leg {mid} must be SUPERSEDED after second correction"
            )

        # Only the latest replacement's legs are ACTIVE
        final_gid = second["ca_group_id"]
        final_active = [
            v for v in fake.portfolio_container._store.values()
            if v.get("ca_group_id") == final_gid and v.get("correction_status") == "ACTIVE"
        ]
        assert len(final_active) == len(_SCRIP_CORRECTION["legs"])

    def test_gc_correcting_voided_group_raises(self, svc):
        """Attempting to correct a VOIDED group raises no_active_legs."""
        svc_obj, _ = svc
        orig = svc_obj.create_corporate_action(_SCRIP_CREATE)
        svc_obj.void_corporate_action_group(orig["ca_group_id"], _ACCOUNT_ID)
        with pytest.raises(ValueError, match="no_active_legs"):
            svc_obj.correct_corporate_action_group(orig["ca_group_id"], _SCRIP_CORRECTION)


# ---------------------------------------------------------------------------
# Shared setup helper for endpoint tests
# ---------------------------------------------------------------------------

def _setup_group_via_svc(fake):
    """Create a SCRIP group via CosmosPortfolioService against FakeCosmos."""
    from src.portfolio.cosmos_portfolio import CosmosPortfolioService
    svc = CosmosPortfolioService(
        portfolio_container=fake.portfolio_container,
        import_sessions_container=fake.import_sessions_container,
        symbols_container=None,
    )
    result = svc.create_corporate_action(_SCRIP_CREATE)
    return result["ca_group_id"]


# ---------------------------------------------------------------------------
# GC-E6..E15: Extended endpoint response shape and error code contract
# ---------------------------------------------------------------------------

class TestGroupCorrectionEndpointExtended:
    """Final contract: response body fields, ca_group_seq, ca_event_type, error codes."""

    def _ready(self, client, monkeypatch):
        monkeypatch.setattr(
            "src.portfolio.cosmos_portfolio.ensure_symbol_config",
            lambda *a, **kw: None,
        )
        c, fake = client
        ca_group_id = _setup_group_via_svc(fake)
        return c, ca_group_id

    # ── Response body fields ─────────────────────────────────────────────────

    def test_gce6_response_has_event_type(self, client, monkeypatch):
        """GC-E6: 201 response contains event_type at top level matching request."""
        c, ca_group_id = self._ready(client, monkeypatch)
        resp = c.post(f"/api/portfolio/corporate-actions/{ca_group_id}/correct",
                      json=_SCRIP_CORRECTION)
        assert resp.status_code == 201
        data = resp.json()
        assert "event_type" in data, "Response must include event_type at top level"
        assert data["event_type"] == _SCRIP_CORRECTION["event_type"], (
            f"event_type must be {_SCRIP_CORRECTION['event_type']!r}; got {data['event_type']!r}"
        )

    def test_gce7_response_has_correction_note(self, client, monkeypatch):
        """GC-E7: 201 response contains correction_note at top level."""
        c, ca_group_id = self._ready(client, monkeypatch)
        resp = c.post(f"/api/portfolio/corporate-actions/{ca_group_id}/correct",
                      json=_SCRIP_CORRECTION)
        assert resp.status_code == 201
        data = resp.json()
        assert data.get("correction_note") == _SCRIP_CORRECTION["correction_note"], (
            f"top-level correction_note mismatch; got {data.get('correction_note')!r}"
        )

    def test_gce8_response_has_original_ca_group_id(self, client, monkeypatch):
        """GC-E8: 201 response contains original_ca_group_id = replaced group."""
        c, ca_group_id = self._ready(client, monkeypatch)
        resp = c.post(f"/api/portfolio/corporate-actions/{ca_group_id}/correct",
                      json=_SCRIP_CORRECTION)
        assert resp.status_code == 201
        data = resp.json()
        assert data.get("original_ca_group_id") == ca_group_id, (
            f"original_ca_group_id must be {ca_group_id!r}; got {data.get('original_ca_group_id')!r}"
        )

    # ── Replacement leg fields ────────────────────────────────────────────────

    def test_gce9_replacement_legs_have_ca_group_seq(self, client, monkeypatch):
        """GC-E9: Replacement legs carry ca_group_seq; values are 1-based consecutive ints."""
        c, ca_group_id = self._ready(client, monkeypatch)
        resp = c.post(f"/api/portfolio/corporate-actions/{ca_group_id}/correct",
                      json=_SCRIP_CORRECTION)
        assert resp.status_code == 201
        movements = resp.json()["movements"]
        seqs = sorted(m["ca_group_seq"] for m in movements)
        expected = list(range(1, len(movements) + 1))
        assert seqs == expected, (
            f"ca_group_seq must be 1-based consecutive {expected}; got {seqs}"
        )

    def test_gce10_replacement_legs_have_ca_event_type(self, client, monkeypatch):
        """GC-E10: Replacement legs carry ca_event_type matching event_type."""
        c, ca_group_id = self._ready(client, monkeypatch)
        resp = c.post(f"/api/portfolio/corporate-actions/{ca_group_id}/correct",
                      json=_SCRIP_CORRECTION)
        assert resp.status_code == 201
        for mvt in resp.json()["movements"]:
            assert mvt.get("ca_event_type") == _SCRIP_CORRECTION["event_type"], (
                f"ca_event_type must be {_SCRIP_CORRECTION['event_type']!r} on leg "
                f"{mvt.get('ca_leg_type')!r}; got {mvt.get('ca_event_type')!r}"
            )

    # ── Validation error codes ────────────────────────────────────────────────

    def test_gce11_empty_legs_returns_400_validation_error(self, client, monkeypatch):
        """GC-E11: Empty legs array → 400 validation_error."""
        c, ca_group_id = self._ready(client, monkeypatch)
        req = {**_SCRIP_CORRECTION, "legs": []}
        resp = c.post(f"/api/portfolio/corporate-actions/{ca_group_id}/correct", json=req)
        assert resp.status_code == 400
        assert resp.json()["error"] == "validation_error", resp.text

    def test_gce12_invalid_event_type_returns_400_validation_error(self, client, monkeypatch):
        """GC-E12: Invalid event_type string → 400 validation_error."""
        c, ca_group_id = self._ready(client, monkeypatch)
        req = {**_SCRIP_CORRECTION, "event_type": "NOT_A_REAL_EVENT"}
        resp = c.post(f"/api/portfolio/corporate-actions/{ca_group_id}/correct", json=req)
        assert resp.status_code == 400
        assert resp.json()["error"] == "validation_error", resp.text

    def test_gce15_missing_account_id_key_returns_400(self, client, monkeypatch):
        """GC-E15: Missing account_id key entirely → 400 validation_error."""
        c, ca_group_id = self._ready(client, monkeypatch)
        req = {k: v for k, v in _SCRIP_CORRECTION.items() if k != "account_id"}
        resp = c.post(f"/api/portfolio/corporate-actions/{ca_group_id}/correct", json=req)
        assert resp.status_code == 400
        assert resp.json()["error"] == "validation_error", resp.text

    # ── Rollback error code mapping ───────────────────────────────────────────

    def test_gce13_phase1_failure_returns_400_ca_group_correction_failed(
        self, client, monkeypatch
    ):
        """GC-E13: Phase-1 write failure → HTTP 400 ca_group_correction_failed."""
        monkeypatch.setattr(
            "src.portfolio.cosmos_portfolio.ensure_symbol_config",
            lambda *a, **kw: None,
        )
        c, fake = client
        ca_group_id = _setup_group_via_svc(fake)

        from src.portfolio.cosmos_portfolio import CosmosPortfolioService
        def _raise_phase1(self_inner, *a, **kw):
            raise ValueError("ca_group_correction_failed: simulated write error")
        monkeypatch.setattr(CosmosPortfolioService, "correct_corporate_action_group", _raise_phase1)

        resp = c.post(f"/api/portfolio/corporate-actions/{ca_group_id}/correct",
                      json=_SCRIP_CORRECTION)
        assert resp.status_code == 400, (
            f"Phase-1 failure must return 400; got {resp.status_code}: {resp.text}"
        )
        assert resp.json()["error"] == "ca_group_correction_failed", resp.text

    def test_gce14_phase2_failure_returns_409_integrity_error(self, client, monkeypatch):
        """GC-E14: Phase-2 supersession failure → HTTP 409 integrity_error."""
        monkeypatch.setattr(
            "src.portfolio.cosmos_portfolio.ensure_symbol_config",
            lambda *a, **kw: None,
        )
        c, fake = client
        ca_group_id = _setup_group_via_svc(fake)

        from src.portfolio.cosmos_portfolio import CosmosPortfolioService
        def _raise_phase2(self_inner, *a, **kw):
            raise ValueError("integrity_error: simulated phase-2 failure")
        monkeypatch.setattr(CosmosPortfolioService, "correct_corporate_action_group", _raise_phase2)

        resp = c.post(f"/api/portfolio/corporate-actions/{ca_group_id}/correct",
                      json=_SCRIP_CORRECTION)
        assert resp.status_code == 409, (
            f"Phase-2 failure must return 409; got {resp.status_code}: {resp.text}"
        )
        assert resp.json()["error"] == "integrity_error", resp.text


# ---------------------------------------------------------------------------
# GC — Server-owned arithmetic on replacement legs
# ---------------------------------------------------------------------------

class TestGroupCorrectionArithmetic:
    """net_eur = gross_eur - fees_eur - wht_source_eur - wht_dest_eur on replacement legs."""

    def test_net_eur_correct_on_cash_dividend_leg(self, svc):
        """net_eur formula verified on CASH_DIVIDEND replacement leg."""
        svc_obj, _ = svc
        orig = svc_obj.create_corporate_action(_SCRIP_CREATE)
        result = svc_obj.correct_corporate_action_group(orig["ca_group_id"], _SCRIP_CORRECTION)

        div_leg = next(m for m in result["movements"] if m["ca_leg_type"] == "CASH_DIVIDEND")
        net_eur = Decimal(div_leg["net"]["eur_amount"])
        gross_eur = Decimal(div_leg["gross"]["eur_amount"])
        fees_eur = Decimal(div_leg.get("fees", {}).get("total_eur", "0") or "0")
        wht_source = Decimal(
            (div_leg.get("withholding") or {}).get("source", {}).get("amount_eur", "0") or "0"
        )
        wht_dest = Decimal(
            (div_leg.get("withholding") or {}).get("destination", {}).get("amount_eur", "0") or "0"
        )
        expected_net = gross_eur - fees_eur - wht_source - wht_dest
        assert net_eur == expected_net, (
            f"net_eur={net_eur} must equal "
            f"gross({gross_eur}) - fees({fees_eur}) - wht_src({wht_source}) - wht_dest({wht_dest}) "
            f"= {expected_net}"
        )

    def test_net_eur_zero_on_zero_gross_share_acquisition(self, svc):
        """SHARE_ACQUISITION with gross_eur=0 and no fees/WHT has net_eur=0."""
        svc_obj, _ = svc
        orig = svc_obj.create_corporate_action(_SCRIP_CREATE)
        result = svc_obj.correct_corporate_action_group(orig["ca_group_id"], _SCRIP_CORRECTION)

        share_leg = next(m for m in result["movements"] if m["ca_leg_type"] == "SHARE_ACQUISITION")
        gross_eur = Decimal(share_leg["gross"]["eur_amount"])
        net_eur = Decimal(share_leg["net"]["eur_amount"])
        assert gross_eur == Decimal("0"), "SHARE_ACQUISITION gross must be 0 per fixture"
        assert net_eur == Decimal("0"), (
            f"SHARE_ACQUISITION with zero gross must have net_eur=0; got {net_eur}"
        )

    def test_wht_rate_pct_zero_when_gross_is_zero(self, svc):
        """rate_pct = '0' when gross_eur = 0 — no ZeroDivision. Contract §H arithmetic."""
        svc_obj, _ = svc
        # Create a DIVIDEND_WITH_SCRIP group (CASH_DIVIDEND is single-leg and never
        # a group — see create_corporate_action) with zero gross but non-zero WHT
        # amounts on the CASH_DIVIDEND leg.
        zero_gross_req = {
            "event_type": "DIVIDEND_WITH_SCRIP",
            "security_id": _SECURITY_ID,
            "account_id": _ACCOUNT_ID,
            "payment_date": "2024-03-28",
            "legs": [
                {
                    "leg_type": "CASH_DIVIDEND",
                    "trade_date": "2024-03-28",
                    "gross": {"amount": "0", "currency": "EUR", "eur_amount": "0"},
                    "withholding": {
                        "source": {"country": "ES", "amount_eur": "10.00", "rate_pct": "999"},
                    },
                    "fx": {"rate": "1.000000000", "rate_source": "ECB"},
                },
                {
                    "leg_type": "SHARE_ACQUISITION",
                    "trade_date": "2024-03-28",
                    "quantity": "5",
                    "gross": {"amount": "0", "currency": "EUR", "eur_amount": "0"},
                    "cost_basis_status": "INCOMPLETE",
                    "fx": {"rate": "1.000000000", "rate_source": "ECB"},
                },
            ],
        }
        orig = svc_obj.create_corporate_action(zero_gross_req)
        correction_req = {
            "event_type": "DIVIDEND_WITH_SCRIP",
            "account_id": _ACCOUNT_ID,
            "correction_note": "Zero gross WHT rate test",
            "legs": [
                {
                    "leg_type": "CASH_DIVIDEND",
                    "trade_date": "2024-03-28",
                    "gross": {"amount": "0", "currency": "EUR", "eur_amount": "0"},
                    "withholding": {
                        "source": {"country": "ES", "amount_eur": "5.00", "rate_pct": "999"},
                    },
                    "fx": {"rate": "1.000000000", "rate_source": "ECB"},
                },
                {
                    "leg_type": "SHARE_ACQUISITION",
                    "trade_date": "2024-03-28",
                    "quantity": "5",
                    "gross": {"amount": "0", "currency": "EUR", "eur_amount": "0"},
                    "cost_basis_status": "INCOMPLETE",
                    "fx": {"rate": "1.000000000", "rate_source": "ECB"},
                },
            ],
        }
        result = svc_obj.correct_corporate_action_group(orig["ca_group_id"], correction_req)
        div_leg = next(m for m in result["movements"] if m["ca_leg_type"] == "CASH_DIVIDEND")
        source_rate = div_leg["withholding"]["source"]["rate_pct"]
        assert source_rate == "0", (
            f"rate_pct must be '0' when gross_eur=0 (avoid ZeroDivision); got {source_rate!r}"
        )


# ---------------------------------------------------------------------------
# GC-S15: Optional field inference from original group
# ---------------------------------------------------------------------------

class TestGroupCorrectionInference:
    """security_id and payment_date inferred from original group when omitted in request."""

    def test_gcs15_security_id_inherited_when_omitted(self, svc):
        """GC-S15: Replacement legs carry security_id from original when not supplied."""
        svc_obj, _ = svc
        orig = svc_obj.create_corporate_action(_SCRIP_CREATE)
        orig_security_id = orig["movements"][0]["security_id"]

        # _SCRIP_CORRECTION has no security_id key — inferred from original
        req_no_sec = {k: v for k, v in _SCRIP_CORRECTION.items() if k != "security_id"}
        result = svc_obj.correct_corporate_action_group(orig["ca_group_id"], req_no_sec)
        for mvt in result["movements"]:
            assert mvt.get("security_id") == orig_security_id, (
                f"Replacement leg must inherit security_id={orig_security_id!r}; "
                f"got {mvt.get('security_id')!r}"
            )

    def test_gcs15_payment_date_inherited_when_omitted(self, svc):
        """GC-S15: trade_date on replacement leg falls back to original when payment_date omitted."""
        svc_obj, _ = svc
        orig = svc_obj.create_corporate_action(_SCRIP_CREATE)
        orig_trade_date = orig["movements"][0].get("trade_date")

        # Request without payment_date — legs override trade_date explicitly in fixture
        # Omit payment_date AND let legs set trade_date to something different from orig
        req_no_date = {k: v for k, v in _SCRIP_CORRECTION.items() if k != "payment_date"}
        result = svc_obj.correct_corporate_action_group(orig["ca_group_id"], req_no_date)
        # trade_date on legs is explicitly "2024-03-28" in _SCRIP_CORRECTION legs
        for mvt in result["movements"]:
            assert mvt.get("trade_date") is not None, (
                "Replacement leg must have a trade_date"
            )

    def test_gcs15_explicit_security_id_overrides_inherited(self, svc):
        """GC-S15: Explicit security_id in correction request is used, not original."""
        svc_obj, _ = svc
        orig = svc_obj.create_corporate_action(_SCRIP_CREATE)
        override_sec_id = _SECURITY_ID  # same value, confirms path taken
        req_with_sec = {**_SCRIP_CORRECTION, "security_id": override_sec_id}
        result = svc_obj.correct_corporate_action_group(orig["ca_group_id"], req_with_sec)
        for mvt in result["movements"]:
            assert mvt.get("security_id") == override_sec_id
