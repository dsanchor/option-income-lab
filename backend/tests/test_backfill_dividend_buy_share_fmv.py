import shutil
from copy import deepcopy
from pathlib import Path

import pytest

from scripts import backfill_dividend_buy_share_fmv as backfill

TARGET = backfill.TargetIdentity(
    "https://example.documents.azure.com", "db", "portfolio", "symbols"
)
ARTIFACTS = Path(__file__).resolve().parents[1] / ".test-artifacts" / "share-fmv"


class MemoryContainer:
    def __init__(self, docs=()):
        self.docs = {doc["id"]: deepcopy(doc) for doc in docs}
        self.replace_calls = []

    def query_items(self, **kwargs):
        return iter(deepcopy(list(self.docs.values())))

    def read_item(self, item, partition_key):
        doc = self.docs[item]
        assert doc.get("account_id", partition_key) == partition_key
        return deepcopy(doc)

    def replace_item(self, item, body, **kwargs):
        self.replace_calls.append((item, deepcopy(body), deepcopy(kwargs)))
        updated = deepcopy(body)
        updated["_etag"] = f"etag-{len(self.replace_calls) + 1}"
        self.docs[item] = updated
        return deepcopy(updated)


class SymbolContainer:
    def __init__(self, security):
        self.security = security

    def read_item(self, item, partition_key):
        return deepcopy(self.security)


class Fetcher:
    def __init__(self, response=None):
        self.response = response or {
            "status": "ok",
            "open": "12.345678",
            "market_session_date": "2026-09-28",
            "currency": "GBP",
        }
        self.calls = []

    def get_daily_open(self, symbol, requested_date, max_calendar_days=7):
        self.calls.append((symbol, requested_date, max_calendar_days))
        return deepcopy(self.response)


def _doc(identifier="buy", **overrides):
    result = {
        "id": identifier,
        "_etag": "etag-1",
        "account_id": "acct",
        "doc_type": "ledger_txn",
        "txn_type": "BUY",
        "security_id": "XLON:ULVR",
        "trade_date": "2026-09-26",
        "quantity": "3",
        "ca_leg_type": "SHARE_ACQUISITION",
        "ca_event_type": "SCRIP_DIVIDEND",
        "correction_status": "ACTIVE",
        "gross": {"amount": "999", "currency": "EUR", "eur_amount": "999"},
        "net": {"amount": "1000", "currency": "EUR", "eur_amount": "1000"},
    }
    result.update(overrides)
    return result


def _security(**overrides):
    result = {
        "id": "sec_XLON_ULVR",
        "ticker": "ULVR",
        "exchange_mic": "XLON",
        "listing_currency": "GBP",
        "provider_symbols": {"yfinance": "ULVR.L"},
    }
    result.update(overrides)
    return result


@pytest.fixture(autouse=True)
def clean_artifacts():
    shutil.rmtree(ARTIFACTS, ignore_errors=True)
    yield
    shutil.rmtree(ARTIFACTS, ignore_errors=True)


def _plan(portfolio, *, force=False, fetcher=None, fx=None):
    return backfill.build_plan(
        portfolio,
        SymbolContainer(_security()),
        target=TARGET,
        force=force,
        fetcher=fetcher or Fetcher(),
        fx_getter=fx or (
            lambda currency, to_currency, rate_date: ("1.166480000", "2026-09-25")
        ),
    )


def test_plan_is_deterministic_read_only_and_ignores_gross_as_valuation():
    portfolio = MemoryContainer([_doc()])
    fetcher = Fetcher()
    first = _plan(portfolio, fetcher=fetcher)
    second = _plan(portfolio, fetcher=fetcher)
    assert first == second
    assert len(first["sha256"]) == 64
    assert portfolio.replace_calls == []
    proposal = first["actions"][0]["share_fmv"]
    assert proposal["amount"] == "37.037034"
    assert proposal["amount"] != portfolio.docs["buy"]["gross"]["amount"]
    assert proposal["fx"]["date"] == "2026-09-25"
    assert proposal["provenance"]["requested_date"] == "2026-09-26"
    assert proposal["provenance"]["market_session_date"] == "2026-09-28"
    assert fetcher.calls[0] == ("ULVR.L", "2026-09-26", 7)


@pytest.mark.parametrize(
    "overrides",
    [
        {"correction_status": "SUPERSEDED"},
        {"correction_status": "VOIDED"},
        {"deleted_at": "2026-09-27T00:00:00Z"},
        {"txn_type": "SELL"},
        {"ca_leg_type": "CASH_TOP_UP"},
        {"ca_event_type": "SHARE_CONSOLIDATION"},
        {"quantity": "0"},
        {"trade_date": "bad"},
    ],
)
def test_selection_is_fail_closed_even_when_forced(overrides):
    plan = _plan(MemoryContainer([_doc(**overrides)]), force=True)
    assert plan["actions"] == []


@pytest.mark.parametrize(
    ("response", "security_patch", "reason"),
    [
        ({"status": "no_market_session"}, {}, "no_market_session"),
        (
            {
                "status": "invalid_open",
                "market_session_date": "2026-09-28",
                "currency": "GBP",
            },
            {},
            "invalid_open",
        ),
        (
            {
                "status": "ok",
                "open": "12",
                "market_session_date": "2026-09-28",
                "currency": "USD",
            },
            {},
            "currency_mismatch",
        ),
    ],
)
def test_plan_classifies_market_failures_without_partial_fmv(
    response, security_patch, reason
):
    plan = backfill.build_plan(
        MemoryContainer([_doc()]),
        SymbolContainer(_security(**security_patch)),
        target=TARGET,
        fetcher=Fetcher(response),
        fx_getter=lambda *args, **kwargs: ("1.2", "2026-09-25"),
    )
    assert plan["actions"] == []
    assert plan["counts"][reason] == 1


def test_fx_unavailable_skip_has_actionable_safe_context():
    from src.portfolio.fx_service import FxRateNotFoundError

    def unavailable(*args, **kwargs):
        raise FxRateNotFoundError("GBP", "2001-09-16")

    plan = _plan(
        MemoryContainer([_doc(trade_date="2001-09-16")]),
        force=True,
        fetcher=Fetcher(
            {
                "status": "ok",
                "open": "5.25",
                "market_session_date": "2001-09-17",
                "currency": "GBP",
            }
        ),
        fx=unavailable,
    )

    assert plan["skips"] == [
        {
            "id": "buy",
            "reason": "fx_unavailable",
            "movement_id": "buy",
            "security_id": "XLON:ULVR",
            "trade_date": "2001-09-16",
            "valuation_date": "2001-09-16",
            "native_currency": "GBP",
            "detail_code": "rate_not_found",
        }
    ]


def test_existing_fmv_is_idempotently_skipped_and_force_records_previous_value():
    previous = {"source": "MANUAL", "amount": "1.000000"}
    portfolio = MemoryContainer([_doc(share_fmv=previous)])
    skipped = _plan(portfolio)
    assert skipped["counts"]["already_set"] == 1
    assert skipped["actions"] == []
    forced = _plan(portfolio, force=True)
    assert forced["actions"][0]["previous_share_fmv"] == previous


@pytest.mark.parametrize("provider_currency", ["GBp", "GBX"])
def test_force_recomputes_erroneous_uk_pence_fmv_through_shared_service(
    provider_currency,
):
    erroneous = {
        "source": "YAHOO_OPEN",
        "price_per_share": "4500.000000",
        "currency": "GBP",
    }
    portfolio = MemoryContainer([_doc(share_fmv=erroneous)])
    forced = _plan(
        portfolio,
        force=True,
        fetcher=Fetcher(
            {
                "status": "ok",
                "open": "4500",
                "market_session_date": "2026-09-28",
                "currency": provider_currency,
            }
        ),
    )
    action = forced["actions"][0]
    assert action["previous_share_fmv"] == erroneous
    assert action["share_fmv"]["price_per_share"] == "45.000000"


def test_apply_backs_up_before_cas_write_and_restore_round_trips(monkeypatch):
    portfolio = MemoryContainer([_doc()])
    plan = _plan(portfolio)
    events = []
    original_write_backup = backfill.write_backup
    original_replace = backfill._etag_replace

    def tracked_backup(*args, **kwargs):
        events.append("backup")
        return original_write_backup(*args, **kwargs)

    def tracked_replace(*args, **kwargs):
        events.append("replace")
        return original_replace(*args, **kwargs)

    monkeypatch.setattr(backfill, "write_backup", tracked_backup)
    monkeypatch.setattr(backfill, "_etag_replace", tracked_replace)
    results, backup_path = backfill.apply_plan(
        portfolio, plan, backup_dir=ARTIFACTS
    )
    assert results["updated"] == 1
    assert events[:2] == ["backup", "replace"]
    assert backup_path and backup_path.exists()
    written = portfolio.docs["buy"]["share_fmv"]
    assert written["provenance"]["run_id"]
    assert written["provenance"]["fetched_at"].endswith("+00:00")

    restored = backfill.restore_backup(portfolio, backup_path, TARGET)
    assert restored == {
        "restored": 1,
        "already_restored": 0,
        "cas_conflict": 0,
        "failed": 0,
    }
    assert "share_fmv" not in portfolio.docs["buy"]


def test_cas_conflict_is_reported_without_retry(monkeypatch):
    portfolio = MemoryContainer([_doc()])
    plan = _plan(portfolio)
    calls = []

    def conflict(*args, **kwargs):
        calls.append(1)
        return False

    monkeypatch.setattr(backfill, "_etag_replace", conflict)
    results, backup_path = backfill.apply_plan(
        portfolio, plan, backup_dir=ARTIFACTS
    )
    assert backup_path and backup_path.exists()
    assert results["cas_conflict"] == 1
    assert results["updated"] == 0
    assert len(calls) == 1
    assert "share_fmv" not in portfolio.docs["buy"]


@pytest.mark.parametrize(
    "argv",
    [
        ["--apply"],
        [
            "--apply", "--plan-sha256", "a" * 64,
            "--confirm", backfill.APPLY_CONFIRMATION,
        ],
        [
            "--apply", "--all-active", "--force",
            "--plan-sha256", "a" * 64,
            "--confirm", backfill.APPLY_CONFIRMATION,
        ],
    ],
)
def test_apply_and_force_require_all_confirmation_gates(argv):
    args = backfill._parser().parse_args(argv)
    with pytest.raises(backfill.BackfillError):
        backfill._validate_args(args)


def test_bare_all_active_reaches_read_only_audit_planning(monkeypatch, capsys):
    portfolio = MemoryContainer()
    symbols = SymbolContainer(_security())
    calls = []
    plan = {
        "sha256": "a" * 64,
        "counts": {"failed": 0, "fx_unavailable": 0},
        "actions": [],
    }

    monkeypatch.setattr(
        backfill,
        "_build_containers",
        lambda *args: (portfolio, symbols, TARGET),
    )

    def tracked_build_plan(*args, **kwargs):
        calls.append((args, kwargs))
        return plan

    monkeypatch.setattr(backfill, "build_plan", tracked_build_plan)

    assert backfill.main(["--all-active"]) == 0
    assert len(calls) == 1
    assert calls[0][0] == (portfolio, symbols)
    assert calls[0][1]["force"] is False
    assert '"mode": "dry-run"' in capsys.readouterr().out
