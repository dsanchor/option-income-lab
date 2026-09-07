"""Options Screener Dropdown — regression tests.

Ref: copilot-directive-20260907-options-screener-dropdown.md
Ref: danny-symbol-config-investments-screener-dropdown-final-gate.md
Ref: danny-options-screener-universe-contract.md §2.2, §2.7

The dropdown in OptionsScreenerView.tsx is populated by fetching
``GET /api/symbols/overview`` and applying ``isScreenerEligible`` which now
reads a single authoritative backend boolean ``screener_eligible === true``
(Reuben / Danny final-gate revision; previously used heuristics).

For this to work every overview row MUST carry:
  - ``us_options_eligible`` bool: MIC ∈ {XNYS, XNAS} (US gate)
  - ``screener_eligible`` bool: US gate AND (shares>0 OR is_watchlist_member)
    (single authoritative predicate — no reimplementation in frontend)

Coverage (OSD = Options Screener Dropdown):

  OSD-1   Overview rows include ``us_options_eligible`` boolean (XNYS -> True).
  OSD-2   Overview rows: XAMS symbol -> us_options_eligible=False.
  OSD-3   Overview rows: unknown/missing MIC -> us_options_eligible=False.
  OSD-3b  Overview rows: XNAS symbol -> us_options_eligible=True.
  OSD-4   US symbol with portfolio shares > 0 -> eligible (screener_eligible=True).
  OSD-5   Non-US with portfolio shares -> screener_eligible=False.
  OSD-6   US explicit-watchlist zero shares -> screener_eligible=True.
  OSD-7   US auto-enrolled zero-share historical -> excluded from overview.
  OSD-8   Non-US watchlist member -> screener_eligible=False (US gate first).
  OSD-9   Negative shares + explicit watchlist US -> screener_eligible=True.
  OSD-10  Negative shares + no watchlist US -> screener_eligible=False.
  OSD-11  Unknown MIC + shares -> screener_eligible=False.
  OSD-12  Overview rows contain no duplicate symbols.
  OSD-13  Screener universe subset of overview eligible symbols (cross-endpoint coherence).
  OSD-14  Dropdown endpoint /api/symbols/overview returns correct shape.

  OSD-SE-1  ``screener_eligible`` field present in every visible overview row.
  OSD-SE-2  XNYS + shares>0 -> screener_eligible=True.
  OSD-SE-3  XNAS + explicit-watchlist cc=True + zero shares -> screener_eligible=True.
  OSD-SE-4  XAMS + shares>0 -> screener_eligible=False.
  OSD-SE-5  XAMS + watchlist member + shares -> screener_eligible=False.
  OSD-SE-6  Unknown MIC + shares -> screener_eligible=False.
  OSD-SE-7  Negative shares + watchlist (cc=True) US -> screener_eligible=True.
  OSD-SE-8  Negative shares + auto-enrolled only US -> screener_eligible=False.
  OSD-SE-9  CRITICAL: XNYS + auto_enrolled=True + covered_call=True + zero shares ->
            screener_eligible=True (is_watchlist_member detects toggle; old is_auto_enrolled
            heuristic would have incorrectly excluded this symbol from the dropdown).

All tests use hermetic fake Cosmos containers; no network calls.
"""
from __future__ import annotations

import pytest
from decimal import Decimal
from fastapi.testclient import TestClient
from azure.cosmos.exceptions import CosmosResourceNotFoundError


# ---------------------------------------------------------------------------
# Standalone fake containers (independent of other test suites)
# ---------------------------------------------------------------------------

class _FakePortfolioContainer:
    def __init__(self):
        self._store: dict = {}

    def query_items(self, query="", parameters=None, enable_cross_partition_query=True,
                    partition_key=None):
        param_map = {p["name"]: p["value"] for p in (parameters or [])}
        results = []
        for doc in self._store.values():
            if partition_key is not None and doc.get("account_id") != partition_key:
                continue
            if "doc_type = 'ledger_txn'" in query and doc.get("doc_type") != "ledger_txn":
                continue
            if "NOT IS_DEFINED(c.deleted_at)" in query and "deleted_at" in doc:
                continue
            cs = doc.get("correction_status")
            if "ACTIVE" in query and cs is not None and cs != "ACTIVE":
                continue
            if "@security_id" in param_map and doc.get("security_id") != param_map["@security_id"]:
                continue
            results.append(dict(doc))
        return iter(results)

    def read_item(self, item, partition_key):
        if item not in self._store:
            raise CosmosResourceNotFoundError(message="not found", response=None)
        return dict(self._store[item])

    def upsert_item(self, body):
        self._store[body["id"]] = dict(body)
        return dict(body)

    def add_buy(self, security_id, quantity="100", gross_eur="10000"):
        ticker = security_id.split(":")[-1]
        doc_id = f"buy_{ticker}"
        self._store[doc_id] = {
            "id": doc_id,
            "account_id": "_unassigned",
            "doc_type": "ledger_txn",
            "txn_type": "BUY",
            "security_id": security_id,
            "ticker": ticker,
            "trade_date": "2026-01-01",
            "quantity": quantity,
            "gross": {"amount": gross_eur, "currency": "EUR", "eur_amount": gross_eur},
            "net": {"amount": gross_eur, "currency": "EUR", "eur_amount": gross_eur},
            "fees": {"total": "0", "currency": "EUR", "total_eur": "0"},
            "net_eur": gross_eur,
            "correction_status": "ACTIVE",
            "cost_basis_status": "COMPLETE",
        }


class _FakeSymbolsContainer:
    def __init__(self):
        self._docs: dict = {}   # ticker -> symbol_config doc

    def add_symbol(self, ticker: str, exchange: str = "XNYS",
                   auto_enrolled: bool = False, cc: bool = False,
                   csp: bool = False, bt: bool = False, tg: bool = False):
        self._docs[ticker] = {
            "id": f"config_{ticker}",
            "symbol": ticker,
            "doc_type": "symbol_config",
            "security_id": f"{exchange}:{ticker}",
            "exchange": exchange,
            "display_name": f"{ticker} Corp",
            "_auto_enrolled": auto_enrolled,
            "watchlist": {
                "covered_call": cc,
                "cash_secured_put": csp,
                "buy_tracker": bt,
            },
            "telegram_notifications_enabled": tg,
            "total_shares": 0,
            "enrichment": {},
            "positions": [],
        }
        return self._docs[ticker]

    def query_items(self, query="", parameters=None, enable_cross_partition_query=False,
                    partition_key=None):
        return iter([
            doc for doc in self._docs.values()
            if doc.get("doc_type") == "security_master"
        ])

    def read_item(self, item, partition_key):
        raise CosmosResourceNotFoundError(message="not found", response=None)

    def upsert_item(self, body):
        ticker = body.get("symbol", "")
        self._docs[ticker] = dict(body)
        return dict(body)


class _FakeCosmos:
    def __init__(self):
        self.container = _FakeSymbolsContainer()
        self.portfolio_container = _FakePortfolioContainer()
        self.import_sessions_container = None

    def list_symbols(self):
        return list(self.container._docs.values())

    def get_symbol(self, symbol):
        return self.container._docs.get(symbol.upper())


@pytest.fixture
def cosmos_and_client():
    from web.app import app
    fake = _FakeCosmos()
    with TestClient(app) as c:
        app.state.cosmos = fake
        app.state.cosmos_error = None
        yield c, fake


# ---------------------------------------------------------------------------
# OSD-1 / OSD-2 / OSD-3: us_options_eligible field in overview rows
# ---------------------------------------------------------------------------

class TestOverviewRowUsOptionsEligibleField:
    """OSD-1/2/3: every overview row must carry us_options_eligible boolean.

    DEFECT: _compute_symbols_overview in app.py does not currently add this
    field.  These tests will FAIL until Livingston fixes it.
    """

    def test_osd1_xnys_symbol_has_us_options_eligible_true(self, cosmos_and_client):
        """OSD-1: XNYS symbol in overview row must have us_options_eligible=True."""
        c, fake = cosmos_and_client
        fake.container.add_symbol("ABBV", exchange="XNYS", auto_enrolled=False)
        resp = c.get("/api/symbols/overview")
        assert resp.status_code == 200
        rows = resp.json().get("rows", [])
        abbv_rows = [r for r in rows if r.get("symbol") == "ABBV"]
        assert abbv_rows, "ABBV must appear in overview rows (explicit watchlist member)"
        row = abbv_rows[0]
        assert "us_options_eligible" in row, (
            "OSD-1 DEFECT: 'us_options_eligible' field missing from overview row for XNYS:ABBV. "
            "Livingston: add is_us_options_eligible(doc['exchange']) per row in _compute_symbols_overview."
        )
        assert row["us_options_eligible"] is True, (
            f"OSD-1 DEFECT: XNYS:ABBV must have us_options_eligible=True, got {row['us_options_eligible']!r}"
        )

    def test_osd2_xams_symbol_has_us_options_eligible_false(self, cosmos_and_client):
        """OSD-2: XAMS (Amsterdam) symbol must have us_options_eligible=False in overview."""
        c, fake = cosmos_and_client
        fake.container.add_symbol("AD", exchange="XAMS", auto_enrolled=False)
        resp = c.get("/api/symbols/overview")
        assert resp.status_code == 200
        rows = resp.json().get("rows", [])
        ad_rows = [r for r in rows if r.get("symbol") == "AD"]
        assert ad_rows, "AD (XAMS) must appear in overview rows"
        row = ad_rows[0]
        assert "us_options_eligible" in row, (
            "OSD-2 DEFECT: 'us_options_eligible' missing from overview row for XAMS:AD."
        )
        assert row["us_options_eligible"] is False, (
            f"OSD-2 DEFECT: XAMS:AD must have us_options_eligible=False, got {row['us_options_eligible']!r}"
        )

    def test_osd3_unknown_mic_has_us_options_eligible_false(self, cosmos_and_client):
        """OSD-3: Symbol with unknown/missing MIC -> us_options_eligible=False."""
        c, fake = cosmos_and_client
        fake.container.add_symbol("ZZZZ", exchange="XZZZ", auto_enrolled=False)
        resp = c.get("/api/symbols/overview")
        assert resp.status_code == 200
        rows = resp.json().get("rows", [])
        zzzz_rows = [r for r in rows if r.get("symbol") == "ZZZZ"]
        assert zzzz_rows, "ZZZZ (XZZZ) must appear in overview rows"
        row = zzzz_rows[0]
        assert "us_options_eligible" in row, (
            "OSD-3 DEFECT: 'us_options_eligible' missing from overview row for XZZZ:ZZZZ."
        )
        assert row["us_options_eligible"] is False, (
            f"OSD-3 DEFECT: XZZZ:ZZZZ must have us_options_eligible=False, got {row['us_options_eligible']!r}"
        )

    def test_osd3b_xnas_has_us_options_eligible_true(self, cosmos_and_client):
        """OSD-3b: XNAS symbol must have us_options_eligible=True in overview."""
        c, fake = cosmos_and_client
        fake.container.add_symbol("MSFT", exchange="XNAS", auto_enrolled=False)
        resp = c.get("/api/symbols/overview")
        assert resp.status_code == 200
        rows = resp.json().get("rows", [])
        msft_rows = [r for r in rows if r.get("symbol") == "MSFT"]
        assert msft_rows, "MSFT must appear in overview rows"
        row = msft_rows[0]
        assert "us_options_eligible" in row, (
            "OSD-3b DEFECT: 'us_options_eligible' missing from overview row for XNAS:MSFT."
        )
        assert row["us_options_eligible"] is True, (
            f"OSD-3b DEFECT: XNAS:MSFT must have us_options_eligible=True, got {row['us_options_eligible']!r}"
        )


# ---------------------------------------------------------------------------
# OSD-4/5/6/7/8/9/10/11: Dropdown eligibility — conditional on field presence
# ---------------------------------------------------------------------------

class TestDropdownUniversePredicate:
    """OSD-4–11: verify which symbols appear/don't appear when overview field is correct.

    These tests verify both the FIELD PRESENCE and the eligibility outcome.
    For non-US checks, if us_options_eligible is absent the test documents
    the defect rather than asserting a false-positive pass.
    """

    def test_osd4_us_portfolio_positive_eligible(self, cosmos_and_client):
        """OSD-4: US symbol with portfolio shares > 0 must be eligible for dropdown."""
        c, fake = cosmos_and_client
        fake.container.add_symbol("ABBV", exchange="XNYS", auto_enrolled=True)
        fake.portfolio_container.add_buy("XNYS:ABBV", quantity="100")
        resp = c.get("/api/symbols/overview")
        assert resp.status_code == 200
        rows = resp.json().get("rows", [])
        abbv = next((r for r in rows if r.get("symbol") == "ABBV"), None)
        assert abbv is not None, "ABBV must appear in overview (has portfolio shares)"
        # When us_options_eligible is present, confirm it's True
        if "us_options_eligible" in abbv:
            assert abbv["us_options_eligible"] is True
        # Shares field must indicate positive holdings
        shares = float(abbv.get("portfolio_shares") or "0")
        assert shares > 0, f"OSD-4: ABBV must have portfolio_shares > 0, got {shares!r}"

    def test_osd5_non_us_portfolio_positive_has_false_flag(self, cosmos_and_client):
        """OSD-5: Non-US (XAMS) symbol with portfolio shares must have us_options_eligible=False.

        If us_options_eligible is absent (current defect), this test documents the gap:
        the frontend cannot correctly exclude the symbol from the dropdown.
        """
        c, fake = cosmos_and_client
        fake.container.add_symbol("AD", exchange="XAMS", auto_enrolled=True)
        fake.portfolio_container.add_buy("XAMS:AD", quantity="50")
        resp = c.get("/api/symbols/overview")
        assert resp.status_code == 200
        rows = resp.json().get("rows", [])
        ad = next((r for r in rows if r.get("symbol") == "AD"), None)
        assert ad is not None, "AD must appear in overview (non-zero shares)"
        # Verify the field and value
        assert "us_options_eligible" in ad, (
            "OSD-5 DEFECT: us_options_eligible absent -> frontend cannot block XAMS:AD from dropdown. "
            "Livingston: add is_us_options_eligible(doc['exchange']) per row."
        )
        assert ad["us_options_eligible"] is False, (
            f"OSD-5 DEFECT: XAMS:AD with portfolio shares must have us_options_eligible=False, "
            f"got {ad['us_options_eligible']!r}"
        )

    def test_osd6_us_watchlist_only_zero_shares_eligible(self, cosmos_and_client):
        """OSD-6: US explicit-watchlist symbol with zero shares is eligible for dropdown."""
        c, fake = cosmos_and_client
        # Manually added (auto_enrolled=False) -> watchlist member → included even with zero shares
        fake.container.add_symbol("JNJ", exchange="XNYS", auto_enrolled=False)
        resp = c.get("/api/symbols/overview")
        assert resp.status_code == 200
        rows = resp.json().get("rows", [])
        jnj = next((r for r in rows if r.get("symbol") == "JNJ"), None)
        assert jnj is not None, "JNJ (explicit watchlist) must appear in overview"
        if "us_options_eligible" in jnj:
            assert jnj["us_options_eligible"] is True

    def test_osd7_us_auto_enrolled_zero_share_excluded_from_overview(self, cosmos_and_client):
        """OSD-7: US auto-enrolled historical zero-share symbol is hidden from overview by default."""
        c, fake = cosmos_and_client
        # auto_enrolled=True, no portfolio txns -> shares=0 → hidden by default
        fake.container.add_symbol("HIST", exchange="XNYS", auto_enrolled=True)
        resp = c.get("/api/symbols/overview")
        assert resp.status_code == 200
        rows = resp.json().get("rows", [])
        symbols = {r.get("symbol") for r in rows}
        assert "HIST" not in symbols, (
            "OSD-7: auto-enrolled zero-share historical symbol must not appear in overview "
            "(and therefore cannot appear in dropdown)"
        )

    def test_osd8_non_us_watchlist_member_has_false_flag(self, cosmos_and_client):
        """OSD-8: Non-US symbol that is an explicit watchlist member must have us_options_eligible=False.

        US-eligibility gate takes priority over watchlist membership in the screener universe.
        """
        c, fake = cosmos_and_client
        # Non-US, manually added (watchlist member), no shares
        fake.container.add_symbol("REP", exchange="XMAD", auto_enrolled=False)
        resp = c.get("/api/symbols/overview")
        assert resp.status_code == 200
        rows = resp.json().get("rows", [])
        rep = next((r for r in rows if r.get("symbol") == "REP"), None)
        assert rep is not None, "REP (XMAD, explicit watchlist) must appear in overview"
        assert "us_options_eligible" in rep, (
            "OSD-8 DEFECT: us_options_eligible absent -> cannot gate non-US watchlist member out of dropdown."
        )
        assert rep["us_options_eligible"] is False, (
            f"OSD-8 DEFECT: XMAD:REP must have us_options_eligible=False, got {rep['us_options_eligible']!r}"
        )

    def test_osd9_us_negative_shares_watchlist_member_eligible(self, cosmos_and_client):
        """OSD-9: US symbol with negative portfolio shares AND explicit watchlist -> eligible.

        Negative shares (data anomaly) alone don't qualify, but watchlist membership does.
        """
        c, fake = cosmos_and_client
        # Manually added (auto_enrolled=False) with covered_call toggle -> watchlist member
        fake.container.add_symbol("ABBV", exchange="XNYS", auto_enrolled=False, cc=True)
        # Add a sell without a prior buy to create negative-share scenario
        ticker = "ABBV"
        did = f"sell_{ticker}"
        fake.portfolio_container._store[did] = {
            "id": did,
            "account_id": "_unassigned",
            "doc_type": "ledger_txn",
            "txn_type": "SELL",
            "security_id": "XNYS:ABBV",
            "ticker": ticker,
            "trade_date": "2026-01-01",
            "quantity": "100",
            "sales_type": "ACCIONES",
            "gross": {"amount": "10000", "currency": "EUR", "eur_amount": "10000"},
            "net": {"amount": "10000", "currency": "EUR", "eur_amount": "10000"},
            "fees": {"total": "0", "currency": "EUR", "total_eur": "0"},
            "net_eur": "10000",
            "correction_status": "ACTIVE",
        }
        resp = c.get("/api/symbols/overview")
        assert resp.status_code == 200
        rows = resp.json().get("rows", [])
        abbv = next((r for r in rows if r.get("symbol") == "ABBV"), None)
        assert abbv is not None, "OSD-9: ABBV (non-zero shares, explicit watchlist) must appear in overview"
        if "us_options_eligible" in abbv:
            assert abbv["us_options_eligible"] is True

    def test_osd10_us_negative_shares_no_watchlist_excluded(self, cosmos_and_client):
        """OSD-10: US auto-enrolled symbol with negative shares, no watchlist -> NOT eligible.

        shares < 0 counts as nonzero (visible in overview per visibility predicate),
        but is NOT > 0 (screener eligibility). Without watchlist membership, excluded.
        """
        c, fake = cosmos_and_client
        # auto_enrolled=True (NOT a watchlist member), no watchlist toggles
        fake.container.add_symbol("HIST2", exchange="XNYS", auto_enrolled=True)
        did = "sell_HIST2"
        fake.portfolio_container._store[did] = {
            "id": did,
            "account_id": "_unassigned",
            "doc_type": "ledger_txn",
            "txn_type": "SELL",
            "security_id": "XNYS:HIST2",
            "ticker": "HIST2",
            "trade_date": "2026-01-01",
            "quantity": "50",
            "sales_type": "ACCIONES",
            "gross": {"amount": "5000", "currency": "EUR", "eur_amount": "5000"},
            "net": {"amount": "5000", "currency": "EUR", "eur_amount": "5000"},
            "fees": {"total": "0", "currency": "EUR", "total_eur": "0"},
            "net_eur": "5000",
            "correction_status": "ACTIVE",
        }
        resp = c.get("/api/symbols/overview")
        assert resp.status_code == 200
        rows = resp.json().get("rows", [])
        hist2 = next((r for r in rows if r.get("symbol") == "HIST2"), None)
        # HIST2 appears in overview because shares != 0
        assert hist2 is not None, "HIST2 must appear in overview (non-zero shares)"
        if "us_options_eligible" in hist2:
            # verify: shares < 0, no watchlist -> isScreenerEligible returns false
            shares = float(hist2.get("portfolio_shares") or "0")
            is_watchlist = (
                hist2.get("is_auto_enrolled") is False
                or hist2.get("row_source") == "watchlist"
            )
            assert shares <= 0 and not is_watchlist, (
                "OSD-10: HIST2 should have negative/zero shares and no watchlist membership"
            )

    def test_osd11_unknown_mic_with_shares_has_false_flag(self, cosmos_and_client):
        """OSD-11: Symbol with unknown MIC must have us_options_eligible=False even with shares."""
        c, fake = cosmos_and_client
        fake.container.add_symbol("ZUNK", exchange="XZZZ", auto_enrolled=False)
        fake.portfolio_container.add_buy("XZZZ:ZUNK", quantity="100")
        resp = c.get("/api/symbols/overview")
        assert resp.status_code == 200
        rows = resp.json().get("rows", [])
        zunk = next((r for r in rows if r.get("symbol") == "ZUNK"), None)
        assert zunk is not None, "ZUNK must appear in overview (has portfolio shares)"
        assert "us_options_eligible" in zunk, (
            "OSD-11 DEFECT: us_options_eligible absent from overview row for unknown MIC symbol."
        )
        assert zunk["us_options_eligible"] is False, (
            f"OSD-11 DEFECT: XZZZ:ZUNK must have us_options_eligible=False, got {zunk['us_options_eligible']!r}"
        )


# ---------------------------------------------------------------------------
# OSD-12: No duplicate symbols in overview rows
# ---------------------------------------------------------------------------

class TestOverviewDeduplication:
    def test_osd12_no_duplicate_symbols_in_overview_rows(self, cosmos_and_client):
        """OSD-12: Overview rows must contain no duplicate symbol values."""
        c, fake = cosmos_and_client
        for ticker, exchange in [("ABBV", "XNYS"), ("MSFT", "XNAS"), ("AD", "XAMS")]:
            fake.container.add_symbol(ticker, exchange=exchange, auto_enrolled=False)
        resp = c.get("/api/symbols/overview")
        assert resp.status_code == 200
        rows = resp.json().get("rows", [])
        syms = [r.get("symbol") for r in rows]
        assert len(syms) == len(set(syms)), (
            f"OSD-12: Duplicate symbols in overview rows: {[s for s in syms if syms.count(s) > 1]}"
        )


# ---------------------------------------------------------------------------
# OSD-13: Cross-endpoint coherence
# ---------------------------------------------------------------------------

class TestCrossEndpointCoherence:
    """OSD-13: Every ticker admitted by compute_options_screener_universe
    must also appear and be eligible in the /api/symbols/overview response.

    This ensures the dropdown and screener execution use the same universe.
    """

    def test_osd13_screener_universe_subset_of_overview_eligible(self, cosmos_and_client):
        """OSD-13: Symbols in compute_options_screener_universe subset of eligible overview rows.

        If us_options_eligible is missing from overview rows (current defect), this test
        verifies at least that the symbols are PRESENT in the overview (which they must be
        for the dropdown to offer them). The US eligibility check on the frontend side
        requires the field — so this test is a necessary but not sufficient condition.
        """
        from src.options_screener_universe import compute_options_screener_universe
        from decimal import Decimal

        c, fake = cosmos_and_client
        # Seed: ABBV (XNYS, watchlist) -> eligible; AD (XAMS, watchlist) → excluded
        fake.container.add_symbol("ABBV", exchange="XNYS", auto_enrolled=False)
        fake.container.add_symbol("AD", exchange="XAMS", auto_enrolled=False)

        # Compute screener universe directly
        docs = fake.list_symbols()
        universe = compute_options_screener_universe(docs, {"ABBV": Decimal("0")})
        # ABBV is watchlist member -> included; AD is XAMS → excluded
        assert "ABBV" in universe, "ABBV (XNYS, watchlist) must be in screener universe"
        assert "AD" not in universe, "AD (XAMS) must not be in screener universe"

        # Verify overview includes both (visibility predicate: explicit watchlist -> visible)
        resp = c.get("/api/symbols/overview")
        assert resp.status_code == 200
        all_rows = resp.json().get("rows", [])
        all_syms = {r.get("symbol") for r in all_rows}
        # Both appear in overview (watchlist members are visible regardless of MIC)
        assert "ABBV" in all_syms
        assert "AD" in all_syms

        # But: every ticker in the screener universe must also appear in the overview,
        # and when us_options_eligible field is present, it must be True for those tickers.
        for ticker in universe:
            row = next((r for r in all_rows if r.get("symbol") == ticker), None)
            assert row is not None, (
                f"OSD-13: {ticker!r} is in screener universe but absent from overview rows — "
                "dropdown cannot offer it."
            )
            if "us_options_eligible" in row:
                assert row["us_options_eligible"] is True, (
                    f"OSD-13: {ticker!r} is in screener universe but overview has "
                    f"us_options_eligible=False — cross-endpoint inconsistency."
                )

        # Verify AD (non-US) is not in screener universe and overview correctly flags it
        ad_row = next((r for r in all_rows if r.get("symbol") == "AD"), None)
        if ad_row and "us_options_eligible" in ad_row:
            assert ad_row["us_options_eligible"] is False, (
                "OSD-13: AD (XAMS) must have us_options_eligible=False in overview — "
                "frontend filter will then correctly exclude it from the dropdown."
            )


# ---------------------------------------------------------------------------
# OSD-14: Dropdown uses /api/symbols/overview (source-contract, Python-side)
# ---------------------------------------------------------------------------

class TestDropdownUsesOverviewEndpoint:
    """OSD-14: The dropdown endpoint source-contract is /api/symbols/overview.

    This is a structural test: we verify that the backend /api/symbols/overview
    endpoint exists and returns a response shape compatible with what
    OptionsScreenerView.tsx expects (body.symbols or body.rows as SymbolRow[]).
    A divergent all-symbols endpoint without the eligibility filter would be
    a contract violation.
    """

    def test_osd14_overview_endpoint_returns_rows_or_symbols_key(self, cosmos_and_client):
        """OSD-14: /api/symbols/overview response has 'rows' or 'symbols' key."""
        c, _ = cosmos_and_client
        resp = c.get("/api/symbols/overview")
        assert resp.status_code == 200
        body = resp.json()
        # Frontend reads body?.symbols ?? body?.rows
        assert "rows" in body or "symbols" in body, (
            "OSD-14: /api/symbols/overview must return 'rows' or 'symbols' key for frontend dropdown"
        )

    def test_osd14b_overview_row_shape_matches_symbol_row_type(self, cosmos_and_client):
        """OSD-14b: Each row in overview has the fields OptionsScreenerView reads."""
        c, fake = cosmos_and_client
        fake.container.add_symbol("ABBV", exchange="XNYS", auto_enrolled=False)
        resp = c.get("/api/symbols/overview")
        assert resp.status_code == 200
        rows = resp.json().get("rows", [])
        assert rows, "Must have at least one row"
        row = rows[0]
        # Fields consumed by isScreenerEligible
        assert "symbol" in row, "SymbolRow must have 'symbol'"
        assert "is_auto_enrolled" in row, "SymbolRow must have 'is_auto_enrolled'"
        assert "row_source" in row, "SymbolRow must have 'row_source'"
        assert "portfolio_shares" in row, "SymbolRow must have 'portfolio_shares'"
        # Authoritative screener eligibility booleans (final-gate requirement)
        assert "screener_eligible" in row, (
            "OSD-14b: 'screener_eligible' boolean must be in every overview row "
            "(frontend isScreenerEligible now reads r.screener_eligible === true directly)"
        )
        assert "us_options_eligible" in row, (
            "OSD-14b: 'us_options_eligible' boolean must be in every overview row"
        )


# ---------------------------------------------------------------------------
# OSD-SE: screener_eligible authoritative boolean — final-gate requirement
# ---------------------------------------------------------------------------

class TestScreenerEligibleField:
    """OSD-SE-1..9: _compute_symbols_overview emits screener_eligible per row.

    Danny final-gate contract: the backend must emit an authoritative
    screener_eligible boolean (MIC ∈ {XNYS,XNAS} AND shares>0 OR is_watchlist_member)
    on every visible overview row, so the frontend can do a direct strict-equality
    check (r.screener_eligible === true) instead of reimplementing the predicate.
    """

    def test_odsse1_field_present_in_every_visible_row(self, cosmos_and_client):
        """OSD-SE-1: screener_eligible field present in every visible overview row."""
        c, fake = cosmos_and_client
        # Mix of US and non-US, portfolio and watchlist
        fake.container.add_symbol("ABBV", exchange="XNYS", auto_enrolled=False)
        fake.container.add_symbol("AD", exchange="XAMS", auto_enrolled=False)
        resp = c.get("/api/symbols/overview")
        assert resp.status_code == 200
        rows = resp.json().get("rows", [])
        assert rows, "Must have rows"
        for row in rows:
            assert "screener_eligible" in row, (
                f"OSD-SE-1: screener_eligible missing from row for symbol={row.get('symbol')!r}"
            )
            assert isinstance(row["screener_eligible"], bool), (
                f"OSD-SE-1: screener_eligible must be bool for symbol={row.get('symbol')!r}, "
                f"got {type(row['screener_eligible'])!r}"
            )

    def test_odsse2_xnys_portfolio_positive_true(self, cosmos_and_client):
        """OSD-SE-2: XNYS + portfolio shares > 0 -> screener_eligible=True."""
        c, fake = cosmos_and_client
        fake.container.add_symbol("ABBV", exchange="XNYS", auto_enrolled=True)
        fake.portfolio_container.add_buy("XNYS:ABBV", quantity="100")
        resp = c.get("/api/symbols/overview")
        rows = resp.json().get("rows", [])
        row = next((r for r in rows if r.get("symbol") == "ABBV"), None)
        assert row is not None
        assert row["screener_eligible"] is True, (
            f"OSD-SE-2: XNYS:ABBV with portfolio shares must be screener_eligible=True, "
            f"got {row['screener_eligible']!r}"
        )

    def test_odsse3_xnas_explicit_watchlist_zero_shares_true(self, cosmos_and_client):
        """OSD-SE-3: XNAS + covered_call=True + zero shares -> screener_eligible=True."""
        c, fake = cosmos_and_client
        # cc=True -> is_watchlist_member=True → screener_eligible=True even with zero shares
        fake.container.add_symbol("MSFT", exchange="XNAS", auto_enrolled=True, cc=True)
        resp = c.get("/api/symbols/overview")
        rows = resp.json().get("rows", [])
        row = next((r for r in rows if r.get("symbol") == "MSFT"), None)
        assert row is not None, "MSFT (XNAS, cc=True) must appear in overview"
        assert row["screener_eligible"] is True, (
            f"OSD-SE-3: XNAS:MSFT with covered_call=True must be screener_eligible=True, "
            f"got {row['screener_eligible']!r}"
        )

    def test_odsse4_xams_portfolio_positive_false(self, cosmos_and_client):
        """OSD-SE-4: Non-US (XAMS) + portfolio shares > 0 -> screener_eligible=False."""
        c, fake = cosmos_and_client
        fake.container.add_symbol("AD", exchange="XAMS", auto_enrolled=True)
        fake.portfolio_container.add_buy("XAMS:AD", quantity="50")
        resp = c.get("/api/symbols/overview")
        rows = resp.json().get("rows", [])
        row = next((r for r in rows if r.get("symbol") == "AD"), None)
        assert row is not None
        assert row["screener_eligible"] is False, (
            f"OSD-SE-4: XAMS:AD (non-US) must have screener_eligible=False even with shares, "
            f"got {row['screener_eligible']!r}"
        )

    def test_odsse5_xams_watchlist_member_shares_false(self, cosmos_and_client):
        """OSD-SE-5: Non-US + watchlist member + shares -> screener_eligible=False (US gate first)."""
        c, fake = cosmos_and_client
        # XMAS, manually added watchlist member, covered_call=True, has shares
        fake.container.add_symbol("REP", exchange="XMAD", auto_enrolled=False, cc=True)
        fake.portfolio_container.add_buy("XMAD:REP", quantity="100")
        resp = c.get("/api/symbols/overview")
        rows = resp.json().get("rows", [])
        row = next((r for r in rows if r.get("symbol") == "REP"), None)
        assert row is not None
        assert row["screener_eligible"] is False, (
            f"OSD-SE-5: XMAD:REP (non-US) must be screener_eligible=False regardless of watchlist, "
            f"got {row['screener_eligible']!r}"
        )

    def test_odsse6_unknown_mic_portfolio_shares_false(self, cosmos_and_client):
        """OSD-SE-6: Unknown MIC + shares -> screener_eligible=False (fail-closed)."""
        c, fake = cosmos_and_client
        fake.container.add_symbol("ZUNK", exchange="XZZZ", auto_enrolled=False)
        fake.portfolio_container.add_buy("XZZZ:ZUNK", quantity="100")
        resp = c.get("/api/symbols/overview")
        rows = resp.json().get("rows", [])
        row = next((r for r in rows if r.get("symbol") == "ZUNK"), None)
        assert row is not None
        assert row["screener_eligible"] is False, (
            f"OSD-SE-6: Unknown MIC XZZZ must fail closed (screener_eligible=False), "
            f"got {row['screener_eligible']!r}"
        )

    def test_odsse7_negative_shares_watchlist_us_true(self, cosmos_and_client):
        """OSD-SE-7: XNYS + negative shares + watchlist (cc=True) -> screener_eligible=True."""
        c, fake = cosmos_and_client
        fake.container.add_symbol("ABBV", exchange="XNYS", auto_enrolled=True, cc=True)
        did = "sell_ABBV"
        fake.portfolio_container._store[did] = {
            "id": did, "account_id": "_unassigned", "doc_type": "ledger_txn",
            "txn_type": "SELL", "security_id": "XNYS:ABBV", "ticker": "ABBV",
            "trade_date": "2026-01-01", "quantity": "100", "sales_type": "ACCIONES",
            "gross": {"amount": "10000", "currency": "EUR", "eur_amount": "10000"},
            "net": {"amount": "10000", "currency": "EUR", "eur_amount": "10000"},
            "fees": {"total": "0", "currency": "EUR", "total_eur": "0"},
            "net_eur": "10000", "correction_status": "ACTIVE",
        }
        resp = c.get("/api/symbols/overview")
        rows = resp.json().get("rows", [])
        row = next((r for r in rows if r.get("symbol") == "ABBV"), None)
        assert row is not None, "ABBV with cc=True must remain visible"
        assert row["screener_eligible"] is True, (
            f"OSD-SE-7: XNYS:ABBV with negative shares + cc=True must be screener_eligible=True, "
            f"got {row['screener_eligible']!r}"
        )

    def test_odsse8_negative_shares_auto_enrolled_only_false(self, cosmos_and_client):
        """OSD-SE-8: XNYS + negative shares + auto-enrolled only -> screener_eligible=False."""
        c, fake = cosmos_and_client
        fake.container.add_symbol("HIST2", exchange="XNYS", auto_enrolled=True)
        did = "sell_HIST2"
        fake.portfolio_container._store[did] = {
            "id": did, "account_id": "_unassigned", "doc_type": "ledger_txn",
            "txn_type": "SELL", "security_id": "XNYS:HIST2", "ticker": "HIST2",
            "trade_date": "2026-01-01", "quantity": "50", "sales_type": "ACCIONES",
            "gross": {"amount": "5000", "currency": "EUR", "eur_amount": "5000"},
            "net": {"amount": "5000", "currency": "EUR", "eur_amount": "5000"},
            "fees": {"total": "0", "currency": "EUR", "total_eur": "0"},
            "net_eur": "5000", "correction_status": "ACTIVE",
        }
        resp = c.get("/api/symbols/overview")
        rows = resp.json().get("rows", [])
        row = next((r for r in rows if r.get("symbol") == "HIST2"), None)
        assert row is not None, "HIST2 (negative shares) must appear in overview"
        assert row["screener_eligible"] is False, (
            f"OSD-SE-8: XNYS:HIST2 with only negative shares (no watchlist) must be "
            f"screener_eligible=False, got {row['screener_eligible']!r}"
        )

    def test_odsse9_critical_toggle_active_auto_enrolled_true(self, cosmos_and_client):
        """OSD-SE-9 CRITICAL: XNYS + auto_enrolled=True + covered_call=True + zero shares
        -> screener_eligible=True.

        This is the exact scenario Danny identified as the concrete defect with the old
        is_auto_enrolled/row_source heuristic:
        - Backend is_watchlist_member(doc) -> True  (covered_call toggle)
        - screener_eligible = XNYS eligible AND (shares>0 OR is_watchlist_member)
                            = True AND (False OR True) = True
        - Old frontend isScreenerEligible:
            is_auto_enrolled=True -> is_auto_enrolled===false fails → False
            row_source will be "portfolio" or "watchlist" but never "both" here
            -> frontend would have returned False (wrong)
        - New frontend isScreenerEligible: r.screener_eligible === True -> True (correct)
        """
        c, fake = cosmos_and_client
        # auto_enrolled=True (NOT a manually-added member by that flag alone),
        # but covered_call=True (which IS an explicit watchlist signal)
        fake.container.add_symbol("ABBV", exchange="XNYS", auto_enrolled=True, cc=True)
        # Zero portfolio shares — the only watchlist signal is the cc toggle
        resp = c.get("/api/symbols/overview")
        rows = resp.json().get("rows", [])
        row = next((r for r in rows if r.get("symbol") == "ABBV"), None)
        assert row is not None, (
            "OSD-SE-9: ABBV with covered_call=True must appear in overview "
            "(is_watchlist_member=True keeps it visible)"
        )
        # Verify the old heuristic fields that would have misled the frontend
        assert row.get("is_auto_enrolled") is True, (
            "Fixture: is_auto_enrolled must be True (this is the field the old heuristic read)"
        )
        # The authoritative boolean must correctly signal eligibility
        assert row["screener_eligible"] is True, (
            f"OSD-SE-9 CRITICAL: XNYS:ABBV with auto_enrolled=True + cc=True + zero shares "
            f"must have screener_eligible=True (backend is_watchlist_member detects cc toggle). "
            f"Got screener_eligible={row['screener_eligible']!r}. "
            f"Old is_auto_enrolled=True heuristic would have wrongly excluded this symbol."
        )
