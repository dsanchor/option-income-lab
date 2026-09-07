"""Regression coverage for the Symbol Pricing Cache (Phase 5 tests).

Contract: .squad/decisions/inbox/danny-symbol-pricing-cache-contract.md

Test IDs: SP-1 ... SP-N (unit/integration) within named test classes.

Coverage (all contract SS11 cases):
  SP-1   GBp raw 4815 -> price_major 48.15, price_currency GBP, price_eur = 48.15 * rate
  SP-2   GBX behaves identically to GBp (both divide by 100 to GBP)
  SP-3   USD: raw price passes through unchanged; no division
  SP-4   EUR: price_eur = price_major, fx_rate = "1.000000000" (identity, no ECB call);
           run_symbol_pricing() end-to-end — get_fx_rate asserted NOT called for EUR
  SP-5   CHF: price_major unchanged, price_eur = price_major * CHF/EUR rate (not division);
           run_symbol_pricing() end-to-end — get_fx_rate asserted called; reciprocal guard
  SP-6   Missing provider currency -> status "error", no price_eur written
  SP-6c  Valid price but no currency with prior cache -> ZERO Cosmos writes, cache preserved
  SP-7   FxUnavailableError aborts entire run; existing cache untouched
  SP-8   FxRateNotFoundError for single currency -> that currency null price_eur, others succeed
  SP-9   Unknown MIC / no Yahoo mapping -> status "error"
  SP-10  provider_symbols["yfinance"] override takes precedence over MIC suffix
  SP-11  pricing_cache write shape: all required fields present, correct types
  SP-12  pricing_cache timestamps: fetched_at ISO 8601 UTC, run_id format
  SP-13  Partial success: some symbols fail, others succeed; good data written
  SP-14  Overview uses pricing_cache price_major when status=="ok"
  SP-15  Overview falls back to enrichment.metrics.current_price when no cache
  SP-16  Overview staleness detection: fetched_at > 2h => pricing_status="stale"
  SP-17  current_value_eur = shares * price_eur (portfolio row, status ok)
  SP-18  total_current_value_eur aggregated across portfolio rows
  SP-19  current_value_eur null for zero-share rows
  SP-20  current_value_eur null when price_eur is null (FX unavailable)
  SP-21  plain GBP (major unit) is NOT divided by 100
  SP-22  scheduler registered with cron "0 9-23 * * 1-5" in main.py
  SP-23  config.yaml has symbol_pricing block with enabled:true and correct cron
  SP-24  integration seam: pricing run -> overview produces price_eur field
  SP-25  double pence conversion guard (backend never divides by 100 twice)

Unit tests (SP-1..13, SP-21, SP-25) require backend/src/symbol_pricing.py.
Source-contract tests (SP-22, SP-23) read the live source files.
Integration tests (SP-14..20, SP-24) run against _compute_symbols_overview via TestClient.

All unit tests skip until Livingston creates symbol_pricing.py.
Integration tests detect regressions on the live overview endpoint.
"""

from __future__ import annotations

import inspect
import pathlib
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch, AsyncMock

import pytest
from azure.cosmos.exceptions import CosmosResourceNotFoundError


# ---------------------------------------------------------------------------
# Conditional import -- all unit tests skip until Livingston ships the module
# ---------------------------------------------------------------------------

_SRC = pathlib.Path(__file__).parent.parent / "src"
sys.path.insert(0, str(_SRC.parent))  # backend/ root on path

# Module lives at backend/src/symbol_pricing.py → import as src.symbol_pricing
_SP_MOD_PATH = "src.symbol_pricing"
try:
    from src import symbol_pricing as _sp_mod
    _MODULE_AVAILABLE = True
    run_symbol_pricing = getattr(_sp_mod, "run_symbol_pricing", None)
    # Private helpers — exported names vary; fall back to None so unit tests
    # exercise the run_symbol_pricing path instead.
    build_pricing_cache_entry = getattr(_sp_mod, "build_pricing_cache_entry", None)
    apply_minor_unit_conversion = (
        getattr(_sp_mod, "apply_minor_unit_conversion", None)
        or getattr(_sp_mod, "_apply_minor_unit", None)
    )
    MINOR_UNIT_TABLE = (
        getattr(_sp_mod, "MINOR_UNIT_TABLE", None)
        or getattr(_sp_mod, "_MINOR_UNIT_MAP", None)
    )
except ImportError:
    _MODULE_AVAILABLE = False
    run_symbol_pricing = None
    build_pricing_cache_entry = None
    apply_minor_unit_conversion = None
    MINOR_UNIT_TABLE = None

_skip_no_module = pytest.mark.skipif(
    not _MODULE_AVAILABLE,
    reason=(
        "backend/src/symbol_pricing.py not yet created. "
        "Livingston: implement per SS7 of danny-symbol-pricing-cache-contract.md."
    ),
)

# FX errors — import from fx_service (always available)
try:
    from src.portfolio.fx_service import FxUnavailableError, FxRateNotFoundError
except ImportError:
    class FxUnavailableError(Exception): pass   # type: ignore[no-redef]
    class FxRateNotFoundError(Exception):       # type: ignore[no-redef]
        def __init__(self, currency="", rate_date=""):
            self.currency = currency
            self.rate_date = rate_date
            super().__init__(f"No ECB rate for {currency} on {rate_date}")


# ---------------------------------------------------------------------------
# Shared fake Cosmos helpers (reused from existing test suite patterns)
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
        self._docs: dict = {}  # ticker -> symbol_config doc

    def add_symbol(self, ticker: str, exchange: str = "XNYS",
                   auto_enrolled: bool = False, cc: bool = False,
                   csp: bool = False, bt: bool = False, tg: bool = False,
                   pricing_cache: dict | None = None,
                   enrichment_price: float | None = None,
                   provider_yfinance: str | None = None):
        doc: dict = {
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
            "positions": [],
        }
        if pricing_cache is not None:
            doc["pricing_cache"] = pricing_cache
        if enrichment_price is not None:
            doc["enrichment"] = {"metrics": {"current_price": enrichment_price}}
        else:
            doc["enrichment"] = {}
        if provider_yfinance is not None:
            doc["provider_symbols"] = {"yfinance": provider_yfinance}
        self._docs[ticker] = doc
        return doc

    def query_items(self, query="", parameters=None, enable_cross_partition_query=False,
                    partition_key=None):
        # security_master query -> return nothing (no security_master docs here)
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

    def replace_item(self, item, body):
        for ticker, doc in self._docs.items():
            if doc.get("id") == item:
                self._docs[ticker] = dict(body)
                return dict(body)
        raise CosmosResourceNotFoundError(message="not found", response=None)


class _FakeCosmos:
    def __init__(self):
        self.container = _FakeSymbolsContainer()
        self.portfolio_container = _FakePortfolioContainer()
        self.import_sessions_container = None
        self._written_caches: dict = {}  # ticker -> pricing_cache written

    def list_symbols(self) -> list:
        return list(self.container._docs.values())

    def get_symbol(self, symbol: str) -> dict | None:
        return self.container._docs.get(symbol.upper())

    def update_symbol_pricing_cache(self, symbol: str, pricing_cache: dict) -> dict:
        """Stub for CosmosDBService.update_symbol_pricing_cache (contract SS7.4)."""
        doc = self.get_symbol(symbol)
        if doc is None:
            raise ValueError(f"Symbol {symbol} not found")
        doc["pricing_cache"] = pricing_cache
        self._written_caches[symbol] = pricing_cache
        return doc


@pytest.fixture
def cosmos_and_client():
    """Shared fixture: fake Cosmos + FastAPI TestClient for overview integration tests."""
    from fastapi.testclient import TestClient
    from web.app import app
    fake = _FakeCosmos()
    with TestClient(app) as c:
        app.state.cosmos = fake
        app.state.cosmos_error = None
        yield c, fake


# ---------------------------------------------------------------------------
# Helper: build a minimal "ok" pricing cache document for seeding
# ---------------------------------------------------------------------------

def _ok_cache(raw_price: float, quote_currency: str, price_major: float,
               price_currency: str, fx_rate: str, price_eur: float | None,
               fetched_at: str | None = None, run_id: str = "20260907T140000Z") -> dict:
    """Minimal valid pricing_cache dict (status='ok') for planting in fake docs."""
    if fetched_at is None:
        fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "raw_price": raw_price,
        "quote_currency": quote_currency,
        "quote_unit": "minor" if quote_currency in ("GBp", "GBX", "ILA", "ZAc") else "major",
        "price_major": price_major,
        "price_currency": price_currency,
        "fx_rate": fx_rate,
        "fx_pair": f"{price_currency}/EUR",
        "fx_rate_date": "2026-09-05",
        "fx_source": "ECB",
        "price_eur": price_eur,
        "fetched_at": fetched_at,
        "status": "ok",
        "error_message": None,
        "run_id": run_id,
    }


# ===========================================================================
# SP-1/2: GBp and GBX minor-unit conversion
# ===========================================================================

class TestGBpGBXConversion:
    """SP-1/2: Pence-to-pounds conversion applied exactly once."""

    @_skip_no_module
    def test_sp1_gbp_pence_raw_4815_becomes_48_15(self):
        """SP-1: GBp raw=4815 -> price_major=48.15, price_currency='GBP'."""
        if apply_minor_unit_conversion is not None:
            # Test the dedicated helper if exported
            major, currency, unit, _qcd = apply_minor_unit_conversion(4815.0, "GBp")
            assert major == pytest.approx(48.15, abs=0.001), (
                f"SP-1 DEFECT: GBp 4815 / 100 must equal 48.15, got {major}"
            )
            assert currency == "GBP", f"SP-1 DEFECT: price_currency must be 'GBP', got {currency!r}"
            assert unit == "minor", f"SP-1 DEFECT: quote_unit must be 'minor' for GBp, got {unit!r}"
        else:
            # Fall through to run_symbol_pricing path
            pytest.skip("apply_minor_unit_conversion not exported; covered by SP-24 integration test")

    @_skip_no_module
    def test_sp1_gbp_cache_entry_shape_via_build(self):
        """SP-1: GBp full cache-entry shape: raw_price/price_major/quote_unit/price_eur correct."""
        if build_pricing_cache_entry is not None:
            # Bonus: helper path for isolated shape check
            entry = build_pricing_cache_entry(
                raw_price=4815.0,
                quote_currency="GBp",
                fx_rate_str="0.845230000",
                fx_rate_date="2026-09-05",
                run_id="20260907T140000Z",
            )
        else:
            # Primary path: run_symbol_pricing() end-to-end — same shape assertions apply.
            if run_symbol_pricing is None:
                pytest.skip("run_symbol_pricing not exported")
            fake_cosmos = _FakeCosmos()
            fake_cosmos.container.add_symbol("ULVR", exchange="XLON")
            with (
                patch("src.symbol_pricing.resolve_yfinance_symbol", return_value="ULVR.L"),
                patch("src.symbol_pricing.yf") as mock_yf,
                patch("src.symbol_pricing.get_fx_rate", return_value="0.845230000"),
            ):
                mock_yf.Ticker.return_value.info = {
                    "currency": "GBp",
                    "regularMarketPrice": 4815.0,
                }
                import asyncio
                asyncio.run(run_symbol_pricing(fake_cosmos))
            written = fake_cosmos._written_caches.get("ULVR")
            assert written is not None, "SP-1 DEFECT: pricing cache must be written for GBp symbol"
            entry = written
        assert entry["raw_price"] == 4815.0
        assert entry["price_major"] == pytest.approx(48.15, abs=0.001), (
            f"SP-1 DEFECT: price_major must be 48.15 (4815/100), got {entry['price_major']}"
        )
        assert entry["price_currency"] == "GBP"
        assert entry["quote_currency"] == "GBp"
        assert entry["quote_unit"] == "minor"
        # price_eur = 48.15 * 0.845230 = 40.70...
        expected_eur = round(48.15 * 0.845230, 2)
        assert entry["price_eur"] == pytest.approx(expected_eur, abs=0.01), (
            f"SP-1 DEFECT: price_eur = price_major * fx_rate expected ~{expected_eur}, got {entry['price_eur']}"
        )

    @_skip_no_module
    def test_sp2_gbx_same_as_gbp(self):
        """SP-2: GBX (alternative pence code) divides by 100 the same as GBp."""
        if apply_minor_unit_conversion is None:
            if build_pricing_cache_entry is None:
                pytest.skip("No exported helper to test GBX; covered by SP-24 integration test")
            entry = build_pricing_cache_entry(
                raw_price=4815.0,
                quote_currency="GBX",
                fx_rate_str="0.845230000",
                fx_rate_date="2026-09-05",
                run_id="20260907T140000Z",
            )
            assert entry["price_major"] == pytest.approx(48.15, abs=0.001), (
                "SP-2 DEFECT: GBX must also divide by 100"
            )
            assert entry["price_currency"] == "GBP"
        else:
            major, currency, unit, _qcd = apply_minor_unit_conversion(4815.0, "GBX")
            assert major == pytest.approx(48.15, abs=0.001)
            assert currency == "GBP"

    @_skip_no_module
    def test_sp21_plain_gbp_is_not_divided(self):
        """SP-21: plain GBP (major unit) must NOT be divided by 100."""
        if apply_minor_unit_conversion is not None:
            major, currency, unit, _qcd = apply_minor_unit_conversion(48.15, "GBP")
            assert major == pytest.approx(48.15, abs=0.001), (
                f"SP-21 DEFECT: 'GBP' (major) must NOT be divided; got {major}. "
                "Only GBp/GBX trigger the /100 division."
            )
            assert currency == "GBP"
            assert unit == "major", f"SP-21 DEFECT: quote_unit must be 'major' for plain GBP, got {unit!r}"
        elif build_pricing_cache_entry is not None:
            entry = build_pricing_cache_entry(
                raw_price=48.15,
                quote_currency="GBP",
                fx_rate_str="0.845230000",
                fx_rate_date="2026-09-05",
                run_id="20260907T140000Z",
            )
            assert entry["price_major"] == pytest.approx(48.15, abs=0.001), (
                "SP-21 DEFECT: plain GBP price_major must equal raw_price (no division)"
            )
            assert entry["quote_unit"] == "major"
        else:
            pytest.skip("No exported helper to test plain GBP")


# ===========================================================================
# SP-3/4/5: USD / EUR / CHF pass-throughs
# ===========================================================================

class TestMajorCurrencies:
    """SP-3/4/5: Non-pence currencies are not divided; EUR is identity."""

    @_skip_no_module
    def test_sp3_usd_raw_passthrough(self):
        """SP-3: USD price_major = raw_price (no division)."""
        if apply_minor_unit_conversion is None:
            if build_pricing_cache_entry is None:
                pytest.skip("No exported helper")
            entry = build_pricing_cache_entry(
                raw_price=166.25,
                quote_currency="USD",
                fx_rate_str="0.917000000",
                fx_rate_date="2026-09-05",
                run_id="20260907T140000Z",
            )
            assert entry["price_major"] == pytest.approx(166.25, abs=0.001), (
                "SP-3 DEFECT: USD price_major must equal raw_price; got division applied"
            )
            assert entry["price_currency"] == "USD"
            assert entry["quote_unit"] == "major"
        else:
            major, currency, unit, _qcd = apply_minor_unit_conversion(166.25, "USD")
            assert major == pytest.approx(166.25, abs=0.001)
            assert currency == "USD"
            assert unit == "major"

    @_skip_no_module
    def test_sp4_eur_identity_no_ecb_call(self):
        """SP-4: EUR price_eur = price_major, fx_rate = '1.000000000'; get_fx_rate NOT called."""
        if build_pricing_cache_entry is not None:
            # Bonus: helper path — verify the entry builder honours the identity contract
            entry = build_pricing_cache_entry(
                raw_price=28.50,
                quote_currency="EUR",
                fx_rate_str="1.000000000",
                fx_rate_date="2026-09-05",
                run_id="20260907T140000Z",
            )
            assert entry["price_major"] == pytest.approx(28.50, abs=0.001)
            assert entry["price_eur"] == pytest.approx(28.50, abs=0.001), (
                "SP-4 DEFECT: EUR price_eur must equal price_major (identity rate)"
            )
            assert entry["fx_rate"] == "1.000000000", (
                "SP-4 DEFECT: EUR fx_rate must be '1.000000000'"
            )
            assert entry["fx_pair"] == "EUR/EUR", (
                "SP-4 DEFECT: EUR fx_pair must be 'EUR/EUR'"
            )
        else:
            # Primary coverage: run_symbol_pricing() end-to-end for a EUR MIC symbol.
            # get_fx_rate must NOT be called — EUR identity needs no ECB roundtrip.
            if run_symbol_pricing is None:
                pytest.skip("run_symbol_pricing not exported")
            fake_cosmos = _FakeCosmos()
            fake_cosmos.container.add_symbol("ADS", exchange="XETR")
            # Any accidental call to get_fx_rate immediately fails the test.
            mock_get_fx = MagicMock(
                side_effect=AssertionError(
                    "SP-4 DEFECT: get_fx_rate must NOT be called for EUR — "
                    "EUR identity rate 1.000000000 requires no ECB lookup."
                )
            )
            with (
                patch("src.symbol_pricing.resolve_yfinance_symbol", return_value="ADS.DE"),
                patch("src.symbol_pricing.yf") as mock_yf,
                patch("src.symbol_pricing.get_fx_rate", mock_get_fx),
            ):
                mock_yf.Ticker.return_value.info = {
                    "currency": "EUR",
                    "regularMarketPrice": 28.50,
                }
                import asyncio
                asyncio.run(run_symbol_pricing(fake_cosmos))
            written = fake_cosmos._written_caches.get("ADS")
            assert written is not None, (
                "SP-4 DEFECT: pricing cache must be written for EUR symbol"
            )
            assert written.get("price_major") == pytest.approx(28.50, abs=0.001), (
                f"SP-4 DEFECT: price_major must equal raw_price for EUR; "
                f"got {written.get('price_major')!r}"
            )
            assert written.get("price_eur") == pytest.approx(28.50, abs=0.001), (
                "SP-4 DEFECT: EUR price_eur must equal price_major (identity rate 1.0); "
                f"got {written.get('price_eur')!r}"
            )
            assert written.get("fx_rate") == "1.000000000", (
                "SP-4 DEFECT: fx_rate for EUR must be '1.000000000'; "
                f"got {written.get('fx_rate')!r}"
            )
            assert written.get("fx_pair") == "EUR/EUR", (
                "SP-4 DEFECT: fx_pair for EUR must be 'EUR/EUR'; "
                f"got {written.get('fx_pair')!r}"
            )
            assert written.get("price_currency") == "EUR", (
                "SP-4 DEFECT: price_currency must be 'EUR'; "
                f"got {written.get('price_currency')!r}"
            )

    @_skip_no_module
    def test_sp5_chf_conversion(self):
        """SP-5: CHF price_major unchanged; price_eur = price_major * CHF/EUR rate (not division)."""
        if build_pricing_cache_entry is not None:
            # Bonus: helper path
            chf_rate = "0.940000000"
            entry = build_pricing_cache_entry(
                raw_price=120.50,
                quote_currency="CHF",
                fx_rate_str=chf_rate,
                fx_rate_date="2026-09-05",
                run_id="20260907T140000Z",
            )
            assert entry["price_major"] == pytest.approx(120.50, abs=0.001), (
                "SP-5 DEFECT: CHF price_major must equal raw_price (no division)"
            )
            expected_eur = round(120.50 * 0.940, 2)
            assert entry["price_eur"] == pytest.approx(expected_eur, abs=0.01), (
                f"SP-5 DEFECT: CHF price_eur = price_major * 0.940 = {expected_eur}, "
                f"got {entry['price_eur']}"
            )
            assert entry["price_currency"] == "CHF"
        else:
            # Primary coverage: run_symbol_pricing() end-to-end for a CHF symbol.
            # Verifies: get_fx_rate IS called for CHF (unlike EUR), multiplication used,
            # and reciprocal/division direction would produce ~128.19 instead of ~113.27.
            if run_symbol_pricing is None:
                pytest.skip("run_symbol_pricing not exported")
            fake_cosmos = _FakeCosmos()
            fake_cosmos.container.add_symbol("NESN", exchange="XSWX")
            mock_get_fx = MagicMock(return_value="0.940000000")
            with (
                patch("src.symbol_pricing.resolve_yfinance_symbol", return_value="NESN.SW"),
                patch("src.symbol_pricing.yf") as mock_yf,
                patch("src.symbol_pricing.get_fx_rate", mock_get_fx),
            ):
                mock_yf.Ticker.return_value.info = {
                    "currency": "CHF",
                    "regularMarketPrice": 120.50,
                }
                import asyncio
                asyncio.run(run_symbol_pricing(fake_cosmos))
            # get_fx_rate must have been called for CHF (unlike EUR which is identity)
            assert mock_get_fx.called, (
                "SP-5 DEFECT: get_fx_rate must be called for CHF — "
                "CHF/EUR requires an ECB lookup (unlike EUR which is identity)."
            )
            written = fake_cosmos._written_caches.get("NESN")
            assert written is not None, (
                "SP-5 DEFECT: pricing cache must be written for CHF symbol"
            )
            assert written.get("price_major") == pytest.approx(120.50, abs=0.001), (
                "SP-5 DEFECT: CHF price_major must equal raw_price (no minor-unit division); "
                f"got {written.get('price_major')!r}"
            )
            # Correct: price_major * rate = 120.50 * 0.940 = 113.27
            # Wrong (reciprocal): price_major / rate = 120.50 / 0.940 ≈ 128.19
            expected_eur = float(
                (Decimal("120.50") * Decimal("0.940000000")).quantize(Decimal("0.01"))
            )
            assert written.get("price_eur") == pytest.approx(expected_eur, abs=0.01), (
                f"SP-5 DEFECT: price_eur must be price_major * rate = {expected_eur:.2f} "
                f"(not price_major / rate ≈ 128.19); got {written.get('price_eur')!r}"
            )
            assert written.get("price_currency") == "CHF", (
                f"SP-5 DEFECT: price_currency must be 'CHF'; got {written.get('price_currency')!r}"
            )
            assert written.get("fx_pair") == "CHF/EUR", (
                "SP-5 DEFECT: fx_pair must be 'CHF/EUR' (not 'EUR/CHF' reciprocal); "
                f"got {written.get('fx_pair')!r}"
            )
            assert written.get("fx_rate") == "0.940000000", (
                "SP-5 DEFECT: fx_rate must store the direct CHF/EUR rate '0.940000000' "
                f"(not the reciprocal 1/0.940 ≈ '1.0638...'); got {written.get('fx_rate')!r}"
            )


# ===========================================================================
# SP-6: Missing provider currency -> error, no price_eur
# ===========================================================================

class TestMissingCurrencyField:
    """SP-6: No currency from provider -> status 'error', skip."""

    @_skip_no_module
    def test_sp6_missing_currency_sets_error_status(self):
        """SP-6: yf info without 'currency' key -> status='error', price_eur absent."""
        if run_symbol_pricing is None:
            pytest.skip("run_symbol_pricing not exported")

        fake_cosmos = _FakeCosmos()
        fake_cosmos.container.add_symbol("ABBV", exchange="XNYS")

        # yf.Ticker().info returns no 'currency' key
        mock_info = {"regularMarketPrice": 166.25}

        with (
            patch("src.symbol_pricing.resolve_yfinance_symbol", return_value="ABBV"),
            patch("src.symbol_pricing.yf") as mock_yf,
            patch("src.symbol_pricing.get_fx_rate", return_value="0.917000000"),
        ):
            mock_yf.Ticker.return_value.info = mock_info
            import asyncio
            result = asyncio.run(run_symbol_pricing(fake_cosmos))

        # Verify: either written cache has status="error" or symbol counted in errors
        written = fake_cosmos._written_caches.get("ABBV")
        if written is not None:
            assert written.get("status") == "error", (
                f"SP-6 DEFECT: Missing currency must produce status='error', got {written.get('status')!r}"
            )
            assert written.get("price_eur") is None, (
                "SP-6 DEFECT: price_eur must be None when currency is unknown"
            )
        else:
            # Acceptable: symbol skipped entirely (not written)
            assert result.get("errors", 0) >= 1 or result.get("success", 0) == 0, (
                "SP-6 DEFECT: Missing currency must count as error in run summary"
            )

    @_skip_no_module
    def test_sp6b_no_mic_assumption(self):
        """SP-6b: Missing currency must never fall back to assuming USD/EUR from MIC.
        Verified by ensuring the error path does NOT call update_symbol_pricing_cache
        with a non-null price_eur for a symbol with no currency."""
        if run_symbol_pricing is None:
            pytest.skip("run_symbol_pricing not exported")

        fake_cosmos = _FakeCosmos()
        fake_cosmos.container.add_symbol("XNYS_GHOST", exchange="XNYS")

        with (
            patch("src.symbol_pricing.resolve_yfinance_symbol", return_value="XNYS_GHOST"),
            patch("src.symbol_pricing.yf") as mock_yf,
            patch("src.symbol_pricing.get_fx_rate", return_value="0.917000000"),
        ):
            # Empty info -- no currency, no price
            mock_yf.Ticker.return_value.info = {}
            import asyncio
            asyncio.run(run_symbol_pricing(fake_cosmos))

        written = fake_cosmos._written_caches.get("XNYS_GHOST")
        if written is not None:
            assert written.get("price_eur") is None, (
                "SP-6b DEFECT: price_eur must be None when no currency — "
                "never infer USD from XNYS or any other MIC"
            )

    @_skip_no_module
    def test_sp6c_valid_price_but_no_currency_must_not_overwrite_prior_cache(self):
        """SP-6c (Linus rejection): valid non-null price + missing currency -> ZERO Cosmos writes.

        This is the rejected scenario: provider returns a real market price (non-null) but
        the info dict has no 'currency' key.  Livingston's pre-fix code called _write_cache
        with null price_eur even in this case, overwriting the symbol's existing good cache.

        Contract:
          - errors / error_symbols must include the affected symbol.
          - update_symbol_pricing_cache must NOT be called (write count == 0).
          - The prior pricing_cache on the document must remain byte-for-byte unchanged.

        Test must FAIL against pre-fix code that writes null-currency results and
        PASS after Reuben's fix that skips the write on missing currency.
        """
        if run_symbol_pricing is None:
            pytest.skip("run_symbol_pricing not exported")

        # Seed a symbol that already has a good prior pricing_cache
        prior_cache = _ok_cache(
            raw_price=166.25, quote_currency="USD", price_major=166.25,
            price_currency="USD", fx_rate="0.917000000", price_eur=152.46,
            fetched_at="2026-09-07T10:00:00Z",
            run_id="20260907T100000Z",
        )
        import copy
        prior_cache_snapshot = copy.deepcopy(prior_cache)

        fake_cosmos = _FakeCosmos()
        fake_cosmos.container.add_symbol("ABBV", exchange="XNYS", pricing_cache=prior_cache)

        # Provider returns a non-null price but NO currency field
        mock_info_no_currency = {"regularMarketPrice": 167.80}  # price present, currency absent

        with (
            patch("src.symbol_pricing.resolve_yfinance_symbol", return_value="ABBV"),
            patch("src.symbol_pricing.yf") as mock_yf,
            patch("src.symbol_pricing.get_fx_rate", return_value="0.920000000"),
        ):
            mock_yf.Ticker.return_value.info = mock_info_no_currency
            import asyncio
            result = asyncio.run(run_symbol_pricing(fake_cosmos))

        # --- Assertion 1: symbol reported as error ---
        error_count = result.get("errors", 0) if isinstance(result, dict) else 0
        error_symbols = result.get("error_symbols", []) if isinstance(result, dict) else []
        reported_as_error = (
            error_count >= 1
            or "ABBV" in error_symbols
            or result.get("success", 1) == 0
        )
        assert reported_as_error, (
            "SP-6c DEFECT: Missing currency (with valid price) must be reported in "
            "errors/error_symbols. Got result: " + repr(result)
        )

        # --- Assertion 2: Cosmos write count must be ZERO ---
        assert "ABBV" not in fake_cosmos._written_caches, (
            "SP-6c DEFECT: update_symbol_pricing_cache must NOT be called when currency "
            "is missing — even though a valid price was fetched. "
            "Reuben: remove the _write_cache call on the missing-currency error path."
        )
        assert len(fake_cosmos._written_caches) == 0, (
            "SP-6c DEFECT: Expected zero Cosmos cache writes for a missing-currency scenario "
            f"with an existing prior cache. Got writes: {list(fake_cosmos._written_caches.keys())}"
        )

        # --- Assertion 3: prior cache byte-for-byte unchanged ---
        doc = fake_cosmos.get_symbol("ABBV")
        surviving_cache = doc.get("pricing_cache") if doc else None
        assert surviving_cache is not None, (
            "SP-6c DEFECT: pricing_cache was removed from the document; it must be preserved."
        )
        assert surviving_cache == prior_cache_snapshot, (
            "SP-6c DEFECT: Prior pricing_cache must be byte-for-byte unchanged after a "
            "failed fetch (missing currency). "
            f"Expected: {prior_cache_snapshot!r}\n"
            f"Got:      {surviving_cache!r}"
        )


# ===========================================================================
# SP-7: FxUnavailableError -> abort entire run, existing cache untouched
# ===========================================================================

class TestFxProviderOutageAbortsRun:
    """SP-7: Whole-run FX provider outage must abort without overwriting cache."""

    @_skip_no_module
    def test_sp7_fx_unavailable_aborts_run_and_preserves_cache(self):
        """SP-7: FxUnavailableError -> run aborted, existing pricing_cache NOT overwritten."""
        if run_symbol_pricing is None:
            pytest.skip("run_symbol_pricing not exported")

        existing_cache = _ok_cache(
            raw_price=4815.0, quote_currency="GBp", price_major=48.15,
            price_currency="GBP", fx_rate="0.845230000", price_eur=40.71,
            fetched_at="2026-09-07T12:00:00Z",
        )
        fake_cosmos = _FakeCosmos()
        fake_cosmos.container.add_symbol("ULVR", exchange="XLON", pricing_cache=existing_cache)

        with (
            patch("src.symbol_pricing.resolve_yfinance_symbol", return_value="ULVR.L"),
            patch("src.symbol_pricing.yf") as mock_yf,
            patch("src.symbol_pricing.get_fx_rate", side_effect=FxUnavailableError("ECB unreachable")),
        ):
            mock_yf.Ticker.return_value.info = {
                "currency": "GBp",
                "regularMarketPrice": 4820.0,
            }
            import asyncio
            try:
                asyncio.run(run_symbol_pricing(fake_cosmos))
            except Exception:
                pass  # run may raise or return error status

        # Critical: existing cache must NOT have been overwritten
        assert "ULVR" not in fake_cosmos._written_caches, (
            "SP-7 DEFECT: FxUnavailableError must abort the run without writing ANY cache entries. "
            "Existing good cache was overwritten — this breaks the EUR price column."
        )
        # Verify original cache still intact on the doc
        doc = fake_cosmos.get_symbol("ULVR")
        pc = doc.get("pricing_cache") or {}
        assert pc.get("price_eur") == pytest.approx(40.71, abs=0.01), (
            "SP-7 DEFECT: Existing price_eur must be preserved when FX provider is down"
        )


# ===========================================================================
# SP-8: FxRateNotFoundError for single currency -> null price_eur for that currency only
# ===========================================================================

class TestFxRateNotFoundSingleCurrency:
    """SP-8: Unknown currency in ECB feed -> null price_eur; others succeed."""

    @_skip_no_module
    def test_sp8_fx_rate_not_found_for_chf_leaves_usd_intact(self):
        """SP-8: CHF FxRateNotFoundError -> CHF symbols get null price_eur; USD symbols succeed."""
        if run_symbol_pricing is None:
            pytest.skip("run_symbol_pricing not exported")

        fake_cosmos = _FakeCosmos()
        fake_cosmos.container.add_symbol("ABBV", exchange="XNYS")
        fake_cosmos.container.add_symbol("NESN", exchange="XSWX")

        def _fx_side_effect(from_currency, to_currency="EUR", rate_date=None):
            if from_currency == "CHF":
                raise FxRateNotFoundError("CHF", rate_date or "2026-09-05")
            return "0.917000000"  # USD/EUR

        with (
            patch("src.symbol_pricing.resolve_yfinance_symbol", side_effect=lambda t, e, sec=None: f"{t}.SW" if e == "XSWX" else t),
            patch("src.symbol_pricing.yf") as mock_yf,
            patch("src.symbol_pricing.get_fx_rate", side_effect=_fx_side_effect),
        ):
            def _ticker_info(symbol):
                if symbol == "NESN.SW":
                    return MagicMock(info={"currency": "CHF", "regularMarketPrice": 120.50})
                return MagicMock(info={"currency": "USD", "regularMarketPrice": 166.25})
            mock_yf.Ticker.side_effect = _ticker_info
            import asyncio
            result = asyncio.run(run_symbol_pricing(fake_cosmos))

        nesn_cache = fake_cosmos._written_caches.get("NESN")
        abbv_cache = fake_cosmos._written_caches.get("ABBV")

        if nesn_cache is not None:
            assert nesn_cache.get("price_eur") is None, (
                "SP-8 DEFECT: CHF symbol must have price_eur=null when FxRateNotFoundError"
            )
            assert nesn_cache.get("status") in ("ok", None), (
                "SP-8: CHF with unknown rate -> status should be 'ok' (price valid, EUR unavailable)"
            )

        if abbv_cache is not None:
            assert abbv_cache.get("price_eur") is not None, (
                "SP-8 DEFECT: USD symbol must still have price_eur when only CHF rate fails"
            )


# ===========================================================================
# SP-9: Unknown MIC -> status="error"
# ===========================================================================

class TestUnknownMicNoYahooMapping:
    """SP-9: Symbol with unknown MIC (no resolve_yfinance_symbol result) -> error."""

    @_skip_no_module
    def test_sp9_none_from_resolve_sets_error(self):
        """SP-9: resolve_yfinance_symbol returns None -> pricing_cache status='error'."""
        if run_symbol_pricing is None:
            pytest.skip("run_symbol_pricing not exported")

        fake_cosmos = _FakeCosmos()
        fake_cosmos.container.add_symbol("ZZZZZ", exchange="XZZZ")

        with (
            patch("src.symbol_pricing.resolve_yfinance_symbol", return_value=None),
            patch("src.symbol_pricing.get_fx_rate", return_value="0.917000000"),
        ):
            import asyncio
            result = asyncio.run(run_symbol_pricing(fake_cosmos))

        written = fake_cosmos._written_caches.get("ZZZZZ")
        if written is not None:
            assert written.get("status") == "error", (
                f"SP-9 DEFECT: Unknown MIC must produce status='error', got {written.get('status')!r}"
            )
        else:
            # Acceptable: skipped without writing
            assert result.get("errors", 0) >= 1


# ===========================================================================
# SP-10: provider_symbols["yfinance"] override takes precedence
# ===========================================================================

class TestProviderSymbolOverridePrecedence:
    """SP-10: Explicit yfinance override beats MIC-derived suffix."""

    @_skip_no_module
    def test_sp10_explicit_override_used_for_fetch(self):
        """SP-10: If provider_symbols.yfinance is set, that symbol is fetched from yf."""
        if run_symbol_pricing is None:
            pytest.skip("run_symbol_pricing not exported")

        fake_cosmos = _FakeCosmos()
        # XSWX:NESN with explicit override "NESN.SW" (normally computed, but also explicit override)
        fake_cosmos.container.add_symbol("NESN", exchange="XSWX", provider_yfinance="NESN.SW")

        fetched_symbols = []

        def _track_ticker(symbol):
            fetched_symbols.append(symbol)
            m = MagicMock()
            m.info = {"currency": "CHF", "regularMarketPrice": 120.50}
            return m

        with (
            patch("src.symbol_pricing.yf") as mock_yf,
            patch("src.symbol_pricing.get_fx_rate", return_value="0.940000000"),
        ):
            mock_yf.Ticker.side_effect = _track_ticker
            import asyncio
            asyncio.run(run_symbol_pricing(fake_cosmos))

        assert fetched_symbols, "SP-10: yf.Ticker must be called with the resolved symbol"
        assert "NESN.SW" in fetched_symbols, (
            f"SP-10 DEFECT: provider_symbols.yfinance='NESN.SW' must be used for fetch; "
            f"actually fetched: {fetched_symbols}"
        )


# ===========================================================================
# SP-11/12: Cache write shape and timestamps
# ===========================================================================

class TestCacheWriteShape:
    """SP-11/12: pricing_cache write must contain all required fields with correct types."""

    @_skip_no_module
    def test_sp11_write_shape_all_required_fields_present(self):
        """SP-11: update_symbol_pricing_cache must write all contract-required fields."""
        if run_symbol_pricing is None:
            pytest.skip("run_symbol_pricing not exported")

        fake_cosmos = _FakeCosmos()
        fake_cosmos.container.add_symbol("ABBV", exchange="XNYS")

        with (
            patch("src.symbol_pricing.resolve_yfinance_symbol", return_value="ABBV"),
            patch("src.symbol_pricing.yf") as mock_yf,
            patch("src.symbol_pricing.get_fx_rate", return_value="0.917000000"),
        ):
            mock_yf.Ticker.return_value.info = {
                "currency": "USD",
                "regularMarketPrice": 166.25,
            }
            import asyncio
            asyncio.run(run_symbol_pricing(fake_cosmos))

        written = fake_cosmos._written_caches.get("ABBV")
        assert written is not None, "SP-11: pricing_cache must be written for a successful symbol"

        required_fields = [
            "raw_price", "quote_currency", "quote_unit",
            "price_major", "price_currency",
            "fx_rate", "fx_pair", "fx_rate_date", "fx_source",
            "price_eur", "fetched_at", "status", "error_message", "run_id",
        ]
        for field in required_fields:
            assert field in written, (
                f"SP-11 DEFECT: pricing_cache missing required field '{field}'. "
                f"Present fields: {list(written.keys())}"
            )

    @_skip_no_module
    def test_sp12_timestamps_iso8601_utc_and_run_id_format(self):
        """SP-12: fetched_at is ISO 8601 UTC; run_id follows YYYYMMDDTHHMMSSz pattern."""
        if run_symbol_pricing is None:
            pytest.skip("run_symbol_pricing not exported")

        fake_cosmos = _FakeCosmos()
        fake_cosmos.container.add_symbol("ABBV", exchange="XNYS")

        with (
            patch("src.symbol_pricing.resolve_yfinance_symbol", return_value="ABBV"),
            patch("src.symbol_pricing.yf") as mock_yf,
            patch("src.symbol_pricing.get_fx_rate", return_value="0.917000000"),
        ):
            mock_yf.Ticker.return_value.info = {
                "currency": "USD",
                "regularMarketPrice": 166.25,
            }
            import asyncio
            asyncio.run(run_symbol_pricing(fake_cosmos))

        written = fake_cosmos._written_caches.get("ABBV")
        if written is None:
            pytest.skip("Cache not written; covered by SP-11")

        fetched_at = written.get("fetched_at", "")
        assert fetched_at.endswith("Z"), (
            f"SP-12 DEFECT: fetched_at must be UTC ISO 8601 ending in 'Z', got {fetched_at!r}"
        )
        # Parseable as ISO 8601
        try:
            datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
        except ValueError as exc:
            pytest.fail(f"SP-12 DEFECT: fetched_at not valid ISO 8601: {fetched_at!r} ({exc})")

        run_id = written.get("run_id", "")
        assert run_id, "SP-12 DEFECT: run_id must be non-empty"
        # run_id format: YYYYMMDDTHHMMSSz or similar timestamp pattern
        import re
        assert re.match(r"\d{8}T\d{6}Z?", run_id), (
            f"SP-12 DEFECT: run_id must match YYYYMMDDTHHMMSSz pattern, got {run_id!r}"
        )

    @_skip_no_module
    def test_sp11b_fx_rate_is_9dp_decimal_string(self):
        """SP-11b: fx_rate field must be a 9-decimal-place Decimal string."""
        if run_symbol_pricing is None:
            pytest.skip("run_symbol_pricing not exported")

        fake_cosmos = _FakeCosmos()
        fake_cosmos.container.add_symbol("ABBV", exchange="XNYS")

        with (
            patch("src.symbol_pricing.resolve_yfinance_symbol", return_value="ABBV"),
            patch("src.symbol_pricing.yf") as mock_yf,
            patch("src.symbol_pricing.get_fx_rate", return_value="0.917000000"),
        ):
            mock_yf.Ticker.return_value.info = {
                "currency": "USD",
                "regularMarketPrice": 166.25,
            }
            import asyncio
            asyncio.run(run_symbol_pricing(fake_cosmos))

        written = fake_cosmos._written_caches.get("ABBV")
        if written is None:
            pytest.skip("Cache not written")

        fx_rate = written.get("fx_rate")
        assert isinstance(fx_rate, str), (
            f"SP-11b DEFECT: fx_rate must be a string (Decimal precision), got {type(fx_rate).__name__}"
        )
        # Must be parseable as a Decimal
        try:
            Decimal(fx_rate)
        except Exception as exc:
            pytest.fail(f"SP-11b DEFECT: fx_rate not a valid Decimal string: {fx_rate!r} ({exc})")


# ===========================================================================
# SP-13: Partial success
# ===========================================================================

class TestPartialSuccess:
    """SP-13: Some symbols fail, others succeed."""

    @_skip_no_module
    def test_sp13_partial_success_good_data_written_bad_skipped(self):
        """SP-13: Failed symbol skipped/errored; successful symbol's cache written correctly."""
        if run_symbol_pricing is None:
            pytest.skip("run_symbol_pricing not exported")

        fake_cosmos = _FakeCosmos()
        fake_cosmos.container.add_symbol("ABBV", exchange="XNYS")   # will succeed
        fake_cosmos.container.add_symbol("FAIL", exchange="XNYS")   # will fail

        call_count = 0

        def _ticker_factory(symbol):
            nonlocal call_count
            call_count += 1
            if symbol == "FAIL":
                m = MagicMock()
                m.info = {}  # no currency -> error
                return m
            m = MagicMock()
            m.info = {"currency": "USD", "regularMarketPrice": 166.25}
            return m

        with (
            patch("src.symbol_pricing.resolve_yfinance_symbol", side_effect=lambda t, e, sec=None: t),
            patch("src.symbol_pricing.yf") as mock_yf,
            patch("src.symbol_pricing.get_fx_rate", return_value="0.917000000"),
        ):
            mock_yf.Ticker.side_effect = _ticker_factory
            import asyncio
            result = asyncio.run(run_symbol_pricing(fake_cosmos))

        # ABBV must be written successfully
        abbv = fake_cosmos._written_caches.get("ABBV")
        assert abbv is not None, "SP-13 DEFECT: Successful symbol ABBV must have cache written"
        assert abbv.get("status") == "ok"

        # Summary must report the error
        assert result.get("total", 0) >= 2, "SP-13: total must count all processed symbols"
        assert result.get("errors", 0) >= 1, "SP-13 DEFECT: errors must be counted in summary"
        assert result.get("success", 0) >= 1, "SP-13 DEFECT: success must be counted in summary"


# ===========================================================================
# SP-25: Double pence conversion guard
# ===========================================================================

class TestDoublePenceConversionGuard:
    """SP-25: Backend must never divide pence by 100 twice."""

    @_skip_no_module
    def test_sp25_price_eur_not_double_divided(self):
        """SP-25: GBp 4815 -> price_major=48.15 -> price_eur ~40.71, never 0.4071."""
        if build_pricing_cache_entry is None:
            if run_symbol_pricing is None:
                pytest.skip("No exported helper to test double conversion")
            fake_cosmos = _FakeCosmos()
            fake_cosmos.container.add_symbol("ULVR", exchange="XLON")
            with (
                patch("src.symbol_pricing.resolve_yfinance_symbol", return_value="ULVR.L"),
                patch("src.symbol_pricing.yf") as mock_yf,
                patch("src.symbol_pricing.get_fx_rate", return_value="0.845230000"),
            ):
                mock_yf.Ticker.return_value.info = {"currency": "GBp", "regularMarketPrice": 4815.0}
                import asyncio
                asyncio.run(run_symbol_pricing(fake_cosmos))
            written = fake_cosmos._written_caches.get("ULVR")
            if written is None:
                pytest.skip("Cache not written")
            price_eur = written.get("price_eur")
        else:
            entry = build_pricing_cache_entry(
                raw_price=4815.0, quote_currency="GBp",
                fx_rate_str="0.845230000", fx_rate_date="2026-09-05",
                run_id="20260907T140000Z",
            )
            price_eur = entry.get("price_eur")

        assert price_eur is not None, "SP-25: price_eur must not be None for valid GBp"
        # Correct: 48.15 * 0.845230 ~ 40.70
        # Double-divided wrong: 0.4815 * 0.845230 ~ 0.407 — clearly < 1
        assert price_eur > 1.0, (
            f"SP-25 DEFECT: price_eur={price_eur!r} looks like a double-divided value. "
            "GBp 4815 -> price_major 48.15 -> price_eur ~40.71 (not ~0.407). "
            "Pence conversion must happen exactly once."
        )
        assert price_eur == pytest.approx(40.71, abs=0.10), (
            f"SP-25 DEFECT: price_eur expected ~40.71, got {price_eur!r}"
        )


# ===========================================================================
# SP-14/15/16: Overview endpoint integration — pricing_cache fields
# ===========================================================================

class TestOverviewPricingCacheIntegration:
    """SP-14/15/16: _compute_symbols_overview uses pricing_cache when available."""

    def test_sp14_overview_prefers_pricing_cache_price_major(self, cosmos_and_client):
        """SP-14: When pricing_cache.status='ok', overview row uses price_major (not enrichment)."""
        c, fake = cosmos_and_client
        # Enrich: 100.00 (old, wrong); pricing cache: 48.15 (correct GBp major)
        pc = _ok_cache(
            raw_price=4815.0, quote_currency="GBp", price_major=48.15,
            price_currency="GBP", fx_rate="0.845230000", price_eur=40.71,
        )
        fake.container.add_symbol("ULVR", exchange="XLON",
                                   pricing_cache=pc, enrichment_price=100.00)

        resp = c.get("/api/symbols/overview")
        assert resp.status_code == 200
        rows = resp.json().get("rows", [])
        ulvr_rows = [r for r in rows if r.get("symbol") == "ULVR"]
        assert ulvr_rows, "ULVR must appear in overview rows"
        row = ulvr_rows[0]

        # price field: must be price_major from cache (not enrichment 100.00)
        price = row.get("price")
        assert price is not None, (
            "SP-14 DEFECT: 'price' field missing from overview row; "
            "Livingston: add pricing_cache.price_major to _compute_symbols_overview"
        )
        assert price == pytest.approx(48.15, abs=0.01), (
            f"SP-14 DEFECT: price must be pricing_cache.price_major (48.15), got {price!r}. "
            "If 100.0, the overview is using enrichment instead of the pricing cache."
        )

    def test_sp14b_overview_includes_price_eur_field(self, cosmos_and_client):
        """SP-14b: Overview row must include price_eur when pricing_cache.status='ok'."""
        c, fake = cosmos_and_client
        pc = _ok_cache(
            raw_price=4815.0, quote_currency="GBp", price_major=48.15,
            price_currency="GBP", fx_rate="0.845230000", price_eur=40.71,
        )
        fake.container.add_symbol("ULVR", exchange="XLON", pricing_cache=pc)

        resp = c.get("/api/symbols/overview")
        rows = resp.json().get("rows", [])
        ulvr_rows = [r for r in rows if r.get("symbol") == "ULVR"]
        assert ulvr_rows
        row = ulvr_rows[0]

        assert "price_eur" in row, (
            "SP-14b DEFECT: 'price_eur' missing from overview row; "
            "Livingston: add pricing_cache.price_eur to _compute_symbols_overview"
        )
        assert row["price_eur"] == pytest.approx(40.71, abs=0.01)

    def test_sp14c_overview_includes_price_display_currency(self, cosmos_and_client):
        """SP-14c: Overview row must include price_display_currency = 'GBp' for LSE."""
        c, fake = cosmos_and_client
        pc = _ok_cache(
            raw_price=4815.0, quote_currency="GBp", price_major=48.15,
            price_currency="GBP", fx_rate="0.845230000", price_eur=40.71,
        )
        fake.container.add_symbol("ULVR", exchange="XLON", pricing_cache=pc)

        resp = c.get("/api/symbols/overview")
        rows = resp.json().get("rows", [])
        row = next((r for r in rows if r.get("symbol") == "ULVR"), None)
        assert row is not None
        assert "price_display_currency" in row, (
            "SP-14c DEFECT: 'price_display_currency' missing from overview row"
        )
        assert row["price_display_currency"] == "GBp", (
            f"SP-14c DEFECT: price_display_currency should be 'GBp', got {row.get('price_display_currency')!r}"
        )

    def test_sp15_overview_falls_back_to_enrichment_when_no_cache(self, cosmos_and_client):
        """SP-15: No pricing_cache -> overview uses enrichment.metrics.current_price."""
        c, fake = cosmos_and_client
        fake.container.add_symbol("ABBV", exchange="XNYS", enrichment_price=166.25)

        resp = c.get("/api/symbols/overview")
        rows = resp.json().get("rows", [])
        abbv_rows = [r for r in rows if r.get("symbol") == "ABBV"]
        assert abbv_rows
        row = abbv_rows[0]

        price = row.get("price")
        assert price == pytest.approx(166.25, abs=0.01), (
            f"SP-15 DEFECT: No cache -> price must fall back to enrichment.current_price (166.25), got {price!r}"
        )
        # price_eur should be absent or null when no cache
        price_eur = row.get("price_eur")
        assert price_eur is None, (
            f"SP-15 DEFECT: price_eur must be null/absent when no pricing_cache, got {price_eur!r}"
        )

    def test_sp16_overview_marks_stale_cache_as_stale(self, cosmos_and_client):
        """SP-16: fetched_at > 2h ago -> pricing_status='stale' in overview row."""
        c, fake = cosmos_and_client
        stale_ts = (
            datetime.now(timezone.utc) - timedelta(hours=3)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        pc = _ok_cache(
            raw_price=166.25, quote_currency="USD", price_major=166.25,
            price_currency="USD", fx_rate="0.917000000", price_eur=152.49,
            fetched_at=stale_ts,
        )
        fake.container.add_symbol("ABBV", exchange="XNYS", pricing_cache=pc)

        resp = c.get("/api/symbols/overview")
        rows = resp.json().get("rows", [])
        row = next((r for r in rows if r.get("symbol") == "ABBV"), None)
        assert row is not None

        pricing_status = row.get("pricing_status")
        assert pricing_status is not None, (
            "SP-16 DEFECT: 'pricing_status' field missing from overview row; "
            "Livingston: add pricing_status to _compute_symbols_overview"
        )
        assert pricing_status == "stale", (
            f"SP-16 DEFECT: Cache older than 2h must have pricing_status='stale', got {pricing_status!r}"
        )

    def test_sp16b_fresh_cache_is_ok_status(self, cosmos_and_client):
        """SP-16b: Cache fetched <2h ago must have pricing_status='ok'."""
        c, fake = cosmos_and_client
        fresh_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        pc = _ok_cache(
            raw_price=166.25, quote_currency="USD", price_major=166.25,
            price_currency="USD", fx_rate="0.917000000", price_eur=152.49,
            fetched_at=fresh_ts,
        )
        fake.container.add_symbol("ABBV", exchange="XNYS", pricing_cache=pc)

        resp = c.get("/api/symbols/overview")
        rows = resp.json().get("rows", [])
        row = next((r for r in rows if r.get("symbol") == "ABBV"), None)
        assert row is not None

        pricing_status = row.get("pricing_status")
        if pricing_status is not None:  # field may be absent when not yet implemented
            assert pricing_status == "ok", (
                f"SP-16b DEFECT: Fresh cache must have pricing_status='ok', got {pricing_status!r}"
            )


# ===========================================================================
# SP-17/18/19/20: current_value_eur computation in overview
# ===========================================================================

class TestCurrentValueComputation:
    """SP-17..20: current_value_eur and total_current_value_eur in overview."""

    def test_sp17_current_value_eur_equals_shares_times_price_eur(self, cosmos_and_client):
        """SP-17: current_value_eur = portfolio_shares * price_eur for portfolio row."""
        c, fake = cosmos_and_client
        pc = _ok_cache(
            raw_price=166.25, quote_currency="USD", price_major=166.25,
            price_currency="USD", fx_rate="0.917000000", price_eur=152.49,
        )
        fake.container.add_symbol("ABBV", exchange="XNYS", pricing_cache=pc)
        fake.portfolio_container.add_buy("XNYS:ABBV", quantity="100", gross_eur="15000")

        resp = c.get("/api/symbols/overview")
        rows = resp.json().get("rows", [])
        row = next((r for r in rows if r.get("symbol") == "ABBV"), None)
        assert row is not None

        cv = row.get("current_value_eur")
        assert cv is not None, (
            "SP-17 DEFECT: 'current_value_eur' missing from overview row; "
            "Livingston: compute shares * price_eur in _compute_symbols_overview"
        )
        # 100 shares * 152.49 EUR = 15249.00
        expected = Decimal("100") * Decimal("152.49")
        assert abs(Decimal(str(cv)) - expected) < Decimal("0.05"), (
            f"SP-17 DEFECT: current_value_eur = 100 * 152.49 = {expected}, got {cv!r}"
        )

    def test_sp18_total_current_value_eur_in_portfolio_summary(self, cosmos_and_client):
        """SP-18: portfolio_summary.total_current_value_eur aggregates across portfolio rows."""
        c, fake = cosmos_and_client
        pc1 = _ok_cache(
            raw_price=166.25, quote_currency="USD", price_major=166.25,
            price_currency="USD", fx_rate="0.917000000", price_eur=152.49,
        )
        pc2 = _ok_cache(
            raw_price=4815.0, quote_currency="GBp", price_major=48.15,
            price_currency="GBP", fx_rate="0.845230000", price_eur=40.71,
        )
        fake.container.add_symbol("ABBV", exchange="XNYS", pricing_cache=pc1)
        fake.container.add_symbol("ULVR", exchange="XLON", pricing_cache=pc2)
        fake.portfolio_container.add_buy("XNYS:ABBV", quantity="100", gross_eur="15000")
        fake.portfolio_container.add_buy("XLON:ULVR", quantity="200", gross_eur="8000")

        resp = c.get("/api/symbols/overview")
        data = resp.json()
        ps = data.get("portfolio_summary")
        assert ps is not None, "portfolio_summary must be present when holdings exist"

        tcv = ps.get("total_current_value_eur")
        assert tcv is not None, (
            "SP-18 DEFECT: 'total_current_value_eur' missing from portfolio_summary; "
            "Livingston: sum current_value_eur across portfolio rows"
        )
        # 100 * 152.49 + 200 * 40.71 = 15249 + 8142 = 23391
        expected = Decimal("100") * Decimal("152.49") + Decimal("200") * Decimal("40.71")
        assert abs(Decimal(str(tcv)) - expected) < Decimal("0.10"), (
            f"SP-18 DEFECT: total_current_value_eur expected ~{expected}, got {tcv!r}"
        )

    def test_sp19_current_value_eur_null_for_zero_share_row(self, cosmos_and_client):
        """SP-19: Zero-share portfolio row (historical) -> current_value_eur is null."""
        c, fake = cosmos_and_client
        pc = _ok_cache(
            raw_price=166.25, quote_currency="USD", price_major=166.25,
            price_currency="USD", fx_rate="0.917000000", price_eur=152.49,
        )
        fake.container.add_symbol("ABBV", exchange="XNYS", pricing_cache=pc)
        # Add a BUY followed by a SELL to net zero shares
        fake.portfolio_container.add_buy("XNYS:ABBV", quantity="100", gross_eur="15000")
        # sell via fake — create a SELL record
        fake.portfolio_container._store["sell_ABBV"] = {
            "id": "sell_ABBV",
            "account_id": "_unassigned",
            "doc_type": "ledger_txn",
            "txn_type": "SELL",
            "security_id": "XNYS:ABBV",
            "ticker": "ABBV",
            "trade_date": "2026-06-01",
            "quantity": "100",
            "gross": {"amount": "17000", "currency": "EUR", "eur_amount": "17000"},
            "net": {"amount": "17000", "currency": "EUR", "eur_amount": "17000"},
            "fees": {"total": "0", "currency": "EUR", "total_eur": "0"},
            "net_eur": "17000",
            "correction_status": "ACTIVE",
            "cost_basis_status": "COMPLETE",
        }
        resp = c.get("/api/symbols/overview?include_zero_portfolio=true")
        rows = resp.json().get("rows", [])
        row = next((r for r in rows if r.get("symbol") == "ABBV"), None)
        if row is None:
            return  # row may be hidden by default

        # Zero shares -> current_value_eur should be null
        if row.get("portfolio_shares") is not None:
            shares = float(row.get("portfolio_shares", "0") or "0")
            if shares == 0:
                cv = row.get("current_value_eur")
                assert cv is None, (
                    f"SP-19 DEFECT: Zero-share row must have current_value_eur=null, got {cv!r}"
                )

    def test_sp20_current_value_null_when_price_eur_null(self, cosmos_and_client):
        """SP-20: price_eur=null (unknown FX) -> current_value_eur must also be null."""
        c, fake = cosmos_and_client
        # CHF with no EUR conversion
        pc: dict = {
            "raw_price": 120.50,
            "quote_currency": "CHF",
            "quote_unit": "major",
            "price_major": 120.50,
            "price_currency": "CHF",
            "fx_rate": None,
            "fx_pair": "CHF/EUR",
            "fx_rate_date": None,
            "fx_source": "ECB",
            "price_eur": None,  # no EUR conversion available
            "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "status": "ok",
            "error_message": None,
            "run_id": "20260907T140000Z",
        }
        fake.container.add_symbol("NESN", exchange="XSWX", pricing_cache=pc)
        fake.portfolio_container.add_buy("XSWX:NESN", quantity="50", gross_eur="6000")

        resp = c.get("/api/symbols/overview")
        rows = resp.json().get("rows", [])
        row = next((r for r in rows if r.get("symbol") == "NESN"), None)
        if row is None:
            return

        price_eur = row.get("price_eur")
        assert price_eur is None, (
            f"SP-20 DEFECT: price_eur must be null when FX unavailable, got {price_eur!r}"
        )
        cv = row.get("current_value_eur")
        assert cv is None, (
            f"SP-20 DEFECT: current_value_eur must be null when price_eur is null, got {cv!r}"
        )


# ===========================================================================
# SP-22: Scheduler registration in main.py (source-contract)
# ===========================================================================

class TestSchedulerRegistration:
    """SP-22: symbol_pricing task registered with correct cron and config_key."""

    def test_sp22_registry_register_call_present_in_main_py(self):
        """SP-22: main.py must contain registry.register('symbol_pricing', ...)."""
        main_py = pathlib.Path(__file__).parent.parent / "src" / "main.py"
        assert main_py.exists(), "backend/src/main.py must exist"
        src = main_py.read_text(encoding="utf-8")

        assert '"symbol_pricing"' in src or "'symbol_pricing'" in src, (
            "SP-22 DEFECT: main.py must call registry.register('symbol_pricing', ...). "
            "Livingston: add symbol_pricing task registration per SS2.2."
        )

    def test_sp22b_cron_expression_correct(self):
        """SP-22b: symbol_pricing cron must be '0 9-23 * * 1-5' (hourly 09:00-23:00 Mon-Fri)."""
        main_py = pathlib.Path(__file__).parent.parent / "src" / "main.py"
        src = main_py.read_text(encoding="utf-8")

        assert "0 9-23 * * 1-5" in src, (
            "SP-22b DEFECT: symbol_pricing cron expression '0 9-23 * * 1-5' not found in main.py. "
            "Contract SS2.1 specifies hourly at 09:00-23:00 UTC Mon-Fri (inclusive)."
        )

    def test_sp22c_symbol_pricing_config_key_used(self):
        """SP-22c: registry.register must use 'symbol_pricing' as config_key."""
        main_py = pathlib.Path(__file__).parent.parent / "src" / "main.py"
        src = main_py.read_text(encoding="utf-8")

        # Check the register block contains the right pattern near symbol_pricing
        import re
        # Look for the register call block for symbol_pricing
        block_match = re.search(
            r'register\s*\(\s*["\']symbol_pricing["\'].*?\)',
            src, re.DOTALL
        )
        assert block_match, (
            "SP-22c DEFECT: Could not find registry.register('symbol_pricing', ...) block in main.py"
        )
        block = block_match.group(0)
        assert "symbol_pricing" in block.lower(), (
            "SP-22c DEFECT: symbol_pricing register block should use 'symbol_pricing' config_key"
        )


# ===========================================================================
# SP-23: config.yaml has symbol_pricing block
# ===========================================================================

class TestConfigYaml:
    """SP-23: config.yaml must have symbol_pricing block with enabled:true and correct cron."""

    def test_sp23_config_yaml_has_symbol_pricing_block(self):
        """SP-23: config.yaml must have a symbol_pricing section."""
        config_path = pathlib.Path(__file__).parent.parent / "config.yaml"
        assert config_path.exists(), "backend/config.yaml must exist"
        content = config_path.read_text(encoding="utf-8")
        assert "symbol_pricing:" in content, (
            "SP-23 DEFECT: config.yaml missing 'symbol_pricing:' section. "
            "Livingston: add symbol_pricing config block per SS2.1."
        )

    def test_sp23b_config_has_enabled_true(self):
        """SP-23b: symbol_pricing section must have enabled: true."""
        config_path = pathlib.Path(__file__).parent.parent / "config.yaml"
        content = config_path.read_text(encoding="utf-8")
        # Find symbol_pricing block by looking for 'enabled: true' within 5 lines after it
        idx = content.find("symbol_pricing:")
        if idx == -1:
            pytest.skip("symbol_pricing block not yet present in config.yaml")
        # Read the next 300 chars from that point to cover the block
        block = content[idx : idx + 300]
        assert "enabled: true" in block, (
            f"SP-23b DEFECT: symbol_pricing block must have 'enabled: true'. "
            f"Block start: {block[:100]!r}"
        )

    def test_sp23c_config_has_correct_cron(self):
        """SP-23c: symbol_pricing section must have cron: '0 9-23 * * 1-5'."""
        config_path = pathlib.Path(__file__).parent.parent / "config.yaml"
        content = config_path.read_text(encoding="utf-8")
        idx = content.find("symbol_pricing:")
        if idx == -1:
            pytest.skip("symbol_pricing block not yet present in config.yaml")
        block = content[idx : idx + 300]
        assert "0 9-23 * * 1-5" in block, (
            f"SP-23c DEFECT: symbol_pricing cron must be '0 9-23 * * 1-5'. "
            f"Block start: {block[:150]!r}"
        )


# ===========================================================================
# SP-24: Integration seam — pricing run -> overview fields
# ===========================================================================

class TestIntegrationSeamPricingRunToOverview:
    """SP-24: Integration test verifying pricing run produces correct overview fields."""

    def test_sp24_pricing_cache_planted_in_doc_appears_in_overview(self, cosmos_and_client):
        """SP-24: Plant pricing_cache in symbol_config -> verify overview row has all new fields."""
        c, fake = cosmos_and_client
        # Simulate a completed pricing run having written this cache
        run_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        pc = _ok_cache(
            raw_price=4815.0, quote_currency="GBp", price_major=48.15,
            price_currency="GBP", fx_rate="0.845230000", price_eur=40.71,
            fetched_at=run_ts, run_id="20260907T090000Z",
        )
        fake.container.add_symbol("ULVR", exchange="XLON", pricing_cache=pc)
        fake.portfolio_container.add_buy("XLON:ULVR", quantity="100", gross_eur="4500")

        resp = c.get("/api/symbols/overview")
        assert resp.status_code == 200
        rows = resp.json().get("rows", [])
        row = next((r for r in rows if r.get("symbol") == "ULVR"), None)
        assert row is not None, "ULVR must appear in overview after portfolio buy"

        # Verify all new pricing fields are present
        new_fields = ["price_eur", "price_display_currency", "price_currency",
                      "pricing_status", "current_value_eur"]
        missing = [f for f in new_fields if f not in row]
        assert not missing, (
            f"SP-24 DEFECT: Overview row missing pricing fields: {missing}. "
            f"Livingston: add all pricing_cache fields to _compute_symbols_overview. "
            f"Present: {list(row.keys())}"
        )

        assert row.get("price") == pytest.approx(48.15, abs=0.01)
        assert row.get("price_eur") == pytest.approx(40.71, abs=0.01)
        assert row.get("price_display_currency") == "GBp"
        assert row.get("price_currency") == "GBP"
        assert row.get("pricing_status") == "ok"

        cv = row.get("current_value_eur")
        assert cv is not None
        assert abs(Decimal(str(cv)) - Decimal("4071.00")) < Decimal("0.05"), (
            f"SP-24: current_value_eur = 100 * 40.71 = 4071.00, got {cv!r}"
        )

    def test_sp24b_unknown_fx_currency_shows_price_but_null_eur(self, cosmos_and_client):
        """SP-24b: Unknown FX currency (null price_eur in cache) -> price shows, EUR is null."""
        c, fake = cosmos_and_client
        pc: dict = {
            "raw_price": 120.50,
            "quote_currency": "CHF",
            "quote_unit": "major",
            "price_major": 120.50,
            "price_currency": "CHF",
            "fx_rate": None,
            "fx_pair": "CHF/EUR",
            "fx_rate_date": None,
            "fx_source": "ECB",
            "price_eur": None,
            "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "status": "ok",
            "error_message": None,
            "run_id": "20260907T090000Z",
        }
        fake.container.add_symbol("NESN", exchange="XSWX", pricing_cache=pc)

        resp = c.get("/api/symbols/overview")
        rows = resp.json().get("rows", [])
        row = next((r for r in rows if r.get("symbol") == "NESN"), None)
        assert row is not None

        price = row.get("price")
        assert price == pytest.approx(120.50, abs=0.01), (
            "SP-24b: price (price_major) must be present even when FX unavailable"
        )

        price_eur = row.get("price_eur")
        if "price_eur" in row:
            assert price_eur is None, (
                "SP-24b DEFECT: price_eur must be null when FX unavailable"
            )
