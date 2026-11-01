"""Persistence, fresh-data job, wire-read consistency and UTC registry behavior."""

import asyncio
import copy
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest
import yaml
from azure.cosmos.exceptions import CosmosHttpResponseError, CosmosResourceNotFoundError
from fastapi.testclient import TestClient
from src.option_timing import DEFAULT_CRON, TimingInputError, calculate_sides, snapshot
from src.option_timing_job import process_symbol, run_option_timing
from src.option_timing_store import OptionTimingStore, load_current, read_current
from src.scheduler_registry import TaskRegistry

from tests.test_option_timing import market_history, utc


class MemoryContainer:
    def __init__(self):
        self.docs = {}
        self.sequence = 0
        self.conflict_hook = None
        self.queries = []

    def read_item(self, item, partition_key):
        if (partition_key, item) not in self.docs:
            raise CosmosResourceNotFoundError(message="not found")
        return copy.deepcopy(self.docs[(partition_key, item)])

    def _save(self, body):
        self.sequence += 1
        body = copy.deepcopy(body)
        body["_etag"] = str(self.sequence)
        self.docs[(body["symbol"], body["id"])] = body
        return copy.deepcopy(body)

    def create_item(self, body):
        if (body["symbol"], body["id"]) in self.docs:
            raise CosmosHttpResponseError(status_code=409, message="duplicate")
        return self._save(body)

    def replace_item(self, item, body, etag=None, match_condition=None):
        if self.conflict_hook:
            hook, self.conflict_hook = self.conflict_hook, None
            hook()
        if self.docs[(body["symbol"], item)]["_etag"] != etag:
            raise CosmosHttpResponseError(status_code=412, message="etag conflict")
        return self._save(body)

    def query_items(self, query, parameters=None, **kwargs):
        self.queries.append(query)
        params = {p["name"]: p["value"] for p in (parameters or [])}
        docs = list(self.docs.values())
        if "@type" in params:
            docs = [d for d in docs if d.get("doc_type") == params["@type"] and
                    d.get("model_version") == params["@version"]]
        elif "security_master" in query:
            docs = [d for d in docs if d.get("doc_type") == "security_master"]
        return copy.deepcopy(docs)


def make_point(hour=14, history_kind="observed", history=None):
    now = utc(f"2026-10-30T{hour:02d}:00:00")
    return snapshot(
        "MSFT", calculate_sides(history if history is not None else market_history()),
        now, now, "2026-10-30", hour < 20, history_kind,
    )


def test_three_same_day_points_immutable_dedup_and_original_explanations():
    store = OptionTimingStore(MemoryContainer())
    points = []
    for hour in (14, 17, 20):
        history = market_history()
        history.iloc[-1, history.columns.get_loc("High")] += hour / 20
        point = make_point(hour, history=history)
        points.append(point)
        store.publish("MSFT", "XNAS:MSFT", [point], utc("2026-10-30T22:00:00"), current=point)
    result = store.history("MSFT", "XNAS:MSFT", 90, utc("2026-10-30T22:00:00"))
    assert len(result["points"]) == 3
    assert len({p["snapshot_id"] for p in result["points"]}) == 3
    assert len({str(p["covered_call"]["factors"]) for p in result["points"]}) == 3
    altered = copy.deepcopy(points[0])
    altered["covered_call"]["score"] = 0
    store.publish("MSFT", "XNAS:MSFT", [altered], utc("2026-10-30T22:00:00"), current=altered)
    assert store.read("MSFT")["points"][0] == points[0]
    assert store.read("MSFT")["current"] == points[-1]
    assert result["points"][0]["covered_call"]["factors"] == points[0]["covered_call"]["factors"]

def test_configured_fourth_tick_and_manual_run_preserve_all_observed_points():
    store = OptionTimingStore(MemoryContainer())
    now = utc("2026-10-30T22:00:00")
    points = [make_point(hour) for hour in (14, 15, 17, 19, 20)]
    for point in points:
        store.publish("MSFT", "XNAS:MSFT", [point], now, current=point)
    assert store.history("MSFT", "XNAS:MSFT", 90, now)["points"] == points
    assert store.read("MSFT")["current"] == points[-1]


def test_payload_limit_fails_before_write_retains_last_valid_and_reserves_error_room(monkeypatch):
    from src import option_timing_store

    container = MemoryContainer()
    store = OptionTimingStore(container)
    now = utc("2026-10-30T22:00:00")
    initial = make_point(14)
    saved = store.publish("MSFT", "XNAS:MSFT", [initial], now, current=initial)
    body = {key: value for key, value in saved.items() if key != "_etag"}
    initial_bytes = len(json.dumps(body).encode("utf-8"))
    monkeypatch.setattr(option_timing_store, "MAX_DOCUMENT_BYTES",
                        initial_bytes + option_timing_store.ERROR_RESERVE_BYTES + 10)
    later = make_point(17)
    later["covered_call"]["factors"][0]["evidence"].append("日本語" * 2000)
    create = Mock(wraps=container.create_item)
    replace = Mock(wraps=container.replace_item)
    monkeypatch.setattr(container, "create_item", create)
    monkeypatch.setattr(container, "replace_item", replace)
    with pytest.raises(TimingInputError, match="option_timing_payload_too_large"):
        store.publish("MSFT", "XNAS:MSFT", [later], now, current=later)
    with pytest.raises(TimingInputError, match="option_timing_payload_too_large"):
        store.publish("NEW", "XNAS:NEW", [later], now, current=later)
    create.assert_not_called()
    replace.assert_not_called()
    assert store.read("NEW") is None
    assert store.read("MSFT") == saved
    result = store.publish("MSFT", "XNAS:MSFT", [], now,
                           error="option_timing_payload_too_large")
    assert result["points"] == saved["points"]
    assert result["current"] == saved["current"]
    assert result["last_error"]["reason"] == "option_timing_payload_too_large"
    assert read_current("MSFT", "XNAS:MSFT", True, result, now)["freshness"] == "stale"
    assert len(json.dumps({k: v for k, v in result.items() if k != "_etag"}).encode()) > initial_bytes + 10
    replace.assert_called_once()
    with pytest.raises(TimingInputError, match="option_timing_payload_too_large"):
        store.publish("MSFT", "XNAS:MSFT", [], now, error="x" * 20_000)
    assert store.read("MSFT") == result


def test_nonfinite_payload_is_rejected_atomically_before_write():
    container = MemoryContainer()
    store = OptionTimingStore(container)
    now = utc("2026-10-30T22:00:00")
    initial = make_point(14)
    saved = store.publish("MSFT", "XNAS:MSFT", [initial], now, current=initial)
    later = make_point(17)
    later["covered_call"]["factors"][0]["score"] = float("nan")
    with pytest.raises(TimingInputError, match="option_timing_payload_not_json_serializable"):
        store.publish("MSFT", "XNAS:MSFT", [later], now, current=later)
    assert store.read("MSFT") == saved
    assert container.sequence == 1


def test_job_payload_failure_is_identifiable_and_keeps_atomic_last_valid(monkeypatch):
    from src import option_timing_store

    store = OptionTimingStore(MemoryContainer())
    now = utc("2026-10-30T17:00:00")
    initial = make_point(14)
    saved = store.publish("MSFT", "XNAS:MSFT", [initial], now,
                          current=initial, backfilled=True)
    initial_bytes = len(json.dumps({k: v for k, v in saved.items() if k != "_etag"}).encode())
    monkeypatch.setattr(option_timing_store, "MAX_DOCUMENT_BYTES",
                        initial_bytes + option_timing_store.ERROR_RESERVE_BYTES + 10)
    doc = {"symbol": "MSFT", "security_id": "XNAS:MSFT", "exchange": "XNAS"}
    result = asyncio.run(process_symbol(store, Provider(market_history()), doc, None, now))
    assert result == {"status": "failed", "reason": "option_timing_payload_too_large"}
    current = store.read("MSFT")
    assert current["current"] == initial
    assert current["points"] == saved["points"]
    assert current["last_error"]["reason"] == result["reason"]


def full_retention_points():
    # Conservative 270-point bound: even three observations on every calendar
    # day fit, with genuine stressed-market factor/guard explanations.
    history = market_history()
    closes = history["Close"].to_numpy().copy()
    start = closes[-21]
    for index in range(20):
        closes[-20 + index] = start - (index + 1) * .6
    closes[-1] -= 15
    template = make_point(history=market_history(closes))
    template["source"] = {
        "provider": "yfinance", "provider_symbol": "MSFT",
        "price_basis": "split_and_dividend_adjusted_daily_ohlcv",
        "fetched_at": template["computed_at"], "timestamp_kind": "retrieval_cutoff",
        "market_timezone": "America/New_York",
    }
    points = []
    for offset in range(90):
        day = utc("2026-10-30T00:00:00") - timedelta(days=offset)
        for hour in (14, 17, 20):
            point = copy.deepcopy(template)
            point["as_of"] = point["computed_at"] = day.replace(hour=hour).isoformat()
            point["source_session"] = day.date().isoformat()
            point["snapshot_id"] = f"{offset:030d}{hour:02d}"
            point["source"]["fetched_at"] = point["computed_at"]
            points.append(point)
    return sorted(points, key=lambda point: point["as_of"])


def test_full_90_calendar_day_three_tick_payload_fits_with_saved_explanations():
    from src.option_timing_store import ERROR_RESERVE_BYTES, MAX_DOCUMENT_BYTES

    store = OptionTimingStore(MemoryContainer())
    points = full_retention_points()
    saved = store.publish("MSFT", "XNAS:MSFT", points, utc("2026-10-30T22:00:00"),
                          current=points[-1], backfilled=True)
    assert saved["points"] == points
    payload_bytes = len(json.dumps({k: v for k, v in saved.items() if k != "_etag"}).encode())
    assert payload_bytes < MAX_DOCUMENT_BYTES - ERROR_RESERVE_BYTES
    assert len(saved["points"]) == 270


def test_reconstructed_does_not_replace_observed_and_retention_bounded():
    store = OptionTimingStore(MemoryContainer())
    old = make_point()
    old["as_of"] = "2026-01-01T21:00:00+00:00"
    old["source_session"] = "2026-01-01"
    old["snapshot_id"] = "old"
    observed = make_point(17)
    daily = make_point(20, "reconstructed")
    store.publish("MSFT", "XNAS:MSFT", [old, daily, observed],
                  utc("2026-10-30T22:00:00"), current=observed)
    assert store.read("MSFT")["points"] == [observed]
    store.publish("MSFT", "XNAS:MSFT", [daily], utc("2026-10-30T22:00:00"))
    assert store.read("MSFT")["points"] == [observed]


def test_atomic_etag_retry_merges_competing_points_and_current():
    container = MemoryContainer()
    store = OptionTimingStore(container)
    p14, p17, p20 = make_point(14), make_point(17), make_point(20)
    now = utc("2026-10-30T22:00:00")
    store.publish("MSFT", "XNAS:MSFT", [p14], now, current=p14)
    container.conflict_hook = lambda: store.publish("MSFT", "XNAS:MSFT", [p20], now, current=p20)
    store.publish("MSFT", "XNAS:MSFT", [p17], now, current=p17)
    doc = store.read("MSFT")
    assert [p["as_of"] for p in doc["points"]] == [p14["as_of"], p17["as_of"], p20["as_of"]]
    assert doc["current"] == p20
    with pytest.raises(ValueError, match="identity_mismatch"):
        store.publish("MSFT", "XNYS:MSFT", [], now)

def test_old_success_cannot_clear_new_failure_or_downgrade_settled_close():
    store = OptionTimingStore(MemoryContainer())
    provisional = make_point(20)
    settled = snapshot(
        "MSFT", calculate_sides(market_history()), utc("2026-10-30T20:00:00"),
        utc("2026-10-30T20:30:00"), "2026-10-30", False,
    )
    now = utc("2026-10-30T21:00:00")
    store.publish("MSFT", "XNAS:MSFT", [settled], now, current=settled)
    store.publish("MSFT", "XNAS:MSFT", [], now, error="new_failure")
    store.publish("MSFT", "XNAS:MSFT", [provisional], now, current=provisional)
    doc = store.read("MSFT")
    assert doc["current"] == settled
    assert doc["last_error"]["reason"] == "new_failure"
    # A genuinely later successful refresh may clear the failed-attempt state,
    # while preserving the immutable already-stored settled explanation.
    refreshed = copy.deepcopy(settled)
    refreshed["computed_at"] = utc("2026-10-30T21:30:00").isoformat()
    store.publish("MSFT", "XNAS:MSFT", [refreshed], now, current=refreshed)
    assert store.read("MSFT")["current"] == settled
    assert store.read("MSFT")["last_error"] is None


def test_failed_keeps_last_valid_explicit_stale_and_initial_missing_null():
    store = OptionTimingStore(MemoryContainer())
    now = utc("2026-10-30T22:00:00")
    p = make_point(20)
    store.publish("MSFT", "XNAS:MSFT", [p], now, current=p)
    store.publish("MSFT", "XNAS:MSFT", [], now, error="provider_history_unavailable")
    result = read_current("MSFT", "XNAS:MSFT", True, store.read("MSFT"), now)
    assert result["snapshot_id"] == p["snapshot_id"]
    assert result["freshness"] == "stale"
    assert result["last_error"]["reason"] == "provider_history_unavailable"
    assert result["covered_call"]["score"] == p["covered_call"]["score"]
    assert result["covered_call"]["adjustments"][-1]["cap"] is None
    assert "provider_history_unavailable" in result["covered_call"]["adjustments"][-1]["reason"]
    assert store.read("MSFT")["current"] == p  # Operational failure does not mutate history.
    missing = read_current("NEW", "XNAS:NEW", True, None, now)
    assert missing["covered_call"]["score"] is None
    assert missing["covered_call"]["status"] == "unavailable"
    assert missing["input_timestamp_available"] is False
    foreign = read_current("MSFT", "XLON:MSFT", False, store.read("MSFT"), now)
    assert foreign["cash_secured_put"]["status"] == "not_applicable"
    assert foreign["cash_secured_put"]["score"] is None
    broken = SimpleNamespace(container=Mock(read_item=Mock(side_effect=RuntimeError("offline"))))
    assert load_current(broken, "MSFT", "XNAS:MSFT", True)["covered_call"]["reason_codes"] == ["storage_read_failed"]


class Provider:
    def __init__(self, history):
        self.history = history
        self.calls = []

    async def get_ohlcv_history(self, symbol, period):
        self.calls.append((symbol, period))
        return self.history.copy() if self.history is not None else None


def test_job_fresh_fetches_each_tick_includes_current_and_backfills_once(monkeypatch):
    store = OptionTimingStore(MemoryContainer())
    provider = Provider(market_history())
    doc = {"symbol": "MSFT", "security_id": "XNAS:MSFT", "exchange": "XNAS"}
    for hour in (14, 17, 20):
        now = utc(f"2026-10-30T{hour}:00:00")
        if hour == 17:
            from src import option_timing_job
            monkeypatch.setattr(option_timing_job, "reconstruct", Mock(side_effect=AssertionError("backfill repeated")))
        assert asyncio.run(process_symbol(store, provider, doc, None, now))["status"] == "available"
        current = store.read("MSFT")["current"]
        assert current["as_of"] == now.isoformat()
        assert current["source_session"] == "2026-10-30"
        assert current["provisional"] is True  # At closing bell remains provisional until settled.
    saved = store.read("MSFT")
    assert saved["backfilled"]
    assert len([p for p in saved["points"] if p["source_session"] == "2026-10-30"]) == 3
    assert any(p["history_kind"] == "reconstructed" for p in saved["points"])
    assert provider.calls == [("MSFT", "2y")] * 3


def test_job_premarket_does_not_backdate_live_quote_into_previous_session():
    store = OptionTimingStore(MemoryContainer())
    provider = Provider(market_history())
    doc = {"symbol": "MSFT", "security_id": "XNAS:MSFT", "exchange": "XNAS"}
    now = utc("2026-10-30T12:00:00")
    asyncio.run(process_symbol(store, provider, doc, None, now))
    point = store.read("MSFT")["current"]
    assert point["source_session"] == "2026-10-29"
    assert point["as_of"] == "2026-10-29T20:00:00+00:00"
    assert point["computed_at"] == now.isoformat()
    assert point["provisional"] is False
    expected = calculate_sides(provider.history.iloc[:-1])
    assert point["covered_call"] == expected["covered_call"]

def test_provider_history_fresh_daily_adjusted_request_each_time(monkeypatch):
    from src import yfinance_data_provider

    history = market_history()
    ticker = Mock()
    ticker.history.return_value = history
    factory = Mock(return_value=ticker)
    monkeypatch.setattr(yfinance_data_provider.yf, "Ticker", factory)
    provider = yfinance_data_provider.YFinanceDataProvider.__new__(
        yfinance_data_provider.YFinanceDataProvider,
    )
    for _ in range(2):
        assert asyncio.run(provider.get_ohlcv_history("MSFT", period="2y")) is history
    assert factory.call_count == 2
    assert ticker.history.call_count == 2
    ticker.history.assert_called_with(
        period="2y", interval="1d", auto_adjust=True, prepost=False,
    )


def test_job_unavailable_warmup_and_noneligible_skip_fetch():
    store = OptionTimingStore(MemoryContainer())
    provider = Provider(market_history(bars=219))
    doc = {"symbol": "MSFT", "security_id": "XNAS:MSFT", "exchange": "XNAS"}
    now = utc("2026-10-30T17:00:00")
    result = asyncio.run(process_symbol(store, provider, doc, None, now))
    assert result == {"status": "failed", "reason": "warmup_requires_220_valid_bars"}
    assert store.read("MSFT")["current"] is None
    assert store.read("MSFT")["last_error"]["reason"] == result["reason"]
    calls = len(provider.calls)
    foreign = {**doc, "exchange": "XLON", "security_id": "XLON:MSFT"}
    assert asyncio.run(process_symbol(store, provider, foreign, None, now))["status"] == "not_applicable"
    assert len(provider.calls) == calls
    cosmos = SimpleNamespace(container=store.container, list_symbols=lambda: [doc])
    with pytest.raises(RuntimeError, match="Option timing failures"):
        asyncio.run(run_option_timing(cosmos, provider, now))


class APICosmos:
    def __init__(self):
        self.container = MemoryContainer()
        self.portfolio_container = None
        self.symbols = [
            {"symbol": "MSFT", "security_id": "XNAS:MSFT", "exchange": "XNAS", "positions": []},
            {"symbol": "FOREIGN", "security_id": "XLON:FOREIGN", "exchange": "XLON", "positions": []},
        ]
        for doc in self.symbols:
            self.container.create_item({
                "id": "sec_" + doc["security_id"].replace(":", "_"),
                "symbol": doc["symbol"], "doc_type": "security_master",
                "security_id": doc["security_id"], "ticker": doc["symbol"],
                "exchange_mic": doc["exchange"],
            })

    def list_symbols(self):
        return copy.deepcopy(self.symbols)

    def get_symbol(self, symbol):
        return next((copy.deepcopy(d) for d in self.symbols if d["symbol"] == symbol), None)

    def get_plans(self, *args, **kwargs):
        return []

    get_recent_activities = get_plans
    get_recent_alerts = get_plans

    def get_next_earnings_date(self, symbol):
        return None


@pytest.fixture
def api_client(monkeypatch):
    from web.app import app

    cosmos = APICosmos()
    monkeypatch.setattr(app.state, "cosmos", cosmos, raising=False)
    monkeypatch.setattr(app.state, "cosmos_error", None, raising=False)
    # No lifespan: do not construct live providers or production Cosmos clients.
    client = TestClient(app)
    yield client, cosmos
    client.close()


def test_overview_detail_list_share_persisted_snapshot_without_motor_calls(api_client, monkeypatch):
    client, cosmos = api_client
    store = OptionTimingStore(cosmos.container)
    point = make_point(20)
    store.publish("MSFT", "XNAS:MSFT", [point], utc("2026-10-30T22:00:00"), current=point)
    from src import option_timing
    monkeypatch.setattr(option_timing, "calculate_sides", Mock(side_effect=AssertionError("API recomputed")))
    overview = client.get("/api/symbols/overview")
    assert overview.status_code == 200, overview.text
    row = next(r for r in overview.json()["rows"] if r["symbol"] == "MSFT")
    detail = client.get("/api/symbols/XNAS:MSFT/detail")
    assert detail.status_code == 200, detail.text
    assert row["option_timing"] == detail.json()["option_timing"]
    listed = client.get("/api/symbols").json()
    raw = client.get("/api/symbols/MSFT").json()
    assert listed[0]["option_timing"] == raw["option_timing"] == row["option_timing"]
    foreign = next(r for r in overview.json()["rows"] if r["symbol"] == "FOREIGN")
    assert foreign["option_timing"]["covered_call"]["status"] == "not_applicable"
    assert sum("SELECT c.symbol, c.security_id, c.current" in q for q in cosmos.container.queries) == 2


def test_history_endpoint_persisted_explanations_range_and_identity(api_client):
    client, cosmos = api_client
    now = utc("2026-10-30T22:00:00")
    store = OptionTimingStore(cosmos.container)
    for hour in (14, 17, 20):
        point = make_point(hour)
        store.publish("MSFT", "XNAS:MSFT", [point], now, current=point)
    response = client.get("/api/symbols/XNAS:MSFT/option-timing/history?range=90d")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["security_id"] == "XNAS:MSFT"
    assert len(body["points"]) == 3
    assert all(p["covered_call"]["factors"] for p in body["points"])
    assert "not calibrated" in body["methodology"]
    assert client.get("/api/symbols/MSFT/option-timing/history").json() == body
    for value in ("0d", "91d", "-1d", "all", "90000d"):
        assert client.get(f"/api/symbols/MSFT/option-timing/history?range={value}").status_code == 422
    assert client.get("/api/symbols/NONE/option-timing/history").status_code == 404
    cosmos.container.create_item({
        "id": "sec_XNYS_MSFT", "symbol": "MSFT", "doc_type": "security_master",
        "security_id": "XNYS:MSFT", "ticker": "MSFT", "exchange_mic": "XNYS",
    })
    assert client.get("/api/symbols/MSFT/option-timing/history").status_code == 300
    assert client.get("/api/symbols/XNYS:MSFT/option-timing/history").status_code == 409


def test_registry_utc_default_three_runs_and_live_config_reload():
    registry = TaskRegistry()
    registry.register("option_timing", "Option Timing", "option_timing", DEFAULT_CRON,
                      lambda: None, cron_timezone="UTC")
    config = SimpleNamespace(config={"option_timing": {"enabled": True}})
    start = utc("2026-10-30T12:00:00").astimezone(ZoneInfo("Europe/Madrid"))
    task = registry.get_task("option_timing")
    assert registry.initialize_task(task, config, start)
    times = [task.next_run, task.cron_obj.get_next(type(start)), task.cron_obj.get_next(type(start))]
    assert [t.hour for t in times] == [14, 17, 20]
    assert all(str(t.tzinfo) == "UTC" for t in times)
    next_run = task.cron_obj.get_next(type(start))
    assert next_run.date().isoformat() == "2026-11-02"
    assert registry.get_all_task_metadata()[0]["timezone"] == "UTC"
    registry._config = config
    registry.reload_from_cosmos(config, {"option_timing": {"cron": "0 18 * * 1-5"}})
    registry.handle_cron_changes(start)
    assert task.next_run.hour == 18
    registry.reschedule("option_timing", "0 19 * * 1-5", config)
    registry.handle_cron_changes(start)
    assert task.next_run.hour == 19
    registry.update_task_enabled("option_timing", False, config)
    assert task.enabled is False
    registry.update_task_enabled("option_timing", True, config)
    assert str(task.next_run.tzinfo) == "UTC"


def test_config_and_main_register_the_dedicated_job():
    from pathlib import Path

    from src import main

    config = yaml.safe_load(Path("backend/config.yaml").read_text())
    assert config["option_timing"] == {"enabled": True, "cron": DEFAULT_CRON}
    # Exercise the public scheduler wrapper with fake dependencies only.
    scheduler_class = next(
        obj for obj in vars(main).values()
        if isinstance(obj, type) and hasattr(obj, "run_option_timing_job")
    )
    scheduler = scheduler_class.__new__(scheduler_class)
    scheduler.registry = TaskRegistry()
    scheduler.config = SimpleNamespace(config={"option_timing": {"enabled": False}})
    scheduler.run_option_timing_job()  # Disabled path must not initialize a provider.
    scheduler.registry.register("option_timing", "Option Timing", "option_timing",
                                DEFAULT_CRON, scheduler.run_option_timing_job, cron_timezone="UTC")
    scheduler.reschedule_option_timing("0 18 * * 1-5")
    assert scheduler.config.config["option_timing"]["cron"] == "0 18 * * 1-5"
