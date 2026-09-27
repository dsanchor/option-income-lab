"""Acceptance tests for server-resolved Yahoo Open FMV on corporate-action save."""

from copy import deepcopy
from time import sleep

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from scripts import backfill_dividend_buy_share_fmv as backfill
from src.dividends_economics import build_dividends_economics_report
from src.portfolio.cosmos_portfolio import (
    CorporateActionTransactionError,
    CosmosPortfolioService,
    IdempotencyConflictError,
)
from src.portfolio.models import normalize_share_fmv
from src.portfolio.share_fmv_service import (
    SCRIPT_VERSION,
    ShareFmvService,
    YahooFmvError,
    build_yahoo_share_fmv,
    validate_yahoo_request,
)
from tests.conftest_portfolio_p2 import (
    FakeImportSessionsContainer,
    FakePortfolioContainer,
)
from web import portfolio_routes


ACCOUNT = "acct-yahoo"
SECURITY = "XLON:ULVR"
DATE = "2026-09-25"
REQUEST_ID = "6e8ee4fb-8b78-40af-8854-c04e83c2885f"


def _canonical_yahoo_fmv(*, run_id=REQUEST_ID):
    return {
        "valuation_date": DATE,
        "amount": "37.037034",
        "currency": "GBP",
        "eur_amount": "43.202959",
        "price_per_share": "12.345678",
        "price_per_share_eur": "14.400986",
        "source": "YAHOO_OPEN",
        "confidence": "MARKET_ESTIMATE",
        "fx": {
            "rate": "1.166480000",
            "date": "2026-09-24",
            "source": "ECB",
        },
        "provenance": {
            "provider": "yfinance",
            "provider_symbol": "ULVR.L",
            "price_field": "OPEN",
            "requested_date": DATE,
            "market_session_date": "2026-09-28",
            "fetched_at": "2026-09-27T18:00:00+00:00",
            "script_version": SCRIPT_VERSION,
            "run_id": run_id,
        },
    }


def _request(*, request_id=REQUEST_ID, quantity="3", instruction=True):
    leg = {
        "leg_type": "SHARE_ACQUISITION",
        "trade_date": DATE,
        "quantity": quantity,
        "gross": {"amount": "0", "currency": "EUR", "eur_amount": "0"},
        "fees": {"total": "0", "currency": "EUR", "total_eur": "0"},
    }
    if instruction:
        leg["share_fmv_instruction"] = {"source": "YAHOO_OPEN"}
    return {
        "event_type": "SCRIP_DIVIDEND",
        "security_id": SECURITY,
        "account_id": ACCOUNT,
        "payment_date": DATE,
        "client_request_id": request_id,
        "legs": [leg],
    }


class AtomicContainer(FakePortfolioContainer):
    def __init__(self):
        super().__init__()
        self.batch_calls = []
        self.upsert_calls = []
        self.fail_batch = False

    def upsert_item(self, body):
        self.upsert_calls.append(deepcopy(body))
        return super().upsert_item(body)

    def execute_item_batch(self, *, batch_operations, partition_key):
        self.batch_calls.append(deepcopy(batch_operations))
        if self.fail_batch:
            raise RuntimeError("simulated transactional conflict")
        staged = deepcopy(self._store)
        for operation in batch_operations:
            verb, args, *rest = operation
            if verb == "create":
                doc = deepcopy(args[0])
                if doc["id"] in staged:
                    raise RuntimeError("duplicate id")
                assert doc["account_id"] == partition_key
                staged[doc["id"]] = doc
            elif verb == "replace":
                item, doc = args
                assert item in staged
                assert doc["account_id"] == partition_key
                staged[item] = deepcopy(doc)
            else:  # pragma: no cover - protects the fake from contract drift
                raise AssertionError(f"unexpected batch operation {verb}")
        self._store = staged
        return [{"statusCode": 201} for _ in batch_operations]


class CommitThenConflictContainer(AtomicContainer):
    def execute_item_batch(self, *, batch_operations, partition_key):
        result = super().execute_item_batch(
            batch_operations=batch_operations,
            partition_key=partition_key,
        )
        raise RuntimeError("simulated concurrent winner after commit")


class Resolver:
    def __init__(self, results=None):
        self.results = list(results or [_canonical_yahoo_fmv()])
        self.calls = []

    def resolve(self, **kwargs):
        self.calls.append(deepcopy(kwargs))
        result = self.results[min(len(self.calls) - 1, len(self.results) - 1)]
        if isinstance(result, Exception):
            raise result
        return deepcopy(result)


def _service(*, resolver=None, container=None):
    portfolio = container or AtomicContainer()
    return (
        CosmosPortfolioService(
            portfolio_container=portfolio,
            import_sessions_container=FakeImportSessionsContainer(),
            symbols_container=None,
            share_fmv_service=resolver or Resolver(),
        ),
        portfolio,
    )


def _original_group_doc(group="original-group", *, fmv=None):
    doc = {
        "id": "original-share",
        "_etag": "etag-original",
        "account_id": ACCOUNT,
        "doc_type": "ledger_txn",
        "txn_type": "BUY",
        "security_id": SECURITY,
        "ticker": "ULVR",
        "trade_date": DATE,
        "quantity": "3",
        "gross": {"amount": "0", "currency": "EUR", "eur_amount": "0"},
        "fees": {"total": "0", "currency": "EUR", "total_eur": "0"},
        "net": {"amount": "0", "currency": "EUR", "eur_amount": "0"},
        "cost_basis_status": "ZERO_COST",
        "correction_status": "ACTIVE",
        "ca_group_id": group,
        "ca_leg_type": "SHARE_ACQUISITION",
        "ca_event_type": "SCRIP_DIVIDEND",
        "ca_group_seq": 1,
    }
    if fmv is not None:
        doc["share_fmv"] = deepcopy(fmv)
    return doc


@pytest.mark.parametrize(
    "mutate",
    [
        lambda request: request["legs"][0].update(
            share_fmv={"source": "MANUAL"}, share_fmv_instruction={"source": "YAHOO_OPEN"}
        ),
        lambda request: request["legs"][0].update(
            share_fmv_instruction={"source": "YAHOO_OPEN", "price": "12"}
        ),
        lambda request: request["legs"][0].update(
            share_fmv_instruction={"source": "MANUAL"}
        ),
        lambda request: request["legs"][0].update(
            share_fmv={"source": "YAHOO_OPEN", "amount": "10"}
        ),
        lambda request: request.update(client_request_id=None),
        lambda request: request.update(client_request_id="not-a-uuid"),
    ],
)
def test_request_shape_reserves_yahoo_and_requires_stable_uuid(mutate):
    request = _request()
    mutate(request)
    with pytest.raises(ValueError):
        validate_yahoo_request(request)


def test_instruction_is_only_valid_on_eligible_positive_share_leg():
    request = _request()
    request["event_type"] = "CASH_DIVIDEND"
    request["legs"][0]["leg_type"] = "CASH_DIVIDEND"
    with pytest.raises(ValueError, match="only valid"):
        validate_yahoo_request(request)

    request = _request(quantity="0")
    service, container = _service()
    with pytest.raises(ValueError, match="greater than zero"):
        service.create_corporate_action(request)
    assert container._store == {}


def test_public_normalizer_rejects_reserved_source_but_internal_path_accepts_it():
    value = _canonical_yahoo_fmv()
    with pytest.raises(ValueError, match="reserved"):
        normalize_share_fmv(value, quantity="3", trade_date=DATE)
    normalized = normalize_share_fmv(
        value,
        quantity="3",
        trade_date=DATE,
        allow_yahoo=True,
    )
    assert normalized["source"] == "YAHOO_OPEN"


def test_script_and_endpoint_share_one_yahoo_ecb_policy_implementation():
    assert backfill.ShareFmvService is ShareFmvService
    assert backfill.build_yahoo_share_fmv is build_yahoo_share_fmv


def test_shared_policy_uses_resolved_symbol_open_seven_days_currency_ecb_and_provenance():
    class Symbols:
        def read_item(self, *, item, partition_key):
            assert item == "sec_XLON_ULVR"
            assert partition_key == "ULVR"
            return {
                "ticker": "ULVR",
                "exchange_mic": "XLON",
                "listing_currency": "GBP",
                "provider_symbols": {"yfinance": "ULVR.L"},
            }

    class Fetcher:
        def __init__(self):
            self.calls = []

        def get_daily_open(self, symbol, requested_date, max_calendar_days=7):
            self.calls.append((symbol, requested_date, max_calendar_days))
            return {
                "status": "ok",
                "open": "12.345678",
                "market_session_date": "2026-09-28",
                "currency": "GBP",
            }

    fetcher = Fetcher()
    fx_calls = []

    def fx_getter(currency, to_currency, rate_date):
        fx_calls.append((currency, to_currency, rate_date))
        return "1.16648", "2026-09-24"

    value = ShareFmvService(
        Symbols(),
        fetcher=fetcher,
        fx_getter=fx_getter,
    ).resolve(
        security_id=SECURITY,
        quantity="3",
        trade_date=DATE,
        run_id=REQUEST_ID,
        fetched_at="2026-09-27T18:00:00+00:00",
    )

    assert fetcher.calls == [("ULVR.L", DATE, 7)]
    assert fx_calls == [("GBP", "EUR", DATE)]
    assert value == _canonical_yahoo_fmv()


@pytest.mark.parametrize(
    ("observation", "security_currency", "error", "stage", "retryable"),
    [
        ({"status": "no_market_session"}, "GBP", "yahoo_fmv_unavailable", "yahoo", False),
        (
            {
                "status": "ok",
                "open": "NaN",
                "market_session_date": DATE,
                "currency": "GBP",
            },
            "GBP",
            "yahoo_fmv_unavailable",
            "yahoo",
            False,
        ),
        (
            {
                "status": "ok",
                "open": "12",
                "market_session_date": DATE,
                "currency": "USD",
            },
            "GBP",
            "yahoo_fmv_unavailable",
            "currency",
            False,
        ),
    ],
)
def test_shared_policy_fails_closed_for_session_open_and_currency(
    observation, security_currency, error, stage, retryable
):
    with pytest.raises(YahooFmvError) as caught:
        build_yahoo_share_fmv(
            quantity="3",
            trade_date=DATE,
            security={"listing_currency": security_currency},
            provider_symbol="ULVR.L",
            observation=observation,
        )
    assert caught.value.error == error
    assert caught.value.stage == stage
    assert caught.value.retryable is retryable


def test_shared_policy_enforces_five_day_ecb_window_and_identity_eur():
    observation = {
        "status": "ok",
        "open": "10",
        "market_session_date": DATE,
        "currency": "GBP",
    }
    with pytest.raises(YahooFmvError) as caught:
        build_yahoo_share_fmv(
            quantity="2",
            trade_date=DATE,
            security={"listing_currency": "GBP"},
            provider_symbol="ULVR.L",
            observation=observation,
            fx_getter=lambda *args, **kwargs: ("1.2", "2026-09-19"),
        )
    assert caught.value.error == "fx_unavailable"
    assert caught.value.retryable is True

    eur_value = build_yahoo_share_fmv(
        quantity="2",
        trade_date=DATE,
        security={"listing_currency": "EUR"},
        provider_symbol="ACME.DE",
        observation={**observation, "currency": "EUR"},
    )
    assert eur_value["fx"] == {
        "rate": "1.000000000",
        "date": DATE,
        "source": "IDENTITY",
    }


def test_configured_yahoo_and_fx_timeouts_map_to_retryable_504():
    class SlowFetcher:
        def get_daily_open(self, *args, **kwargs):
            sleep(0.03)
            return {}

    service = ShareFmvService(
        symbols_container=object(),
        fetcher=SlowFetcher(),
        yahoo_timeout_seconds=0.001,
        total_timeout_seconds=0.01,
    )
    service.symbols_container = type(
        "Symbols",
        (),
        {
            "read_item": lambda self, **kwargs: {
                "ticker": "ULVR",
                "exchange_mic": "XLON",
                "listing_currency": "GBP",
                "provider_symbols": {"yfinance": "ULVR.L"},
            }
        },
    )()
    with pytest.raises(YahooFmvError) as caught:
        service.resolve(security_id=SECURITY, quantity="3", trade_date=DATE)
    assert (caught.value.error, caught.value.status_code, caught.value.retryable) == (
        "yahoo_fmv_timeout",
        504,
        True,
    )

    with pytest.raises(YahooFmvError) as caught:
        build_yahoo_share_fmv(
            quantity="3",
            trade_date=DATE,
            security={"listing_currency": "GBP"},
            provider_symbol="ULVR.L",
            observation={
                "status": "ok",
                "open": "12",
                "market_session_date": DATE,
                "currency": "GBP",
            },
            fx_getter=lambda *args, **kwargs: (sleep(0.03), "bad"),
            fx_timeout_seconds=0.001,
        )
    assert (caught.value.error, caught.value.status_code, caught.value.retryable) == (
        "fx_timeout",
        504,
        True,
    )


def test_create_resolves_before_one_atomic_batch_and_returns_persisted_fmv():
    resolver = Resolver()
    service, container = _service(resolver=resolver)

    response = service.create_corporate_action(_request())

    assert len(resolver.calls) == 1
    assert resolver.calls[0] == {
        "security_id": SECURITY,
        "quantity": "3",
        "trade_date": DATE,
        "run_id": REQUEST_ID,
    }
    assert len(container.batch_calls) == 1
    assert container.upsert_calls == []
    assert len(container._store) == 2
    movement = response["movements"][0]
    assert movement["share_fmv"] == _canonical_yahoo_fmv()
    assert container._store[movement["id"]]["share_fmv"] == movement["share_fmv"]


@pytest.mark.parametrize(
    "failure",
    [
        YahooFmvError(
            "yahoo_fmv_unavailable", "no price", "yahoo", False, 422
        ),
        YahooFmvError(
            "yahoo_fmv_upstream_unavailable", "provider down", "yahoo", True, 503
        ),
        YahooFmvError("fx_unavailable", "ECB down", "fx", True, 503),
    ],
)
def test_provider_failures_write_nothing_and_leave_no_idempotency_mark(failure):
    service, container = _service(resolver=Resolver([failure]))
    with pytest.raises(YahooFmvError):
        service.create_corporate_action(_request())
    assert container._store == {}
    assert container.batch_calls == []
    assert container.upsert_calls == []


def test_transaction_failure_writes_nothing_and_leaves_no_idempotency_mark():
    container = AtomicContainer()
    container.fail_batch = True
    service, _ = _service(container=container)
    with pytest.raises(CorporateActionTransactionError):
        service.create_corporate_action(_request())
    assert container._store == {}
    assert container.upsert_calls == []


def test_successful_retry_is_idempotent_without_refetch_and_conflicting_reuse_is_409():
    resolver = Resolver()
    service, container = _service(resolver=resolver)
    first = service.create_corporate_action(_request())
    second = service.create_corporate_action(deepcopy(_request()))
    assert second == first
    assert len(resolver.calls) == 1
    assert len(container.batch_calls) == 1

    with pytest.raises(IdempotencyConflictError):
        service.create_corporate_action(_request(quantity="4"))
    assert len(resolver.calls) == 1
    assert len(container.batch_calls) == 1


def test_failed_attempt_can_retry_same_uuid_and_performs_a_fresh_valuation():
    resolver = Resolver(
        [
            YahooFmvError(
                "yahoo_fmv_upstream_unavailable",
                "temporary outage",
                "yahoo",
                True,
                503,
            ),
            _canonical_yahoo_fmv(),
        ]
    )
    service, container = _service(resolver=resolver)
    with pytest.raises(YahooFmvError):
        service.create_corporate_action(_request())
    response = service.create_corporate_action(_request())
    assert len(resolver.calls) == 2
    assert len(container.batch_calls) == 1
    assert response["movements"][0]["share_fmv"]["source"] == "YAHOO_OPEN"


def test_concurrent_commit_race_replays_the_committed_response():
    container = CommitThenConflictContainer()
    resolver = Resolver()
    service, _ = _service(resolver=resolver, container=container)

    response = service.create_corporate_action(_request())

    assert response["movements"][0]["share_fmv"] == _canonical_yahoo_fmv()
    assert len(resolver.calls) == 1
    assert len(container._store) == 2


def test_correction_refresh_is_atomic_and_failure_never_supersedes_original():
    original = _original_group_doc()
    container = AtomicContainer()
    container._store[original["id"]] = deepcopy(original)
    resolver = Resolver(
        [
            YahooFmvError("fx_unavailable", "ECB down", "fx", True, 503),
            _canonical_yahoo_fmv(),
        ]
    )
    service, _ = _service(resolver=resolver, container=container)
    request = {
        **_request(),
        "correction_note": "refresh Yahoo valuation",
    }

    with pytest.raises(YahooFmvError):
        service.correct_corporate_action_group("original-group", request)
    assert container._store == {original["id"]: original}

    response = service.correct_corporate_action_group("original-group", request)
    assert response["movements"][0]["share_fmv"] == _canonical_yahoo_fmv()
    assert container._store[original["id"]]["correction_status"] == "SUPERSEDED"
    assert len(container.batch_calls) == 1


def test_correction_omission_inherits_persisted_yahoo_without_refetch():
    persisted = _canonical_yahoo_fmv(
        run_id="fe1353d3-f68f-4ce2-b4c2-8719bf2f12aa"
    )
    original = _original_group_doc(fmv=persisted)
    container = AtomicContainer()
    container._store[original["id"]] = deepcopy(original)
    resolver = Resolver()
    service, _ = _service(resolver=resolver, container=container)
    request = _request(instruction=False)
    request["correction_note"] = "change notes only"
    request.pop("client_request_id")

    response = service.correct_corporate_action_group("original-group", request)

    assert resolver.calls == []
    assert response["movements"][0]["share_fmv"] == persisted


def test_economics_consumes_the_persisted_yahoo_value_without_refetch_or_special_formula():
    resolver = Resolver()
    service, _ = _service(resolver=resolver)
    movement = service.create_corporate_action(_request())["movements"][0]

    report = build_dividends_economics_report([movement])

    assert len(resolver.calls) == 1
    assert report["summary"]["scrip_fmv_eur"] == 43.2
    assert report["summary"]["scrip_dividends_eur"] == 43.2
    assert report["summary"]["total_dividends_eur"] == 43.2


@pytest.mark.parametrize(
    ("failure", "status", "error", "stage", "retryable"),
    [
        (
            YahooFmvError(
                "yahoo_fmv_unavailable", "No eligible Open", "yahoo", False, 422
            ),
            422,
            "yahoo_fmv_unavailable",
            "yahoo",
            False,
        ),
        (
            YahooFmvError(
                "yahoo_fmv_upstream_unavailable",
                "Yahoo unavailable",
                "yahoo",
                True,
                503,
            ),
            503,
            "yahoo_fmv_upstream_unavailable",
            "yahoo",
            True,
        ),
        (
            YahooFmvError("fx_timeout", "ECB timed out", "fx", True, 504),
            504,
            "fx_timeout",
            "fx",
            True,
        ),
    ],
)
def test_http_error_mapping_is_safe_and_includes_stage_and_retryable(
    monkeypatch, failure, status, error, stage, retryable
):
    class FailingService:
        def create_corporate_action(self, body):
            raise failure

    monkeypatch.setattr(
        portfolio_routes,
        "_get_portfolio_svc",
        lambda request: FailingService(),
    )
    app = FastAPI()
    app.include_router(portfolio_routes.router)

    response = TestClient(app).post(
        "/api/portfolio/corporate-actions",
        json=_request(),
    )

    assert response.status_code == status
    assert response.json() == {
        "error": error,
        "detail": failure.detail,
        "stage": stage,
        "retryable": retryable,
    }
