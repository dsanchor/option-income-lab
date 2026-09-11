"""Phase 2 regression tests — Movement correction (supersession chain).

Covers:
- POST /api/portfolio/movements/{id}/correct — creates replacement
- Original movement preserved with correction_status=SUPERSEDED
- Replacement is independent (correction_status=ACTIVE, references original)
- Double-correction blocked (already superseded → 409)
- Superseded movements excluded from holdings / totals
- Correction response shape: {"original": {...}, "replacement": {...}}

Route: POST /api/portfolio/movements/{movement_id}/correct
Body:  {account_id, correction_note, trade_date?, quantity?, gross?, fees?, ...}
"""

from __future__ import annotations

import pytest
from decimal import Decimal
from azure.cosmos.exceptions import CosmosResourceNotFoundError
from fastapi.testclient import TestClient

from src.portfolio.cosmos_portfolio import CosmosPortfolioService
from src.portfolio.cosmos_securities import CosmosSecuritiesService
from src.portfolio.holdings_service import HoldingsService
from tests.conftest_portfolio_p2 import FakeCosmos


@pytest.fixture
def client():
    from web.app import app
    fake = FakeCosmos()
    with TestClient(app) as c:
        app.state.cosmos = fake
        app.state.cosmos_error = None
        yield c, fake


def _seed(fake, mid, txn_type="BUY", quantity="100", gross_eur="18250",
          security_id="XNYS:AAPL", account_id="_unassigned",
          trade_date="2024-01-15", correction_status="ACTIVE",
          commission_eur="7.50"):
    ticker = security_id.split(":")[-1]
    net = str(Decimal(gross_eur) - Decimal(commission_eur))
    doc = {
        "id": mid,
        "doc_type": "ledger_txn",
        "txn_type": txn_type,
        "security_id": security_id,
        "ticker": ticker,
        "trade_date": trade_date,
        "quantity": quantity,
        "gross": {"amount": gross_eur, "currency": "EUR", "eur_amount": gross_eur},
        "fees": {"total": commission_eur, "currency": "EUR", "total_eur": commission_eur},
        "net": {"amount": net, "currency": "EUR", "eur_amount": net},
        "account_id": account_id,
        "correction_status": correction_status,
        "import_source": "manual",
        "created_at": "2026-09-06T10:00:00Z",
        "warnings": [],
    }
    fake.portfolio_container._store[mid] = doc
    return doc


def _correct(c, mid, account_id="_unassigned", note="fixing quantity",
             quantity=None, gross_eur=None, commission_eur=None, trade_date=None,
             extra=None):
    body = {"account_id": account_id, "correction_note": note}
    if quantity is not None:
        body["quantity"] = quantity
    if gross_eur is not None:
        body["gross"] = {"amount": gross_eur, "currency": "EUR", "eur_amount": gross_eur}
    if commission_eur is not None:
        body["fees"] = {"total": commission_eur, "currency": "EUR", "total_eur": commission_eur}
    if trade_date is not None:
        body["trade_date"] = trade_date
    if extra:
        body.update(extra)
    return c.post(f"/api/portfolio/movements/{mid}/correct", json=body)


# ===========================================================================
# Correction — basic response shape
# ===========================================================================

class TestCorrectionResponseShape:
    def test_correct_200(self, client):
        c, fake = client
        _seed(fake, "mvt_corr_001")
        resp = _correct(c, "mvt_corr_001", quantity="95")
        assert resp.status_code == 200

    def test_response_has_original_and_replacement(self, client):
        c, fake = client
        _seed(fake, "mvt_corr_002")
        resp = _correct(c, "mvt_corr_002", quantity="95")
        assert resp.status_code == 200
        data = resp.json()
        assert "original" in data, "Response must have 'original' key"
        assert "replacement" in data, "Response must have 'replacement' key"

    def test_original_is_preserved_in_response(self, client):
        c, fake = client
        _seed(fake, "mvt_corr_003")
        resp = _correct(c, "mvt_corr_003", quantity="80")
        assert resp.status_code == 200
        orig = resp.json()["original"]
        assert orig["id"] == "mvt_corr_003"
        assert orig["quantity"] == "100"  # old quantity preserved in original

    def test_replacement_has_new_values(self, client):
        c, fake = client
        _seed(fake, "mvt_corr_004")
        resp = _correct(c, "mvt_corr_004", quantity="77")
        assert resp.status_code == 200
        repl = resp.json()["replacement"]
        assert repl["quantity"] == "77"
        assert repl["id"] != "mvt_corr_004"  # different ID

    def test_replacement_is_active(self, client):
        c, fake = client
        _seed(fake, "mvt_corr_005")
        resp = _correct(c, "mvt_corr_005", quantity="90")
        assert resp.status_code == 200
        assert resp.json()["replacement"]["correction_status"] == "ACTIVE"


# ===========================================================================
# Correction — invariants
# ===========================================================================

class TestCorrectionInvariants:
    def test_original_marked_superseded_in_store(self, client):
        c, fake = client
        _seed(fake, "mvt_sup_001")
        _correct(c, "mvt_sup_001", quantity="88")
        stored = fake.portfolio_container._store.get("mvt_sup_001", {})
        assert stored.get("correction_status") == "SUPERSEDED", (
            "Original must be SUPERSEDED in Cosmos after correction"
        )

    def test_original_has_superseded_by_pointer(self, client):
        c, fake = client
        _seed(fake, "mvt_sup_002")
        resp = _correct(c, "mvt_sup_002", quantity="88")
        stored = fake.portfolio_container._store.get("mvt_sup_002", {})
        assert "superseded_by" in stored, (
            "Original must have superseded_by pointer to the replacement ID"
        )
        repl_id = resp.json()["replacement"]["id"]
        assert stored["superseded_by"] == repl_id

    def test_replacement_has_corrects_pointer(self, client):
        c, fake = client
        _seed(fake, "mvt_sup_003")
        resp = _correct(c, "mvt_sup_003", quantity="60")
        repl_id = resp.json()["replacement"]["id"]
        stored_repl = fake.portfolio_container._store.get(repl_id, {})
        assert stored_repl.get("corrects_movement_id") == "mvt_sup_003", (
            "Replacement must have corrects_movement_id pointing to the original"
        )

    def test_correction_note_stored_on_replacement(self, client):
        c, fake = client
        _seed(fake, "mvt_note_001")
        _correct(c, "mvt_note_001", note="Incorrect share count, was 100 not 95")
        replacements = [
            v for k, v in fake.portfolio_container._store.items()
            if v.get("corrects_movement_id") == "mvt_note_001"
        ]
        assert len(replacements) >= 1, "Replacement must be stored in Cosmos"
        repl = replacements[0]
        assert "correction_note" in repl, (
            "correction_note must be stored on the replacement document"
        )

    def test_double_correction_409(self, client):
        c, fake = client
        _seed(fake, "mvt_double_001")
        _correct(c, "mvt_double_001", quantity="90")  # First correction — succeeds
        resp = _correct(c, "mvt_double_001", quantity="80")  # Second — already SUPERSEDED → 409
        assert resp.status_code == 409, (
            "Correcting an already-SUPERSEDED movement must return 409"
        )
        data = resp.json()
        assert data["error"] in ("already_superseded", "conflict", "cannot_correct")

    def test_correct_nonexistent_404(self, client):
        c, _ = client
        resp = _correct(c, "mvt_ghost_001")
        assert resp.status_code == 404
        assert resp.json()["error"] == "not_found"

    def test_correct_missing_account_id_400(self, client):
        c, fake = client
        _seed(fake, "mvt_noact_001")
        resp = c.post("/api/portfolio/movements/mvt_noact_001/correct",
                      json={"correction_note": "note"})
        assert resp.status_code == 400
        assert resp.json()["error"] == "validation_error"

    def test_correct_missing_note_400(self, client):
        c, fake = client
        _seed(fake, "mvt_nonote_001")
        resp = c.post("/api/portfolio/movements/mvt_nonote_001/correct",
                      json={"account_id": "_unassigned"})
        assert resp.status_code == 400
        assert resp.json()["error"] == "validation_error"

    def test_option_correction_can_add_option_position_id(self, client):
        c, fake = client
        fake.portfolio_container._store["mvt_opt_corr_001"] = {
            "id": "mvt_opt_corr_001",
            "doc_type": "ledger_txn",
            "txn_type": "CALL_SELL",
            "security_id": "XNYS:AAPL",
            "ticker": "AAPL",
            "trade_date": "2024-07-19",
            "quantity": "0",
            "gross": {"amount": "150.00", "currency": "USD", "eur_amount": "138.00"},
            "fees": {"total": "3.50", "currency": "USD", "total_eur": "3.25"},
            "net": {"amount": "146.500000", "currency": "USD", "eur_amount": "134.750000"},
            "account_id": "_unassigned",
            "correction_status": "ACTIVE",
            "import_source": "manual",
            "created_at": "2026-09-06T10:00:00Z",
            "warnings": [],
            "option_link_kind": "OPEN_SELL",
            "option_type": "call",
            "option_strike": 210.0,
            "option_expiration": "2024-07-19",
            "option_symbol": "AAPL",
        }
        resp = _correct(
            c,
            "mvt_opt_corr_001",
            note="link later",
            extra={"option_position_id": "pos_call_777"},
        )
        assert resp.status_code == 200
        assert resp.json()["replacement"]["option_position_id"] == "pos_call_777"

    def test_option_correction_can_add_option_close_date(self, client):
        c, fake = client
        fake.portfolio_container._store["mvt_opt_corr_002"] = {
            "id": "mvt_opt_corr_002",
            "doc_type": "ledger_txn",
            "txn_type": "CALL_SELL",
            "security_id": "XNYS:AAPL",
            "ticker": "AAPL",
            "trade_date": "2024-07-19",
            "quantity": "0",
            "gross": {"amount": "150.00", "currency": "USD", "eur_amount": "138.00"},
            "fees": {"total": "3.50", "currency": "USD", "total_eur": "3.25"},
            "net": {"amount": "146.500000", "currency": "USD", "eur_amount": "134.750000"},
            "account_id": "_unassigned",
            "correction_status": "ACTIVE",
            "import_source": "manual",
            "created_at": "2026-09-06T10:00:00Z",
            "warnings": [],
            "option_link_kind": "OPEN_SELL",
            "option_type": "call",
            "option_strike": 210.0,
            "option_expiration": "2024-07-19",
            "option_symbol": "AAPL",
        }
        resp = _correct(
            c,
            "mvt_opt_corr_002",
            note="record close date later",
            extra={"option_close_date": "2024-07-26"},
        )
        assert resp.status_code == 200
        assert resp.json()["replacement"]["option_close_date"] == "2024-07-26"


# ===========================================================================
# Correction — holdings / holdings exclusion
# ===========================================================================

class FakePortfolioForHoldings:
    def __init__(self, movements):
        self._movements = list(movements)

    def query_items(self, query="", parameters=None, enable_cross_partition_query=True,
                    partition_key=None):
        results = self._movements
        if "NOT IS_DEFINED(c.deleted_at)" in query:
            results = [m for m in results if "deleted_at" not in m]
        if "(NOT IS_DEFINED(c.correction_status) OR c.correction_status = 'ACTIVE')" in query:
            results = [m for m in results if m.get("correction_status", "ACTIVE") == "ACTIVE"]
        return iter(results)

    def read_item(self, item=None, partition_key=None, **kw):
        for m in self._movements:
            if m.get("id") == item:
                return dict(m)
        raise CosmosResourceNotFoundError(message="nf", response=None)

    def upsert_item(self, b): self._movements.append(b); return b
    def replace_item(self, i, b): return b


class FakeSymbols:
    def query_items(self, **kw): return iter([])
    def read_item(self, *a, **kw): raise CosmosResourceNotFoundError("nf", None)


def _mvt(mid, sid, txn, qty, gross, acct="_unassigned", commission="0",
         trade_date="2024-01-15", correction_status="ACTIVE"):
    ticker = sid.split(":")[-1]
    net = str(Decimal(gross) - Decimal(commission))
    return {
        "id": mid, "doc_type": "ledger_txn", "txn_type": txn,
        "security_id": sid, "ticker": ticker, "trade_date": trade_date,
        "quantity": str(qty),
        "gross": {"amount": gross, "currency": "EUR", "eur_amount": gross},
        "fees": {"total": commission, "currency": "EUR", "total_eur": commission},
        "net": {"amount": net, "currency": "EUR", "eur_amount": net},
        "account_id": acct, "correction_status": correction_status,
        "warnings": [],
    }


class TestCorrectionHoldingsExclusion:
    def test_superseded_movement_excluded_from_shares(self):
        movements = [
            _mvt("b_orig", "XNYS:AAPL", "BUY", 100, "18250", correction_status="SUPERSEDED"),
            _mvt("b_repl", "XNYS:AAPL", "BUY", 90, "16425"),  # replacement
        ]
        svc = HoldingsService(
            CosmosPortfolioService(FakePortfolioForHoldings(movements), None),
            CosmosSecuritiesService(FakeSymbols()),
        )
        result = svc.compute_holdings()
        aapl = next((h for h in result["holdings"] if h["security_id"] == "XNYS:AAPL"), None)
        assert aapl is not None
        assert Decimal(aapl["total_shares"]) == Decimal("90"), (
            "SUPERSEDED movement must be excluded; only replacement counted"
        )

    def test_superseded_buy_excluded_from_total_purchases(self):
        movements = [
            _mvt("b_sup1", "XNYS:AAPL", "BUY", 100, "18250", correction_status="SUPERSEDED"),
            _mvt("b_act1", "XNYS:AAPL", "BUY", 90, "16425"),
        ]
        svc = HoldingsService(
            CosmosPortfolioService(FakePortfolioForHoldings(movements), None),
            CosmosSecuritiesService(FakeSymbols()),
        )
        result = svc.compute_holdings()
        aapl = next(h for h in result["holdings"] if h["security_id"] == "XNYS:AAPL")
        assert Decimal(aapl["total_purchases_eur"]) == Decimal("16425.00"), (
            "Superseded BUY gross must not appear in total_purchases_eur"
        )

    def test_replacement_increments_correctly(self):
        movements = [
            _mvt("b_s1", "XMAD:SAN", "BUY", 500, "2250", correction_status="SUPERSEDED"),
            _mvt("b_r1", "XMAD:SAN", "BUY", 480, "2160"),
        ]
        svc = HoldingsService(
            CosmosPortfolioService(FakePortfolioForHoldings(movements), None),
            CosmosSecuritiesService(FakeSymbols()),
        )
        result = svc.compute_holdings()
        san = next(h for h in result["holdings"] if h["security_id"] == "XMAD:SAN")
        assert Decimal(san["total_shares"]) == Decimal("480")


# ===========================================================================
# write_ledger_txn — safety guard against restoring voided/superseded movements
# ===========================================================================

class FakePortfolioForWriteGuard:
    """Minimal fake that supports upsert_item and read_item for the write guard tests."""
    def __init__(self, initial_docs=None):
        self._store: dict = {}
        for d in (initial_docs or []):
            self._store[d["id"]] = dict(d)

    def upsert_item(self, body):
        self._store[body["id"]] = dict(body)
        return dict(body)

    def delete_item(self, item=None, partition_key=None, **kw):
        self._store.pop(item, None)

    def read_item(self, item=None, partition_key=None, **kw):
        key = item
        if key not in self._store:
            raise CosmosResourceNotFoundError(message="not found", response=None)
        doc = self._store[key]
        if partition_key is not None and doc.get("account_id") != partition_key:
            raise CosmosResourceNotFoundError(message="not found", response=None)
        return dict(doc)

    def read(self): return {}

    def query_items(self, **kw): return iter([])
    def replace_item(self, i, b): return b
    def create_item(self, b): self._store[b["id"]] = b; return b


def _make_svc(initial_docs=None):
    fake_container = FakePortfolioForWriteGuard(initial_docs)
    return CosmosPortfolioService(fake_container, None), fake_container


class TestWriteLedgerTxnSafetyGuard:
    """write_ledger_txn self-heals stale VOIDED/SUPERSEDED tombstones on
    re-import: it purges the orphaned chain and writes the incoming doc
    fresh, rather than silently restoring the old doc to active in place."""

    def test_new_document_upserted_normally(self):
        svc, container = _make_svc()
        doc = {
            "id": "mvt_new_001", "account_id": "_unassigned",
            "doc_type": "ledger_txn", "txn_type": "BUY",
        }
        result = svc.write_ledger_txn(doc)
        assert result["id"] == "mvt_new_001"
        assert "mvt_new_001" in container._store

    def test_active_document_upserted_normally(self):
        existing = {
            "id": "mvt_active_001", "account_id": "_unassigned",
            "doc_type": "ledger_txn", "txn_type": "BUY",
            "correction_status": "ACTIVE",
        }
        svc, container = _make_svc([existing])
        new_doc = {**existing, "quantity": "200"}
        result = svc.write_ledger_txn(new_doc)
        assert result["id"] == "mvt_active_001"
        assert container._store["mvt_active_001"]["quantity"] == "200"

    def test_voided_document_purged_and_overwritten(self):
        """Re-import of a lone VOIDED tombstone (no chain links) purges it
        and writes the incoming doc in its place instead of raising."""
        voided = {
            "id": "mvt_voided_001", "account_id": "_unassigned",
            "doc_type": "ledger_txn", "txn_type": "BUY",
            "correction_status": "VOIDED",
        }
        svc, container = _make_svc([voided])
        incoming = {
            "id": "mvt_voided_001", "account_id": "_unassigned",
            "doc_type": "ledger_txn", "txn_type": "BUY",
        }
        result = svc.write_ledger_txn(incoming)
        assert result["id"] == "mvt_voided_001"
        assert container._store["mvt_voided_001"] == incoming

    def test_superseded_document_purged_and_overwritten(self):
        """Re-import of a lone SUPERSEDED tombstone (no chain links) purges
        it and writes the incoming doc in its place instead of raising."""
        superseded = {
            "id": "mvt_sup_guard_001", "account_id": "_unassigned",
            "doc_type": "ledger_txn", "txn_type": "SELL",
            "correction_status": "SUPERSEDED",
        }
        svc, container = _make_svc([superseded])
        incoming = {
            "id": "mvt_sup_guard_001", "account_id": "_unassigned",
            "doc_type": "ledger_txn", "txn_type": "SELL",
        }
        result = svc.write_ledger_txn(incoming)
        assert result["id"] == "mvt_sup_guard_001"
        assert container._store["mvt_sup_guard_001"] == incoming

    def test_superseded_chain_purged_before_overwrite(self):
        """When the SUPERSEDED doc has an active successor (superseded_by),
        the whole chain is purged before the incoming doc is written."""
        original = {
            "id": "mvt_sup_chain_001", "account_id": "_unassigned",
            "doc_type": "ledger_txn", "txn_type": "SELL",
            "correction_status": "SUPERSEDED",
            "superseded_by": "mvt_active_replacement",
        }
        replacement = {
            "id": "mvt_active_replacement", "account_id": "_unassigned",
            "doc_type": "ledger_txn", "txn_type": "SELL",
            "correction_status": "ACTIVE",
            "corrects_movement_id": "mvt_sup_chain_001",
        }
        svc, container = _make_svc([original, replacement])
        incoming = {
            "id": "mvt_sup_chain_001", "account_id": "_unassigned",
            "doc_type": "ledger_txn", "txn_type": "SELL",
        }
        result = svc.write_ledger_txn(incoming)
        assert result["id"] == "mvt_sup_chain_001"
        assert container._store["mvt_sup_chain_001"] == incoming
        # The old active replacement is gone — chain was fully purged.
        assert "mvt_active_replacement" not in container._store

    def test_superseded_ca_group_member_still_raises(self):
        """A SUPERSEDED doc that belongs to a corporate-action group must
        still refuse the self-heal purge — CA groups require an explicit,
        all-or-nothing purge via delete_corporate_action_group()."""
        superseded = {
            "id": "mvt_sup_ca_001", "account_id": "_unassigned",
            "doc_type": "ledger_txn", "txn_type": "SELL",
            "correction_status": "SUPERSEDED",
            "ca_group_id": "ca_group_xyz",
        }
        svc, container = _make_svc([superseded])
        incoming = {
            "id": "mvt_sup_ca_001", "account_id": "_unassigned",
            "doc_type": "ledger_txn", "txn_type": "SELL",
        }
        with pytest.raises(CosmosPortfolioService.VoidedMovementError):
            svc.write_ledger_txn(incoming)
        assert container._store["mvt_sup_ca_001"]["correction_status"] == "SUPERSEDED"

    def test_document_without_account_id_skips_guard(self):
        """If account_id is missing, the guard cannot query Cosmos — proceed with upsert."""
        svc, container = _make_svc()
        doc = {"id": "mvt_no_acct_001", "doc_type": "ledger_txn"}
        # Should not raise — guard skipped when account_id is absent
        result = svc.write_ledger_txn(doc)
        assert result["id"] == "mvt_no_acct_001"


# ===========================================================================
# C-1 through C-15: Full-correction field matrix (Danny contract §F.1)
# ===========================================================================

def _seed_full(fake, mid, txn_type="BUY", quantity="100", gross_eur="18250",
               commission_eur="7.50", security_id="XNYS:AAPL", account_id="_unassigned",
               trade_date="2024-01-15", withholding=None, sales_type=None,
               cost_basis_status="COMPLETE", notes=None, currency="EUR",
               quantity_override=None):
    """Seed a full movement document for field-matrix tests."""
    ticker = security_id.split(":")[-1]
    qty = quantity_override if quantity_override is not None else quantity
    gross_d = Decimal(gross_eur)
    fees_d = Decimal(commission_eur)
    wht_s = Decimal("0")
    wht_d_val = Decimal("0")
    if isinstance(withholding, dict):
        wht_s = Decimal(str((withholding.get("source") or {}).get("amount_eur", "0")))
        dst = withholding.get("destination")
        if dst is not None:
            wht_d_val = Decimal(str(dst.get("amount_eur", "0")))
    net = gross_d - fees_d - wht_s - wht_d_val
    doc = {
        "id": mid,
        "doc_type": "ledger_txn",
        "txn_type": txn_type,
        "security_id": security_id,
        "ticker": ticker,
        "trade_date": trade_date,
        "quantity": qty,
        "gross": {"amount": gross_eur, "currency": currency, "eur_amount": gross_eur},
        "fees": {"total": commission_eur, "currency": currency, "total_eur": commission_eur},
        "net": {
            "amount": str(net.quantize(Decimal("0.000001"))),
            "currency": currency,
            "eur_amount": str(net.quantize(Decimal("0.000001"))),
        },
        "account_id": account_id,
        "correction_status": "ACTIVE",
        "import_source": "csv_import",
        "created_at": "2026-09-06T10:00:00Z",
        "warnings": [],
    }
    if withholding is not None:
        doc["withholding"] = withholding
    if txn_type == "SELL" and sales_type:
        doc["sales_type"] = sales_type
    if txn_type == "BUY" and cost_basis_status:
        doc["cost_basis_status"] = cost_basis_status
    if notes:
        doc["notes"] = notes
    fake.portfolio_container._store[mid] = doc
    return doc


class TestFullCorrectionFieldMatrix:
    """C-1 through C-13: Field-matrix corrections per Danny contract §F.1."""

    # ── C-1: BUY — gross + fees override with net recompute ──────────────
    def test_c1_buy_gross_fees_override(self, client):
        c, fake = client
        _seed_full(fake, "c1_001", txn_type="BUY",
                   gross_eur="18250", commission_eur="7.50")
        resp = c.post("/api/portfolio/movements/c1_001/correct", json={
            "account_id": "_unassigned",
            "correction_note": "Wrong gross and fees",
            "gross": {"amount": "17000", "currency": "EUR", "eur_amount": "17000"},
            "fees": {"total": "5.00", "currency": "EUR", "total_eur": "5.00"},
        })
        assert resp.status_code == 200, resp.text
        data = resp.json()
        repl = data["replacement"]
        assert repl["gross"]["eur_amount"] == "17000", "gross should be updated"
        assert repl["fees"]["total_eur"] == "5.00", "fees should be updated"
        net_expected = Decimal("17000") + Decimal("5.00")
        assert Decimal(repl["net"]["eur_amount"]) == net_expected, (
            f"net should be recomputed as {net_expected}, got {repl['net']['eur_amount']}"
        )

    # ── C-2: BUY — cost_basis_status INCOMPLETE→COMPLETE ─────────────────
    def test_c2_buy_cost_basis_status_change(self, client):
        c, fake = client
        orig = _seed_full(fake, "c2_001", txn_type="BUY", cost_basis_status="INCOMPLETE")
        orig["cost_basis_status"] = "INCOMPLETE"
        fake.portfolio_container._store["c2_001"] = orig
        resp = c.post("/api/portfolio/movements/c2_001/correct", json={
            "account_id": "_unassigned",
            "correction_note": "Completing cost basis",
            "cost_basis_status": "COMPLETE",
        })
        assert resp.status_code == 200, resp.text
        assert resp.json()["replacement"]["cost_basis_status"] == "COMPLETE"

    # ── C-3: SELL — withholding source added ─────────────────────────────
    def test_c3_sell_withholding_source_added(self, client):
        c, fake = client
        _seed_full(fake, "c3_001", txn_type="SELL", sales_type="ACCIONES",
                   gross_eur="5000", commission_eur="3.00")
        resp = c.post("/api/portfolio/movements/c3_001/correct", json={
            "account_id": "_unassigned",
            "correction_note": "Adding source withholding",
            "withholding": {
                "source": {"country": "US", "rate_pct": "15", "amount_eur": "45.30"},
            },
        })
        assert resp.status_code == 200, resp.text
        repl = resp.json()["replacement"]
        assert repl["withholding"]["source"]["amount_eur"] == "45.30"
        # net = 5000 - 3.00 - 45.30 = 4951.70
        net_expected = Decimal("5000") - Decimal("3.00") - Decimal("45.30")
        assert Decimal(repl["net"]["eur_amount"]) == net_expected

    # ── C-4: SELL — sales_type ACCIONES→DERECHOS ─────────────────────────
    def test_c4_sell_sales_type_acciones_to_derechos(self, client):
        c, fake = client
        _seed_full(fake, "c4_001", txn_type="SELL", sales_type="ACCIONES")
        resp = c.post("/api/portfolio/movements/c4_001/correct", json={
            "account_id": "_unassigned",
            "correction_note": "Was a rights sale not a share sale",
            "sales_type": "DERECHOS",
        })
        assert resp.status_code == 200, resp.text
        assert resp.json()["replacement"]["sales_type"] == "DERECHOS"

    # ─────────────────────────────────────────────────────────────────────
    # C-5 through C-7, C-10, C-12, C-13: DIVIDEND — ⚠️ PENDING AMENDMENT
    # Danny's in-flight amendment redefines the DIVIDEND model:
    #   • Withholding amounts user-entered; rate_pct auto-calculated (not stored as input)
    #   • DIVIDEND may be a linked COMPOSITE corporate action:
    #       residual cash leg + partial rights sale + shares from remaining rights
    #       + optional investor cash top-up to round whole shares
    #   • Multiple linked movements MUST NOT be flattened; quantity/cost must not be fabricated
    # These tests exercise the CURRENT single-movement model (contract v1 §B.4/B.5).
    # Do NOT base new DIVIDEND creation or composite-correction UI on these.
    # Will be revised once the amended contract lands.
    # ─────────────────────────────────────────────────────────────────────

    # ── C-5: DIVIDEND — withholding destination null→value ───────────────
    def test_c5_dividend_wht_dest_null_to_value(self, client):
        c, fake = client
        _seed_full(fake, "c5_001", txn_type="DIVIDEND",
                   gross_eur="300", commission_eur="0",
                   withholding={"source": {"country": "US", "rate_pct": "15", "amount_eur": "45"},
                                "destination": None})
        resp = c.post("/api/portfolio/movements/c5_001/correct", json={
            "account_id": "_unassigned",
            "correction_note": "Adding destination withholding",
            "withholding": {
                "source": {"country": "US", "rate_pct": "15", "amount_eur": "45"},
                "destination": {"country": "ES", "rate_pct": "19", "amount_eur": "57"},
            },
        })
        assert resp.status_code == 200, resp.text
        repl = resp.json()["replacement"]
        assert repl["withholding"]["destination"]["amount_eur"] == "57"
        # net = 300 - 0 - 45 - 57 = 198
        assert Decimal(repl["net"]["eur_amount"]) == Decimal("198")

    # ── C-6: DIVIDEND — withholding destination value→null ───────────────
    def test_c6_dividend_wht_dest_value_to_null(self, client):
        c, fake = client
        _seed_full(fake, "c6_001", txn_type="DIVIDEND",
                   gross_eur="300", commission_eur="0",
                   withholding={"source": {"country": "US", "rate_pct": "15", "amount_eur": "45"},
                                "destination": {"country": "ES", "rate_pct": "19", "amount_eur": "57"}})
        resp = c.post("/api/portfolio/movements/c6_001/correct", json={
            "account_id": "_unassigned",
            "correction_note": "Clearing destination WHT — not captured",
            "withholding": {
                "source": {"country": "US", "rate_pct": "15", "amount_eur": "45"},
                "destination": None,
            },
        })
        assert resp.status_code == 200, resp.text
        repl = resp.json()["replacement"]
        assert repl["withholding"]["destination"] is None
        # net = 300 - 0 - 45 - 0 = 255  (destination null → 0 in formula)
        assert Decimal(repl["net"]["eur_amount"]) == Decimal("255")

    # ── C-7: DIVIDEND — withholding destination zero ──────────────────────
    def test_c7_dividend_wht_dest_zero(self, client):
        c, fake = client
        _seed_full(fake, "c7_001", txn_type="DIVIDEND",
                   gross_eur="300", commission_eur="0",
                   withholding={"source": {"country": "US", "rate_pct": "15", "amount_eur": "45"},
                                "destination": None})
        resp = c.post("/api/portfolio/movements/c7_001/correct", json={
            "account_id": "_unassigned",
            "correction_note": "Confirmed zero destination",
            "withholding": {
                "source": {"country": "US", "rate_pct": "15", "amount_eur": "45"},
                "destination": {"country": "ES", "rate_pct": "0", "amount_eur": "0"},
            },
        })
        assert resp.status_code == 200, resp.text
        repl = resp.json()["replacement"]
        dst = repl["withholding"]["destination"]
        assert dst is not None
        assert dst["amount_eur"] == "0"
        # net = 300 - 0 - 45 - 0 = 255  (same as null-destination case)
        assert Decimal(repl["net"]["eur_amount"]) == Decimal("255")

    # ── C-8: gross change → net recomputed using original fees ────────────
    def test_c8_gross_change_triggers_net_recompute(self, client):
        c, fake = client
        _seed_full(fake, "c8_001", txn_type="BUY",
                   gross_eur="10000", commission_eur="5.00")
        resp = c.post("/api/portfolio/movements/c8_001/correct", json={
            "account_id": "_unassigned",
            "correction_note": "Gross was wrong",
            "gross": {"amount": "9000", "currency": "EUR", "eur_amount": "9000"},
        })
        assert resp.status_code == 200, resp.text
        repl = resp.json()["replacement"]
        # net = new_gross 9000 + original_fees 5.00 = 9005.00
        assert Decimal(repl["net"]["eur_amount"]) == Decimal("9005.00")
        assert repl["gross"]["eur_amount"] == "9000"

    # ── C-9: fees change → net recomputed using original gross ────────────
    def test_c9_fees_change_triggers_net_recompute(self, client):
        c, fake = client
        _seed_full(fake, "c9_001", txn_type="SELL", sales_type="ACCIONES",
                   gross_eur="5000", commission_eur="3.00")
        resp = c.post("/api/portfolio/movements/c9_001/correct", json={
            "account_id": "_unassigned",
            "correction_note": "Fees were wrong",
            "fees": {"total": "10.00", "currency": "EUR", "total_eur": "10.00"},
        })
        assert resp.status_code == 200, resp.text
        repl = resp.json()["replacement"]
        # net = original_gross 5000 - new_fees 10.00 = 4990.00
        assert Decimal(repl["net"]["eur_amount"]) == Decimal("4990.00")
        assert repl["fees"]["total_eur"] == "10.00"

    # ── C-10: withholding change → net recomputed ─────────────────────────
    def test_c10_withholding_change_triggers_net_recompute(self, client):
        c, fake = client
        _seed_full(fake, "c10_001", txn_type="DIVIDEND",
                   gross_eur="1000", commission_eur="0",
                   withholding={"source": {"country": "US", "rate_pct": "15", "amount_eur": "150"},
                                "destination": {"country": "ES", "rate_pct": "10", "amount_eur": "100"}})
        resp = c.post("/api/portfolio/movements/c10_001/correct", json={
            "account_id": "_unassigned",
            "correction_note": "Withholding amounts corrected",
            "withholding": {
                "source": {"country": "US", "rate_pct": "15", "amount_eur": "100"},
                "destination": {"country": "ES", "rate_pct": "19", "amount_eur": "190"},
            },
        })
        assert resp.status_code == 200, resp.text
        repl = resp.json()["replacement"]
        # net = 1000 - 0 - 100 - 190 = 710
        assert Decimal(repl["net"]["eur_amount"]) == Decimal("710")

    # ── C-11: FX override ─────────────────────────────────────────────────
    def test_c11_fx_override(self, client):
        c, fake = client
        orig = _seed_full(fake, "c11_001", txn_type="BUY")
        orig["fx"] = {"rate": "1.050000000", "rate_source": "ECB"}
        fake.portfolio_container._store["c11_001"] = orig
        resp = c.post("/api/portfolio/movements/c11_001/correct", json={
            "account_id": "_unassigned",
            "correction_note": "Wrong FX rate",
            "fx": {"rate": "1.085000000", "rate_source": "MANUAL"},
        })
        assert resp.status_code == 200, resp.text
        repl = resp.json()["replacement"]
        assert repl["fx"]["rate"] == "1.085000000"
        assert repl["fx"]["rate_source"] == "MANUAL"

    # ── C-12: DIVIDEND quantity null preserved when not provided ──────────
    def test_c12_dividend_quantity_null_preserved(self, client):
        c, fake = client
        orig = _seed_full(fake, "c12_001", txn_type="DIVIDEND",
                          gross_eur="200", commission_eur="0")
        orig["quantity"] = None  # simulate null quantity on a DIVIDEND
        fake.portfolio_container._store["c12_001"] = orig
        # Correct trade_date only — do not touch quantity
        resp = c.post("/api/portfolio/movements/c12_001/correct", json={
            "account_id": "_unassigned",
            "correction_note": "Fixing payment date",
            "trade_date": "2024-03-15",
        })
        assert resp.status_code == 200, resp.text
        repl = resp.json()["replacement"]
        assert repl.get("quantity") is None, (
            "DIVIDEND null quantity must be preserved when quantity not in correction body"
        )
        assert repl["trade_date"] == "2024-03-15"

    # ── C-13: DIVIDEND quantity null→value via correction ─────────────────
    def test_c13_dividend_quantity_null_to_value(self, client):
        c, fake = client
        orig = _seed_full(fake, "c13_001", txn_type="DIVIDEND",
                          gross_eur="200", commission_eur="0")
        orig["quantity"] = None
        fake.portfolio_container._store["c13_001"] = orig
        resp = c.post("/api/portfolio/movements/c13_001/correct", json={
            "account_id": "_unassigned",
            "correction_note": "Setting quantity from statement",
            "quantity": "150",
        })
        assert resp.status_code == 200, resp.text
        assert resp.json()["replacement"]["quantity"] == "150"


class TestFullCorrectionValidation:
    """C-14 through C-15: Validation and rejection per Danny contract §F.1.

    C-14a/b use DIVIDEND movements to test withholding structure rules.
    NOTE: The withholding field shape for DIVIDEND is subject to Danny's in-flight
    amendment (amount user-entered, rate_pct auto-calculated). The structural rule
    that amount_eur is required when source/destination is non-null is expected to
    remain valid; the rate_pct direction may change. Revisit after amendment lands.
    """

    # ── C-14a: invalid withholding structure — source missing amount_eur ──
    def test_c14_invalid_withholding_source_missing_amount_eur(self, client):
        c, fake = client
        _seed_full(fake, "c14_001", txn_type="DIVIDEND",
                   gross_eur="300", commission_eur="0")
        resp = c.post("/api/portfolio/movements/c14_001/correct", json={
            "account_id": "_unassigned",
            "correction_note": "Bad withholding",
            "withholding": {
                "source": {"country": "US", "rate_pct": "15"},  # missing amount_eur
            },
        })
        assert resp.status_code == 400, resp.text
        data = resp.json()
        assert data["error"] == "validation_error"
        assert "amount_eur" in data["detail"]

    # ── C-14b: invalid withholding — not an object ────────────────────────
    def test_c14b_invalid_withholding_not_object(self, client):
        c, fake = client
        _seed_full(fake, "c14b_001", txn_type="DIVIDEND",
                   gross_eur="300", commission_eur="0")
        resp = c.post("/api/portfolio/movements/c14b_001/correct", json={
            "account_id": "_unassigned",
            "correction_note": "Bad withholding",
            "withholding": "not-an-object",
        })
        assert resp.status_code == 400
        assert resp.json()["error"] == "validation_error"

    # ── C-14c: invalid sales_type ─────────────────────────────────────────
    def test_c14c_invalid_sales_type_value(self, client):
        c, fake = client
        _seed_full(fake, "c14c_001", txn_type="SELL", sales_type="ACCIONES")
        resp = c.post("/api/portfolio/movements/c14c_001/correct", json={
            "account_id": "_unassigned",
            "correction_note": "Bad sales type",
            "sales_type": "OPTIONS",  # invalid
        })
        assert resp.status_code == 400
        assert resp.json()["error"] == "validation_error"

    # ── C-14d: BUY with withholding → rejected ────────────────────────────
    def test_c14d_buy_with_withholding_rejected(self, client):
        c, fake = client
        _seed_full(fake, "c14d_001", txn_type="BUY")
        resp = c.post("/api/portfolio/movements/c14d_001/correct", json={
            "account_id": "_unassigned",
            "correction_note": "BUY should not have withholding",
            "withholding": {
                "source": {"country": "US", "rate_pct": "15", "amount_eur": "10"},
            },
        })
        assert resp.status_code == 400
        data = resp.json()
        assert data["error"] == "validation_error"
        assert "BUY" in data["detail"]

    # ── C-14e: SELL with cost_basis_status → rejected ─────────────────────
    def test_c14e_sell_with_cost_basis_status_rejected(self, client):
        c, fake = client
        _seed_full(fake, "c14e_001", txn_type="SELL", sales_type="ACCIONES")
        resp = c.post("/api/portfolio/movements/c14e_001/correct", json={
            "account_id": "_unassigned",
            "correction_note": "SELL should not have cost_basis_status",
            "cost_basis_status": "COMPLETE",
        })
        assert resp.status_code == 400
        assert resp.json()["error"] == "validation_error"

    # ── C-14f: DIVIDEND with sales_type → rejected ────────────────────────
    def test_c14f_dividend_with_sales_type_rejected(self, client):
        c, fake = client
        _seed_full(fake, "c14f_001", txn_type="DIVIDEND",
                   gross_eur="200", commission_eur="0")
        resp = c.post("/api/portfolio/movements/c14f_001/correct", json={
            "account_id": "_unassigned",
            "correction_note": "DIVIDEND should not have sales_type",
            "sales_type": "ACCIONES",
        })
        assert resp.status_code == 400
        assert resp.json()["error"] == "validation_error"

    # ── C-14g: FX rate = 0 → rejected ────────────────────────────────────
    def test_c14g_fx_rate_zero_rejected(self, client):
        c, fake = client
        _seed_full(fake, "c14g_001", txn_type="BUY")
        resp = c.post("/api/portfolio/movements/c14g_001/correct", json={
            "account_id": "_unassigned",
            "correction_note": "Bad FX rate",
            "fx": {"rate": "0", "rate_source": "ECB"},
        })
        assert resp.status_code == 400
        data = resp.json()
        assert data["error"] == "validation_error"
        assert "rate" in data["detail"].lower()

    # ── C-14h: FX invalid rate_source → rejected ──────────────────────────
    def test_c14h_fx_invalid_rate_source_rejected(self, client):
        c, fake = client
        _seed_full(fake, "c14h_001", txn_type="BUY")
        resp = c.post("/api/portfolio/movements/c14h_001/correct", json={
            "account_id": "_unassigned",
            "correction_note": "Bad FX source",
            "fx": {"rate": "1.1", "rate_source": "UNKNOWN"},
        })
        assert resp.status_code == 400
        assert resp.json()["error"] == "validation_error"

    # ── C-14i: gross missing sub-field ────────────────────────────────────
    def test_c14i_gross_missing_eur_amount(self, client):
        c, fake = client
        _seed_full(fake, "c14i_001", txn_type="BUY")
        resp = c.post("/api/portfolio/movements/c14i_001/correct", json={
            "account_id": "_unassigned",
            "correction_note": "Partial gross",
            "gross": {"amount": "1000", "currency": "EUR"},  # missing eur_amount
        })
        assert resp.status_code == 400
        data = resp.json()
        assert data["error"] == "validation_error"
        assert "eur_amount" in data["detail"]

    # ── C-14j: negative quantity → rejected ───────────────────────────────
    def test_c14j_negative_quantity_rejected(self, client):
        c, fake = client
        _seed_full(fake, "c14j_001", txn_type="BUY")
        resp = c.post("/api/portfolio/movements/c14j_001/correct", json={
            "account_id": "_unassigned",
            "correction_note": "Negative quantity",
            "quantity": "-10",
        })
        assert resp.status_code == 400
        assert resp.json()["error"] == "validation_error"

    # ── C-14k: null quantity on BUY → rejected ────────────────────────────
    def test_c14k_null_quantity_on_buy_rejected(self, client):
        c, fake = client
        _seed_full(fake, "c14k_001", txn_type="BUY")
        resp = c.post("/api/portfolio/movements/c14k_001/correct", json={
            "account_id": "_unassigned",
            "correction_note": "Null quantity on BUY",
            "quantity": None,
        })
        assert resp.status_code == 400
        assert resp.json()["error"] == "validation_error"

    # ── C-15: TRANSFER correction → 405 ──────────────────────────────────
    def test_c15_transfer_correction_returns_405(self, client):
        c, fake = client
        # Seed a TRANSFER_OUT movement
        fake.portfolio_container._store["c15_t_out"] = {
            "id": "c15_t_out",
            "doc_type": "ledger_txn",
            "txn_type": "TRANSFER_OUT",
            "security_id": "XNYS:AAPL",
            "ticker": "AAPL",
            "trade_date": "2024-01-15",
            "quantity": "100",
            "gross": {"amount": "0", "currency": "EUR", "eur_amount": "0"},
            "fees": {"total": "0", "currency": "EUR", "total_eur": "0"},
            "net": {"amount": "0", "currency": "EUR", "eur_amount": "0"},
            "account_id": "_unassigned",
            "correction_status": "ACTIVE",
            "import_source": "manual",
            "created_at": "2026-09-06T10:00:00Z",
            "warnings": [],
        }
        resp = c.post("/api/portfolio/movements/c15_t_out/correct", json={
            "account_id": "_unassigned",
            "correction_note": "Trying to correct a transfer",
        })
        assert resp.status_code == 405, resp.text
        data = resp.json()
        assert data["error"] == "transfer_not_correctable"

    def test_c15_transfer_in_correction_returns_405(self, client):
        c, fake = client
        fake.portfolio_container._store["c15_t_in"] = {
            "id": "c15_t_in",
            "doc_type": "ledger_txn",
            "txn_type": "TRANSFER_IN",
            "security_id": "XNYS:AAPL",
            "ticker": "AAPL",
            "trade_date": "2024-01-15",
            "quantity": "100",
            "gross": {"amount": "0", "currency": "EUR", "eur_amount": "0"},
            "fees": {"total": "0", "currency": "EUR", "total_eur": "0"},
            "net": {"amount": "0", "currency": "EUR", "eur_amount": "0"},
            "account_id": "_unassigned",
            "correction_status": "ACTIVE",
            "import_source": "manual",
            "created_at": "2026-09-06T10:00:00Z",
            "warnings": [],
        }
        resp = c.post("/api/portfolio/movements/c15_t_in/correct", json={
            "account_id": "_unassigned",
            "correction_note": "Trying to correct a transfer",
        })
        assert resp.status_code == 405
        assert resp.json()["error"] == "transfer_not_correctable"


class TestCorrectionImportSourceProvenence:
    """Replacement always has import_source=manual; original preserves its original source."""

    def test_replacement_import_source_is_manual(self, client):
        c, fake = client
        doc = _seed_full(fake, "prov_001", txn_type="BUY")
        doc["import_source"] = "csv_import"
        fake.portfolio_container._store["prov_001"] = doc
        resp = c.post("/api/portfolio/movements/prov_001/correct", json={
            "account_id": "_unassigned",
            "correction_note": "Provenance test",
            "quantity": "50",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["replacement"]["import_source"] == "manual"
        assert data["original"]["import_source"] == "csv_import"

    def test_original_import_source_preserved(self, client):
        c, fake = client
        doc = _seed_full(fake, "prov_002", txn_type="SELL", sales_type="ACCIONES")
        doc["import_source"] = "csv_import"
        fake.portfolio_container._store["prov_002"] = doc
        resp = c.post("/api/portfolio/movements/prov_002/correct", json={
            "account_id": "_unassigned",
            "correction_note": "Provenance test on SELL",
            "quantity": "80",
        })
        assert resp.status_code == 200
        # Original in store should still have csv_import
        stored_orig = fake.portfolio_container._store["prov_002"]
        assert stored_orig["import_source"] == "csv_import"
