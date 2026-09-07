"""Independent regression coverage for repair_provider_symbols_enag_micct_ulvr.py.

Contract: .squad/decisions/inbox/danny-provider-symbol-corrections-enag-micct-ulvr.md
Author: Reuben (independent of Linus, who owns the product script — no prior
authorship on this artifact, so no lockout applies; clean division of labor
per the contract's own "Assignment" section).

This repair is narrower than the PEP/AD identity repairs: all three
canonical security_ids are already correct and never change. The ONLY
mutation in scope is `security_master.provider_symbols.{yfinance,tradingview}`
for three securities, each independently gated behind a live-provider
verification check, followed by a synchronous, non-fatal enrichment rerun.

Exact target spec (from the contract's source-facts table / the script's
own TARGET_SPECS):
  XMAD:ENAG   ENAG  -> yfinance "ENG.MC"   tradingview "BME-ENG"       (must verify company too)
  XAMS:MICCT  MICCT -> yfinance "MICC.AS"  tradingview "EURONEXT-MICC"
  XAMS:ULVR   ULVR  -> yfinance "UNA.AS"   tradingview "EURONEXT-UNA"

Test IDs: PSC-1 ... PSC-N (sequential within each class).

Coverage:
  PSC-0   Ungated: the ALREADY-EXISTING resolve_yfinance_symbol /
          resolve_tradingview_symbol / validate_provider_symbols mechanism
          (no new script needed) actually produces the exact corrected
          symbols this repair depends on, and rejects a malformed override.
  PSC-1   --audit (dry_run) produces zero writes across both containers.
  PSC-2   Per-security independence: a single security's failed provider
          verification does not block the other two from being corrected.
  PSC-3   Currency / exchange(MIC) / company-name corroboration checks each
          independently abort that security's correction on mismatch.
  PSC-4   Provider unavailable/unreachable/missing-fields aborts that
          security's correction (fail closed), not a crash.
  PSC-5   All three securities failing verification -> exit_code=2, zero
          writes anywhere.
  PSC-6   Complete, checksum-bearing backup exists before the first mutation.
  PSC-7   provider_symbols merge preserves pre-existing unrelated keys
          (merge, not replace); validate_provider_symbols reuse.
  PSC-8   Every other security_master field is byte-identical before/after.
  PSC-9   config_* and ledger_txn docs are never written, under any
          circumstance (success, partial failure, or full failure).
  PSC-10  Synchronous enrichment is invoked, once per corrected security,
          with the canonical local ticker + the corrected yf_symbol — and
          only after that security's provider_symbols write succeeded.
  PSC-11  Enrichment persistence: cosmos.update_symbol_enrichment and
          cosmos.record_enrichment_snapshot are called with non-empty
          technicals/quality/entry/momentum fields.
  PSC-12  Enrichment failure is surfaced non-fatally: the provider_symbols
          correction is NOT rolled back, but enrichment is recorded as
          unverified (exit_code=3 per the contract's §5 exit-code table).
  PSC-13  Idempotent re-run: a second --apply after full success performs
          zero additional security_master writes, but still re-runs the
          enrichment-verification check.
  PSC-14  --restore reverts exactly the 3 security_master docs from backup;
          never touches config/ledger/portfolio.
  PSC-15  TradingView overrides are exact (BME-ENG / EURONEXT-MICC /
          EURONEXT-UNA), not derived from the non-override MIC-suffix path.

KNOWN PRODUCT-SCRIPT DEFECT FOUND WHILE WRITING THESE TESTS (reported, not
fixed — script is read-only for this revision): in
`verify_provider_symbol()`, when no `yf_fetcher` is injected, the script
does `YFinanceFetcher(spec["expected_yfinance"])` — passing the *ticker
string* positionally into `YFinanceFetcher.__init__(requests_per_minute:
int = 60, ...)`. This will raise `TypeError` on `60.0 / requests_per_minute`
the moment the script runs for real (CLI, no injected fetcher) — i.e. every
real (non-test) invocation. All tests below inject `yf_fetcher=` explicitly
and are therefore unaffected, but this must be fixed by Linus before this
script can run in production. See the final report for detail.

All tests are hermetic (no real Cosmos, no network, no production).
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

import pytest

from azure.cosmos.exceptions import CosmosResourceNotFoundError, CosmosHttpResponseError

# ---------------------------------------------------------------------------
# PSC-0 dependencies — these already exist today and are NOT gated; they are
# the exact reused mechanism the new script depends on (per the contract:
# "the existing provider_symbols override precedence ... is reused verbatim,
# not duplicated").
# ---------------------------------------------------------------------------
from src.portfolio.provider_symbols import (
    resolve_yfinance_symbol,
    resolve_tradingview_symbol,
    validate_provider_symbols,
)


# ---------------------------------------------------------------------------
# Conditional import — all gated tests skip until Linus creates the script.
# ---------------------------------------------------------------------------

try:
    from scripts.repair_provider_symbols_enag_micct_ulvr import (
        audit_repair,
        apply_repair,
        restore_repair,
        verify_provider_symbol,
        RepairReport,
        RepairBackup,
        SecurityRepairResult,
        TARGET_SPECS,
    )
    _SCRIPT_AVAILABLE = True
except ImportError:
    _SCRIPT_AVAILABLE = False
    audit_repair = apply_repair = restore_repair = verify_provider_symbol = None  # type: ignore
    RepairReport = RepairBackup = SecurityRepairResult = None  # type: ignore
    TARGET_SPECS = ()  # type: ignore

_skip = pytest.mark.skipif(
    not _SCRIPT_AVAILABLE,
    reason=(
        "backend/scripts/repair_provider_symbols_enag_micct_ulvr.py not yet "
        "created. Linus: implement per "
        "danny-provider-symbol-corrections-enag-micct-ulvr.md."
    ),
)


# ---------------------------------------------------------------------------
# Fake Cosmos containers (self-contained — not shared with any other test
# module/author's fixtures)
# ---------------------------------------------------------------------------

_SYSTEM_KEYS = frozenset({"_rid", "_self", "_etag", "_attachments", "_ts"})


class FakeSymbolsContainer:
    """In-memory symbols container: security_master + symbol_config docs.

    Partition key is the ticker/symbol, matching the real container's
    convention used throughout this codebase's other repair-script tests.
    """

    def __init__(self):
        self._store: Dict[tuple, dict] = {}
        self.create_calls: list = []
        self.replace_calls: list = []
        self.delete_calls: list = []
        # Optional hook invoked on every mutating call, BEFORE it is applied
        # — used by PSC-6 to assert backup-before-mutation ordering.
        self.before_write_hook = None

    def seed(self, doc: dict) -> dict:
        pk = doc.get("symbol", doc.get("ticker"))
        full = {**doc, "_etag": f"etag-{doc['id']}-v0"}
        self._store[(pk, doc["id"])] = full
        return dict(full)

    def read_item(self, item: str, partition_key: str) -> dict:
        key = (partition_key, item)
        if key not in self._store:
            raise CosmosResourceNotFoundError(message="not found", response=None)
        return dict(self._store[key])

    def create_item(self, body: dict) -> dict:
        if self.before_write_hook:
            self.before_write_hook()
        pk = body.get("symbol", body.get("ticker"))
        key = (pk, body["id"])
        if key in self._store:
            raise CosmosHttpResponseError(status_code=409, message="Conflict", response=None)
        new_etag = f"etag-{body['id']}-created"
        doc = {**body, "_etag": new_etag}
        self._store[key] = doc
        self.create_calls.append({"id": body["id"], "body": dict(body)})
        return dict(doc)

    def replace_item(
        self,
        item: str,
        body: dict,
        *,
        etag: Optional[str] = None,
        match_condition=None,
        **kw,
    ) -> dict:
        if self.before_write_hook:
            self.before_write_hook()
        for key, stored in list(self._store.items()):
            if stored.get("id") == item:
                if etag and stored.get("_etag") != etag:
                    raise CosmosHttpResponseError(
                        status_code=412, message="Precondition Failed", response=None
                    )
                new_etag = f"etag-{item}-r{len(self.replace_calls) + 1}"
                updated = {**body, "_etag": new_etag}
                self._store[key] = updated
                self.replace_calls.append({"id": item, "body": dict(body)})
                return dict(updated)
        raise CosmosResourceNotFoundError(message="not found", response=None)

    def delete_item(self, item: str, partition_key: str, **kw) -> None:
        key = (partition_key, item)
        if key not in self._store:
            self.delete_calls.append({"id": item, "not_found": True})
            return
        del self._store[key]
        self.delete_calls.append({"id": item, "not_found": False})

    @property
    def write_count(self) -> int:
        return len(self.create_calls) + len(self.replace_calls) + len(self.delete_calls)


class FakePortfolioContainer:
    """In-memory portfolio container — ledger_txn docs only. This repair
    must NEVER write here; tests assert `write_count == 0` throughout.

    `query_items` special-cases a `SELECT VALUE COUNT(1)` query shape (used
    by the real script's ledger reference-count check) by returning a
    single-element list containing the match count, mirroring Cosmos's
    scalar VALUE-query behavior.
    """

    def __init__(self):
        self._store: Dict[tuple, dict] = {}
        self.replace_calls: list = []
        self.create_calls: list = []
        self.delete_calls: list = []

    def seed_movement(self, movement_id: str, account_id: str, security_id: str) -> dict:
        doc = {
            "id": movement_id,
            "doc_type": "ledger_txn",
            "account_id": account_id,
            "security_id": security_id,
            "quantity": 10.0,
            "gross": {"amount": 100.0, "currency": "EUR"},
            "fees": {"amount": 1.0, "currency": "EUR"},
            "net": {"amount": 99.0, "currency": "EUR"},
            "trade_date": "2024-01-15",
            "_etag": f"etag-{movement_id}-v0",
        }
        self._store[(account_id, movement_id)] = doc
        return dict(doc)

    def query_items(
        self,
        query: str = "",
        parameters=None,
        enable_cross_partition_query: bool = False,
        partition_key: Optional[str] = None,
    ):
        param_map = {p["name"]: p["value"] for p in (parameters or [])}
        matched = []
        for (pk, _did), doc in self._store.items():
            if partition_key and pk != partition_key:
                continue
            if "@security_id" in param_map and doc.get("security_id") != param_map["@security_id"]:
                continue
            matched.append(dict(doc))
        if "COUNT(1)" in query.upper():
            return iter([len(matched)])
        return iter(matched)

    @property
    def write_count(self) -> int:
        return len(self.replace_calls) + len(self.create_calls) + len(self.delete_calls)


# ---------------------------------------------------------------------------
# Fake live-provider (mimics src.yfinance_fetcher.YFinanceFetcher — no
# network, ever). Injected via the `yf_fetcher=` kwarg (the real DI name
# implemented by the script; matches the PEP currency-repair convention).
# ---------------------------------------------------------------------------

class _FakeProvider:
    """`.get_ticker_data(symbol)` keyed by the candidate Yahoo symbol
    (e.g. "ENG.MC"). Missing keys simulate a 404/unreachable result;
    an exception instance simulates a raised network error."""

    def __init__(self, responses: Optional[Dict[str, Any]] = None):
        self._responses = responses or {}
        self.calls: List[str] = []

    def get_ticker_data(self, symbol: str) -> Optional[dict]:
        self.calls.append(symbol)
        if symbol not in self._responses:
            return None
        resp = self._responses[symbol]
        if isinstance(resp, BaseException):
            raise resp
        return resp


def _info(currency="EUR", exchange="", full_exchange_name="", long_name="") -> dict:
    return {
        "info": {
            "currency": currency,
            "exchange": exchange,
            "fullExchangeName": full_exchange_name,
            "longName": long_name,
        }
    }


# Canonical "everything corroborates" responses, matching the real script's
# `_REPAIR_SCOPED_MIC_EXCHANGE_HINTS` (XMAD -> MCE/MADRID/BME, XAMS ->
# AMS/AMSTERDAM/EURONEXT) and each security's own on-file company_name.
_GOOD_RESPONSES = {
    "ENG.MC": _info(exchange="MCE", full_exchange_name="Madrid Stock Exchange",
                     long_name="Enagas Sociedad Anonima"),
    "MICC.AS": _info(exchange="AMS", full_exchange_name="Euronext Amsterdam",
                      long_name="Magnum Ice Cream Company N.V."),
    "UNA.AS": _info(exchange="AMS", full_exchange_name="Euronext Amsterdam",
                     long_name="Unilever PLC"),
}


# ---------------------------------------------------------------------------
# Fake enrichment sink (mimics the legacy `cosmos` wrapper's
# update_symbol_enrichment / record_enrichment_snapshot / get_symbol
# methods, per §4). `get_symbol` is optional in the real contract (the
# script falls back to the just-persisted dict via `hasattr` if absent) —
# provided here for a genuine post-write re-read.
# ---------------------------------------------------------------------------

class _FakeEnrichmentCosmos:
    def __init__(self):
        self.enrichment_calls: List[tuple] = []   # (symbol, enrichment_dict)
        self.snapshot_calls: List[tuple] = []      # (symbol, technicals_score, momentum)
        self._enrichment_by_symbol: Dict[str, dict] = {}

    def update_symbol_enrichment(self, symbol: str, enrichment: dict) -> None:
        self.enrichment_calls.append((symbol, dict(enrichment)))
        self._enrichment_by_symbol[symbol] = dict(enrichment)

    def record_enrichment_snapshot(self, symbol: str, technicals_score, momentum) -> None:
        self.snapshot_calls.append((symbol, technicals_score, momentum))

    def get_symbol(self, symbol: str) -> Optional[dict]:
        if symbol not in self._enrichment_by_symbol:
            return None
        return {"symbol": symbol, "enrichment": self._enrichment_by_symbol[symbol]}


def _fake_analyze_single_symbol_factory(*, fail_for: frozenset = frozenset()):
    """Builds a monkeypatch replacement for src.dgi_screener.analyze_single_symbol
    (imported by name into src.portfolio_enrichment) — no network, records the
    (symbol, yf_symbol) it was called with, and returns a realistic non-empty
    result unless `symbol` is in `fail_for`."""
    calls: List[tuple] = []

    def _fake(symbol: str, filters: dict = None, yf_symbol: str = None) -> dict:
        calls.append((symbol, yf_symbol))
        if symbol in fail_for:
            return {"error": "simulated provider failure", "symbol": symbol}
        return {
            "symbol": symbol,
            "quality_score": 7.5,
            "quality_detail": {"payout_ratio": 0.4},
            "category": "balanced",
            "entry_tag": "buy_zone",
            "momentum": "improving",
            "metrics": {"pe": 15.0},
            "technicals": {"score": 62.0, "trend": "up"},
            "has_dividends": True,
            "filter_detail": None,
        }

    _fake.calls = calls  # type: ignore[attr-defined]
    return _fake


# ---------------------------------------------------------------------------
# Standard fixture builders
# ---------------------------------------------------------------------------

_TARGETS = {
    "ENAG": {
        "security_id": "XMAD:ENAG",
        "sec_id": "sec_XMAD_ENAG",
        "config_id": "config_ENAG",
        "exchange_mic": "XMAD",
        "company_name": "Enagas SA",
        "proposed_yfinance": "ENG.MC",
        "proposed_tradingview": "BME-ENG",
        "num_ledger_refs": 28,
    },
    "MICCT": {
        "security_id": "XAMS:MICCT",
        "sec_id": "sec_XAMS_MICCT",
        "config_id": "config_MICCT",
        "exchange_mic": "XAMS",
        "company_name": "Magnum Ice Cream Company N.V.",
        "proposed_yfinance": "MICC.AS",
        "proposed_tradingview": "EURONEXT-MICC",
        "num_ledger_refs": 6,
    },
    "ULVR": {
        "security_id": "XAMS:ULVR",
        "sec_id": "sec_XAMS_ULVR",
        "config_id": "config_ULVR",
        "exchange_mic": "XAMS",
        "company_name": "Unilever PLC",
        "proposed_yfinance": "UNA.AS",
        "proposed_tradingview": "EURONEXT-UNA",
        "num_ledger_refs": 38,
    },
}


def _make_security_doc(ticker: str, *, provider_symbols: Optional[dict] = None) -> dict:
    spec = _TARGETS[ticker]
    return {
        "id": spec["sec_id"],
        "doc_type": "security_master",
        "security_id": spec["security_id"],
        "exchange_mic": spec["exchange_mic"],
        "ticker": ticker,
        "symbol": ticker,
        "company_name": spec["company_name"],
        "isin": f"ISIN-{ticker}",
        "cusip": f"CUSIP{ticker}"[:9],
        "sedol": f"SEDOL{ticker}"[:7],
        "listing_currency": "EUR",
        "asset_class": "equity",
        "aliases": [ticker.lower()],
        "broker_ids": {"ibkr": f"{ticker}-IBKR"},
        "created_at": "2022-06-01T00:00:00Z",
        "provider_symbols": dict(provider_symbols) if provider_symbols else {},
    }


def _make_config_doc(ticker: str) -> dict:
    spec = _TARGETS[ticker]
    return {
        "id": spec["config_id"],
        "doc_type": "symbol_config",
        "symbol": ticker,
        "exchange": spec["exchange_mic"],
        "security_id": spec["security_id"],
        "watchlist": {"covered_call": False, "cash_secured_put": False, "buy_tracker": False},
        "total_shares": 100,
        "enrichment": {},
    }


def _standard_symbols_container(tickers=("ENAG", "MICCT", "ULVR"), provider_symbols_by_ticker=None):
    c = FakeSymbolsContainer()
    for t in tickers:
        override = (provider_symbols_by_ticker or {}).get(t)
        c.seed(_make_security_doc(t, provider_symbols=override))
        c.seed(_make_config_doc(t))
    return c


def _standard_portfolio_container(tickers=("ENAG", "MICCT", "ULVR")):
    port = FakePortfolioContainer()
    for t in tickers:
        spec = _TARGETS[t]
        for i in range(spec["num_ledger_refs"]):
            port.seed_movement(f"mvt_{t}_{i}", f"acct_{t}", spec["security_id"])
    return port


def _sec_doc(syms: FakeSymbolsContainer, ticker: str) -> dict:
    spec = _TARGETS[ticker]
    try:
        return next(c["body"] for c in syms.replace_calls if c["id"] == spec["sec_id"])
    except StopIteration:
        return syms.read_item(spec["sec_id"], ticker)


def _result_for(report: "RepairReport", ticker: str) -> "SecurityRepairResult":
    return next(r for r in report.results if r.ticker == ticker)


# ---------------------------------------------------------------------------
# PSC-0: ungated — the existing, already-shipped resolution/validation
# mechanism this repair depends on works exactly as the contract requires.
# ---------------------------------------------------------------------------

class TestExistingResolutionMechanismSupportsOverrides:
    @pytest.mark.parametrize("ticker,mic,yfinance,tradingview", [
        ("ENAG", "XMAD", "ENG.MC", "BME-ENG"),
        ("MICCT", "XAMS", "MICC.AS", "EURONEXT-MICC"),
        ("ULVR", "XAMS", "UNA.AS", "EURONEXT-UNA"),
    ])
    def test_override_precedence_yields_exact_corrected_symbols(
        self, ticker, mic, yfinance, tradingview
    ):
        doc = {"provider_symbols": {"yfinance": yfinance, "tradingview": tradingview}}
        assert resolve_yfinance_symbol(ticker, mic, doc) == yfinance
        assert resolve_tradingview_symbol(ticker, mic, doc) == tradingview

    @pytest.mark.parametrize("ticker,mic,wrong_yfinance,wrong_tradingview", [
        ("ENAG", "XMAD", "ENAG.MC", "BME-ENAG"),
        ("MICCT", "XAMS", "MICCT.AS", "EURONEXT-MICCT"),
        ("ULVR", "XAMS", "ULVR.AS", "EURONEXT-ULVR"),
    ])
    def test_no_override_produces_the_wrong_provider_symbol(
        self, ticker, mic, wrong_yfinance, wrong_tradingview
    ):
        """Documents exactly why the override is required: absent it, the
        non-override MIC-suffix path produces the known-wrong symbols the
        contract cites (ENAG.MC/404, MICCT.AS/404, ULVR.AS/404)."""
        assert resolve_yfinance_symbol(ticker, mic, None) == wrong_yfinance
        assert resolve_tradingview_symbol(ticker, mic, None) == wrong_tradingview

    def test_validate_provider_symbols_accepts_the_three_corrections(self):
        cleaned = validate_provider_symbols({"yfinance": "ENG.MC", "tradingview": "BME-ENG"})
        assert cleaned == {"yfinance": "ENG.MC", "tradingview": "BME-ENG"}

    def test_validate_provider_symbols_rejects_oversized_or_malformed_value(self):
        """PSC-0: proves the reused validation mechanism would reject a
        malformed/oversized proposed correction rather than writing it raw
        — the new script must call this (or an equivalent) before any write,
        per the contract's explicit test requirement."""
        with pytest.raises(ValueError):
            validate_provider_symbols({"yfinance": "x" * 31})  # exceeds 30 chars
        with pytest.raises(ValueError):
            validate_provider_symbols({"yfinance": "bad symbol with spaces!"})

    def test_validate_provider_symbols_preserves_unrelated_keys_on_merge(self):
        existing = {"bloomberg": "ENG-SM"}
        merged = {**existing, "yfinance": "ENG.MC", "tradingview": "BME-ENG"}
        cleaned = validate_provider_symbols(merged)
        assert cleaned["bloomberg"] == "ENG-SM"
        assert cleaned["yfinance"] == "ENG.MC"
        assert cleaned["tradingview"] == "BME-ENG"


# ---------------------------------------------------------------------------
# PSC-1: --audit / dry-run produces zero writes
# ---------------------------------------------------------------------------

@_skip
class TestAuditZeroWrites:
    def test_audit_zero_writes_symbols_and_portfolio(self):
        syms = _standard_symbols_container()
        port = _standard_portfolio_container()
        provider = _FakeProvider(_GOOD_RESPONSES)

        report = audit_repair(syms, port, yf_fetcher=provider)

        assert syms.write_count == 0, "audit must never write to symbols_container"
        assert port.write_count == 0, "audit must never write to portfolio_container"
        assert report.exit_code == 0

    def test_apply_repair_dry_run_true_is_also_zero_writes(self):
        syms = _standard_symbols_container()
        port = _standard_portfolio_container()
        provider = _FakeProvider(_GOOD_RESPONSES)

        report = apply_repair(syms, port, dry_run=True, yf_fetcher=provider)

        assert syms.write_count == 0
        assert port.write_count == 0
        assert report.mode == "audit"

    def test_audit_reports_ledger_ref_counts(self):
        syms = _standard_symbols_container()
        port = _standard_portfolio_container()
        provider = _FakeProvider(_GOOD_RESPONSES)

        report = audit_repair(syms, port, yf_fetcher=provider)

        assert _result_for(report, "ENAG").ledger_ref_count == 28
        assert _result_for(report, "MICCT").ledger_ref_count == 6
        assert _result_for(report, "ULVR").ledger_ref_count == 38


# ---------------------------------------------------------------------------
# PSC-2 / PSC-5: per-security independence + all-fail exit code
# ---------------------------------------------------------------------------

@_skip
class TestPerSecurityIndependentVerification:
    def test_one_security_mismatch_does_not_block_the_other_two(self):
        syms = _standard_symbols_container()
        port = _standard_portfolio_container()
        responses = dict(_GOOD_RESPONSES)
        responses["ENG.MC"] = _info(currency="USD")  # ENAG: currency mismatch
        provider = _FakeProvider(responses)

        report = apply_repair(syms, port, dry_run=False, yf_fetcher=provider)

        assert _sec_doc(syms, "MICCT")["provider_symbols"]["yfinance"] == "MICC.AS"
        assert _sec_doc(syms, "ULVR")["provider_symbols"]["yfinance"] == "UNA.AS"
        assert _result_for(report, "ENAG").provider_symbol_corrected is False
        assert _result_for(report, "MICCT").provider_symbol_corrected is True
        assert _result_for(report, "ULVR").provider_symbol_corrected is True
        assert report.exit_code != 2, (
            "partial success (2 of 3 corrected) must not report the "
            "all-failed exit code"
        )

    def test_all_three_securities_failing_verification_is_exit_code_2(self):
        syms = _standard_symbols_container()
        port = _standard_portfolio_container()
        provider = _FakeProvider({})  # every candidate 404s / unreachable

        report = apply_repair(syms, port, dry_run=False, yf_fetcher=provider)

        assert syms.write_count == 0, "zero writes when all three fail verification"
        assert port.write_count == 0
        assert report.exit_code == 2


# ---------------------------------------------------------------------------
# PSC-3 / PSC-4: currency / MIC-exchange / company-name corroboration and
# provider-unavailable handling
# ---------------------------------------------------------------------------

@_skip
class TestProviderVerificationChecks:
    def _apply_one(self, ticker: str, response: Any):
        syms = _standard_symbols_container()
        port = _standard_portfolio_container()
        responses = dict(_GOOD_RESPONSES)
        yf_candidate = _TARGETS[ticker]["proposed_yfinance"]
        responses[yf_candidate] = response
        provider = _FakeProvider(responses)
        report = apply_repair(syms, port, dry_run=False, yf_fetcher=provider)
        return syms, report

    def test_currency_mismatch_aborts_that_security_only(self):
        syms, report = self._apply_one("MICCT", _info(currency="USD"))
        assert _result_for(report, "MICCT").provider_symbol_corrected is False
        assert "mismatch" in _result_for(report, "MICCT").provider_verdict

    def test_exchange_not_corroborating_canonical_mic_aborts(self):
        """Corrected ticker resolves to a live/valid quote, but on the wrong
        exchange (e.g. London instead of the security's own canonical
        XAMS) — must abort, not merely require "some" live ticker."""
        syms, report = self._apply_one(
            "ULVR", _info(exchange="LSE", full_exchange_name="London Stock Exchange")
        )
        assert _result_for(report, "ULVR").provider_symbol_corrected is False
        assert "mismatch" in _result_for(report, "ULVR").provider_verdict

    def test_enag_company_name_mismatch_aborts_only_enag(self):
        """ENG.MC is the one candidate explicitly flagged 'must verify' in
        the contract — a company-name corroboration failure must abort only
        ENAG, leaving MICCT/ULVR (which don't require this extra check)
        unaffected."""
        syms, report = self._apply_one(
            "ENAG", _info(exchange="MCE", full_exchange_name="Madrid Stock Exchange",
                          long_name="Totally Unrelated Company PLC")
        )
        assert _result_for(report, "ENAG").provider_symbol_corrected is False
        assert "mismatch:company" in _result_for(report, "ENAG").provider_verdict
        assert _result_for(report, "MICCT").provider_symbol_corrected is True
        assert _result_for(report, "ULVR").provider_symbol_corrected is True

    def test_enag_company_name_match_succeeds(self):
        syms, report = self._apply_one(
            "ENAG", _info(exchange="MCE", full_exchange_name="Madrid Stock Exchange",
                          long_name="Enagas Sociedad Anonima")
        )
        assert _result_for(report, "ENAG").provider_symbol_corrected is True
        assert _sec_doc(syms, "ENAG")["provider_symbols"]["yfinance"] == "ENG.MC"

    def test_provider_returns_none_aborts_fail_closed(self):
        syms, report = self._apply_one("MICCT", None)
        assert _result_for(report, "MICCT").provider_symbol_corrected is False
        assert _result_for(report, "MICCT").provider_verdict == "unreachable"

    def test_provider_raises_aborts_fail_closed(self):
        syms, report = self._apply_one("ULVR", RuntimeError("network down"))
        assert _result_for(report, "ULVR").provider_symbol_corrected is False
        assert _result_for(report, "ULVR").provider_verdict == "unreachable"

    def test_provider_missing_info_fields_aborts(self):
        syms, report = self._apply_one("ENAG", {"info": {}})
        assert _result_for(report, "ENAG").provider_symbol_corrected is False


# ---------------------------------------------------------------------------
# PSC-6: complete, checksum-bearing backup exists before the first mutation
# ---------------------------------------------------------------------------

@_skip
class TestBackupBeforeMutation:
    def test_backup_file_exists_and_is_checksum_valid_before_first_write(self, tmp_path):
        syms = _standard_symbols_container()
        port = _standard_portfolio_container()
        provider = _FakeProvider(_GOOD_RESPONSES)

        observed_backup_state = {}

        def _check_backup_before_write():
            if observed_backup_state:
                return  # only need to check once, at the first mutation
            observed_backup_state["files_at_first_write"] = list(tmp_path.glob("*.json"))

        syms.before_write_hook = _check_backup_before_write

        apply_repair(syms, port, dry_run=False, yf_fetcher=provider, backup_dir=tmp_path)

        assert observed_backup_state, "no mutation occurred — cannot verify backup ordering"
        files = observed_backup_state["files_at_first_write"]
        assert files, "a backup file must already exist before the first security_master write"

        backup = json.loads(files[0].read_text())
        assert isinstance(backup.get("documents"), list) and backup["documents"], (
            "backup must contain the discovered documents"
        )
        assert len(backup["documents"]) == 3, "backup must cover all 3 security_master documents"
        checksum = backup.get("sha256", "")
        assert re.fullmatch(r"[0-9a-f]{64}", checksum or ""), (
            f"backup must carry a valid sha256 checksum; got {checksum!r}"
        )
        # Full re-verification using the exact same recipe the script's own
        # read_backup() uses (sha256 over documents sorted by id).
        import hashlib
        recomputed = hashlib.sha256(
            json.dumps(sorted(backup["documents"], key=lambda e: e["id"]), sort_keys=True).encode()
        ).hexdigest()
        assert checksum == recomputed, "backup sha256 must be a genuinely valid checksum"


# ---------------------------------------------------------------------------
# PSC-7 / PSC-8: merge preserves unrelated keys; every other field untouched
# ---------------------------------------------------------------------------

@_skip
class TestScopeOfMutationIsProviderSymbolsOnly:
    def test_merge_preserves_pre_existing_unrelated_provider_symbols_key(self):
        syms = _standard_symbols_container(
            provider_symbols_by_ticker={"ENAG": {"bloomberg": "ENG-SM"}}
        )
        port = _standard_portfolio_container()
        provider = _FakeProvider(_GOOD_RESPONSES)

        apply_repair(syms, port, dry_run=False, yf_fetcher=provider)

        ps = _sec_doc(syms, "ENAG")["provider_symbols"]
        assert ps.get("bloomberg") == "ENG-SM", "unrelated provider key must survive the merge"
        assert ps.get("yfinance") == "ENG.MC"
        assert ps.get("tradingview") == "BME-ENG"

    def test_all_other_security_master_fields_are_byte_identical(self):
        syms = _standard_symbols_container()
        port = _standard_portfolio_container()
        provider = _FakeProvider(_GOOD_RESPONSES)
        before = {t: dict(syms.read_item(_TARGETS[t]["sec_id"], t)) for t in _TARGETS}

        apply_repair(syms, port, dry_run=False, yf_fetcher=provider)

        immutable_fields = (
            "security_id", "exchange_mic", "listing_currency", "isin", "cusip",
            "sedol", "aliases", "asset_class", "broker_ids", "created_at",
            "company_name",
        )
        for ticker in _TARGETS:
            after = _sec_doc(syms, ticker)
            for field in immutable_fields:
                assert after.get(field) == before[ticker].get(field), (
                    f"{ticker}.{field} must be byte-identical; "
                    f"was {before[ticker].get(field)!r}, now {after.get(field)!r}"
                )


# ---------------------------------------------------------------------------
# PSC-9: config_* and ledger_txn docs are never written
# ---------------------------------------------------------------------------

@_skip
class TestZeroConfigAndLedgerMutation:
    def test_config_docs_never_written_on_success(self):
        syms = _standard_symbols_container()
        port = _standard_portfolio_container()
        provider = _FakeProvider(_GOOD_RESPONSES)

        apply_repair(syms, port, dry_run=False, yf_fetcher=provider)

        config_ids = {spec["config_id"] for spec in _TARGETS.values()}
        touched = {c["id"] for c in syms.replace_calls + syms.create_calls if c["id"] in config_ids}
        assert not touched, f"config docs must never be written; touched={touched}"

    def test_ledger_txn_never_written_regardless_of_outcome(self):
        syms = _standard_symbols_container()
        port = _standard_portfolio_container()
        provider = _FakeProvider({})  # everything fails

        apply_repair(syms, port, dry_run=False, yf_fetcher=provider)

        assert port.write_count == 0, "portfolio container must never be written by this repair"


# ---------------------------------------------------------------------------
# PSC-10 / PSC-11 / PSC-12: synchronous enrichment rerun + persistence
# ---------------------------------------------------------------------------

@_skip
class TestEnrichmentRerunAndPersistence:
    def test_enrichment_invoked_with_canonical_ticker_and_corrected_yf_symbol(self, monkeypatch):
        import src.portfolio_enrichment as pe
        fake_analyze = _fake_analyze_single_symbol_factory()
        monkeypatch.setattr(pe, "analyze_single_symbol", fake_analyze)

        syms = _standard_symbols_container()
        port = _standard_portfolio_container()
        provider = _FakeProvider(_GOOD_RESPONSES)
        cosmos = _FakeEnrichmentCosmos()

        apply_repair(syms, port, dry_run=False, yf_fetcher=provider, cosmos=cosmos)

        expected = {("ENAG", "ENG.MC"), ("MICCT", "MICC.AS"), ("ULVR", "UNA.AS")}
        assert expected.issubset(set(fake_analyze.calls)), (
            f"expected enrich_symbol calls {expected} within {fake_analyze.calls}"
        )

    def test_enrichment_not_invoked_for_a_security_whose_write_failed(self, monkeypatch):
        import src.portfolio_enrichment as pe
        fake_analyze = _fake_analyze_single_symbol_factory()
        monkeypatch.setattr(pe, "analyze_single_symbol", fake_analyze)

        syms = _standard_symbols_container()
        port = _standard_portfolio_container()
        responses = dict(_GOOD_RESPONSES)
        responses["ENG.MC"] = None  # ENAG verification fails
        provider = _FakeProvider(responses)
        cosmos = _FakeEnrichmentCosmos()

        apply_repair(syms, port, dry_run=False, yf_fetcher=provider, cosmos=cosmos)

        called_symbols = {sym for sym, _yf in fake_analyze.calls}
        assert "ENAG" not in called_symbols, (
            "enrichment must only be triggered after a successful "
            "provider_symbols write for that security"
        )
        assert "MICCT" in called_symbols and "ULVR" in called_symbols

    def test_enrichment_persistence_has_non_empty_technicals_quality_entry_momentum(self, monkeypatch):
        import src.portfolio_enrichment as pe
        fake_analyze = _fake_analyze_single_symbol_factory()
        monkeypatch.setattr(pe, "analyze_single_symbol", fake_analyze)

        syms = _standard_symbols_container()
        port = _standard_portfolio_container()
        provider = _FakeProvider(_GOOD_RESPONSES)
        cosmos = _FakeEnrichmentCosmos()

        apply_repair(syms, port, dry_run=False, yf_fetcher=provider, cosmos=cosmos)

        assert cosmos.enrichment_calls, "update_symbol_enrichment must have been called"
        for symbol, enrichment in cosmos.enrichment_calls:
            assert enrichment.get("technicals"), f"{symbol}: technicals must be non-empty"
            assert enrichment.get("quality_score") not in (None, ""), (
                f"{symbol}: quality_score must be populated"
            )
            assert enrichment.get("entry_tag"), f"{symbol}: entry_tag must be non-empty"
            assert enrichment.get("momentum"), f"{symbol}: momentum must be non-empty"
        assert cosmos.snapshot_calls, "record_enrichment_snapshot must have been called"

    def test_enrichment_failure_reported_non_fatally_without_rollback(self, monkeypatch):
        import src.portfolio_enrichment as pe
        fake_analyze = _fake_analyze_single_symbol_factory(fail_for=frozenset({"ENAG"}))
        monkeypatch.setattr(pe, "analyze_single_symbol", fake_analyze)

        syms = _standard_symbols_container()
        port = _standard_portfolio_container()
        provider = _FakeProvider(_GOOD_RESPONSES)
        cosmos = _FakeEnrichmentCosmos()

        report = apply_repair(syms, port, dry_run=False, yf_fetcher=provider, cosmos=cosmos)

        # provider_symbols correction must NOT be rolled back for ENAG
        assert _sec_doc(syms, "ENAG")["provider_symbols"]["yfinance"] == "ENG.MC", (
            "a downstream enrichment failure must never roll back the "
            "already-durable provider_symbols correction"
        )
        enag_result = _result_for(report, "ENAG")
        assert enag_result.provider_symbol_corrected is True
        assert enag_result.enrichment_verified is False
        enriched_symbols = {sym for sym, _e in cosmos.enrichment_calls}
        assert "ENAG" not in enriched_symbols
        assert report.exit_code == 3, (
            "enrichment-unverified must surface as exit_code=3 per §5, "
            "without being a fatal repair failure"
        )


# ---------------------------------------------------------------------------
# PSC-13: idempotent re-run
# ---------------------------------------------------------------------------

@_skip
class TestIdempotentRerun:
    def test_second_apply_after_full_success_makes_zero_additional_security_master_writes(self):
        syms = _standard_symbols_container()
        port = _standard_portfolio_container()
        provider = _FakeProvider(_GOOD_RESPONSES)

        apply_repair(syms, port, dry_run=False, yf_fetcher=provider)
        writes_after_first = syms.write_count

        second_report = apply_repair(
            syms, port, dry_run=False, yf_fetcher=_FakeProvider(_GOOD_RESPONSES)
        )

        assert syms.write_count == writes_after_first, (
            "re-running --apply once already corrected must not write "
            "provider_symbols again"
        )
        for ticker in _TARGETS:
            result = _result_for(second_report, ticker)
            assert result.already_correct is True
            ps = _sec_doc(syms, ticker)["provider_symbols"]
            assert ps["yfinance"] == _TARGETS[ticker]["proposed_yfinance"]
            assert ps["tradingview"] == _TARGETS[ticker]["proposed_tradingview"]


# ---------------------------------------------------------------------------
# PSC-14: restore reverts exactly the 3 security_master docs
# ---------------------------------------------------------------------------

@_skip
class TestRestoreRevertsSecurityMasterOnly:
    def test_restore_reverts_the_three_security_master_docs_and_nothing_else(self, tmp_path):
        syms = _standard_symbols_container()
        port = _standard_portfolio_container()
        provider = _FakeProvider(_GOOD_RESPONSES)
        before = {t: dict(syms.read_item(_TARGETS[t]["sec_id"], t)) for t in _TARGETS}

        report = apply_repair(syms, port, dry_run=False, yf_fetcher=provider, backup_dir=tmp_path)
        for ticker in _TARGETS:
            assert _sec_doc(syms, ticker)["provider_symbols"]["yfinance"] == (
                _TARGETS[ticker]["proposed_yfinance"]
            )

        backup_path = report.backup_path
        assert backup_path, "expected a backup_path recorded on the apply report"

        restore_repair(syms, backup_path)

        for ticker in _TARGETS:
            reverted = syms.read_item(_TARGETS[ticker]["sec_id"], ticker)
            assert reverted.get("provider_symbols", {}) == before[ticker].get("provider_symbols", {}), (
                f"{ticker}: provider_symbols must be reverted to its pre-repair value"
            )
        assert port.write_count == 0, "restore must never touch the portfolio container"
        config_ids = {spec["config_id"] for spec in _TARGETS.values()}
        touched_config = {c["id"] for c in syms.replace_calls if c["id"] in config_ids}
        assert not touched_config, "restore must never touch config_* docs"


# ---------------------------------------------------------------------------
# PSC-15: TradingView overrides are exact
# ---------------------------------------------------------------------------

@_skip
class TestTradingViewOverridesExact:
    def test_tradingview_overrides_match_exactly(self):
        syms = _standard_symbols_container()
        port = _standard_portfolio_container()
        provider = _FakeProvider(_GOOD_RESPONSES)

        apply_repair(syms, port, dry_run=False, yf_fetcher=provider)

        assert _sec_doc(syms, "ENAG")["provider_symbols"]["tradingview"] == "BME-ENG"
        assert _sec_doc(syms, "MICCT")["provider_symbols"]["tradingview"] == "EURONEXT-MICC"
        assert _sec_doc(syms, "ULVR")["provider_symbols"]["tradingview"] == "EURONEXT-UNA"
        # Sanity: none of these equal the wrong, non-override MIC-suffix path.
        assert _sec_doc(syms, "ENAG")["provider_symbols"]["tradingview"] != "BME-ENAG"
        assert _sec_doc(syms, "MICCT")["provider_symbols"]["tradingview"] != "EURONEXT-MICCT"
        assert _sec_doc(syms, "ULVR")["provider_symbols"]["tradingview"] != "EURONEXT-ULVR"


# ---------------------------------------------------------------------------
# PSC-DEFECT: regression — verify_provider_symbol() must construct
# YFinanceFetcher() with NO arguments.
#
# Defect: the script does `YFinanceFetcher(spec["expected_yfinance"])`,
# passing the ticker string (e.g. "MICC.AS") as `requests_per_minute`.
# YFinanceFetcher.__init__ immediately computes `60.0 / requests_per_minute`,
# raising TypeError on any string.  The construction is OUTSIDE the
# try/except that wraps fetcher.get_ticker_data(), so the TypeError
# propagates out of verify_provider_symbol() — which is documented "never
# raises" — and crashes every real (non-injected) invocation.
#
# This test FAILS on the buggy code (TypeError escapes) and PASSES after
# Livingston's fix (`YFinanceFetcher()` — no arguments).
# ---------------------------------------------------------------------------

@_skip
class TestVerifyProviderSymbolNoArgConstructor:
    """PSC-DEFECT regression: YFinanceFetcher must be constructed with no
    positional or keyword arguments by verify_provider_symbol()."""

    def test_fetcher_constructed_with_no_args_and_symbol_forwarded_to_get_ticker_data(
        self, monkeypatch
    ):
        import scripts.repair_provider_symbols_enag_micct_ulvr as _script_mod

        construction_log: list = []
        call_log: list = []

        class _NoArgFetcher:
            """Strict fake: raises TypeError if constructed with any argument,
            mirroring the TypeError the real YFinanceFetcher raises when
            requests_per_minute receives a string (60.0 / "MICC.AS")."""

            def __init__(self, *args, **kwargs):
                if args or kwargs:
                    raise TypeError(
                        f"YFinanceFetcher must be constructed with no positional or "
                        f"keyword arguments; got args={args!r}, kwargs={kwargs!r}"
                    )
                construction_log.append({"args": args, "kwargs": kwargs})

            def get_ticker_data(self, symbol: str):
                call_log.append(symbol)
                return _GOOD_RESPONSES.get(symbol)

        monkeypatch.setattr(_script_mod, "YFinanceFetcher", _NoArgFetcher)

        # Use MICCT (verify_company=False) to keep the response simple.
        spec = next(s for s in TARGET_SPECS if s["ticker"] == "MICCT")
        result = verify_provider_symbol(spec, "Magnum Ice Cream Company N.V.")

        assert len(construction_log) == 1, (
            f"YFinanceFetcher must be constructed exactly once with no args; "
            f"got {len(construction_log)} constructions — "
            "the buggy call YFinanceFetcher(spec['expected_yfinance']) would raise TypeError"
        )
        assert call_log == [spec["expected_yfinance"]], (
            f"get_ticker_data must be called with the expected_yfinance symbol "
            f"{spec['expected_yfinance']!r}; got {call_log!r}"
        )
        assert result.provider_verdict == "verified", (
            f"expected provider_verdict='verified' but got {result.provider_verdict!r}"
        )
