"""Smoke test for the deterministic price-forecast cron (src/forecast_cron.py).

No network, no CosmosDB: uses a fake provider returning a synthetic dated OHLCV
DataFrame and a fake cosmos capturing writes.
"""

import asyncio
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from src.forecast_cron import run_forecast_cron
from src.price_forecast import compute_forecast


def _make_history(periods=60, end=None):
    """Synthetic OHLCV DataFrame ending at ``end`` (defaults to today UTC)."""
    end = end or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    idx = pd.date_range(end=end, periods=periods, freq="D")
    rng = np.random.default_rng(42)
    closes = 100.0 + np.cumsum(rng.normal(0, 0.5, periods))
    df = pd.DataFrame({
        "Open": closes * 0.999,
        "High": closes * 1.006,
        "Low": closes * 0.994,
        "Close": closes,
        "Volume": [1_000_000] * periods,
    }, index=idx)
    return df


class _FakeProvider:
    def __init__(self, history):
        self._history = history

    async def get_ohlcv_history(self, symbol, period="1y"):
        return self._history


class _FakeCosmos:
    def __init__(self, open_forecasts):
        self._open = open_forecasts
        self.written = []

    def list_symbols(self):
        return [{"symbol": "TEST"}]

    def get_open_price_forecasts(self, symbol, as_of):
        return [dict(p) for p in self._open]

    def write_price_forecast(self, symbol, forecast):
        self.written.append(forecast)
        return forecast

    def prune_price_forecasts(self, symbol, cutoff):
        return 0

    def get_next_earnings_date(self, symbol):
        return None

    def get_next_calendar_event_date(self, symbol, event_type):
        return None


def test_cron_generates_and_validates():
    history = _make_history()
    session_dates = [d.strftime("%Y-%m-%d") for d in history.index]
    today = session_dates[-1]

    # An open prediction created 1 session ago → today is its 1d endpoint.
    base = compute_forecast(100.0, 0.20)
    open_pred = {
        "id": "TEST_forecast_" + session_dates[-2],
        "symbol": "TEST",
        "doc_type": "price_forecast",
        "created_date": session_dates[-2],
        "start_date": session_dates[-2],
        "end_date": today,
        "status": "open",
        "bias": 0.5,
        "horizons": base["horizons"],
        "snapshots": [],
        "endpoints": {},
    }

    cosmos = _FakeCosmos([open_pred])
    provider = _FakeProvider(history)

    summary = asyncio.run(run_forecast_cron(cosmos, provider))

    assert summary["status"] == "completed"
    assert summary["created"] == 1          # today's new prediction
    assert summary["validated"] == 1        # the open prediction got a snapshot
    assert summary["resolved"] == 1         # 1d endpoint resolved

    # New prediction was written with today's created_date and all fields.
    new_pred = next(w for w in cosmos.written if w.get("created_date") == today)
    assert set(new_pred["horizons"].keys()) == {"1d", "1w", "2w", "4w"}
    assert new_pred["start_date"] == today
    assert new_pred["end_date"] > today
    assert "earnings_in_window" in new_pred["flags"]

    # The open prediction's endpoint was recorded.
    resolved = next(w for w in cosmos.written if w.get("created_date") == session_dates[-2])
    assert "1d" in resolved["endpoints"]
    assert resolved["endpoints"]["1d"]["date"] == today


def test_cron_skips_on_stale_history():
    # Latest session is well before today → holiday/stale guard trips.
    history = _make_history(end="2020-01-02")
    cosmos = _FakeCosmos([])
    provider = _FakeProvider(history)

    summary = asyncio.run(run_forecast_cron(cosmos, provider))
    assert summary["created"] == 0
    assert summary["skipped"] == 1


def test_cron_handles_missing_provider_and_cosmos():
    assert asyncio.run(run_forecast_cron(None, object()))["status"] == "skipped"
    assert asyncio.run(run_forecast_cron(object(), None))["status"] == "skipped"


class _FakeCosmosBackfill(_FakeCosmos):
    """Fake cosmos that also supports the per-doc lookup the backfill uses."""

    def __init__(self, existing_ids=None):
        super().__init__([])
        self._existing_ids = set(existing_ids or [])

    def get_price_forecast(self, symbol, forecast_id):
        return {"id": forecast_id} if forecast_id in self._existing_ids else None


def test_backfill_seeds_recent_sessions():
    from src.forecast_cron import backfill_symbol_forecasts

    history = _make_history(periods=120)  # plenty of warm-up beyond MIN_HISTORY_BARS
    cosmos = _FakeCosmosBackfill()
    provider = _FakeProvider(history)

    out = asyncio.run(backfill_symbol_forecasts(
        cosmos, provider, "TEST", sessions=25))

    assert out["reason"] == "ok"
    assert out["created"] == 25          # exactly the requested trailing sessions
    assert len(cosmos.written) == 25
    # All are point-in-time forecasts with the four horizons and marked backfilled.
    for w in cosmos.written:
        assert set(w["horizons"].keys()) == {"1d", "1w", "2w", "4w"}
        assert w["backfilled"] is True
        assert w["start_date"] == w["created_date"]
    # Older backfilled predictions have resolved endpoints (forward data exists).
    oldest = min(cosmos.written, key=lambda w: w["created_date"])
    assert oldest["endpoints"], "oldest prediction should have resolved endpoints"


def test_backfill_skips_existing_without_force():
    from src.forecast_cron import backfill_symbol_forecasts

    history = _make_history(periods=120)
    session_dates = [d.strftime("%Y-%m-%d") for d in history.index]
    # Pretend the two most recent sessions already have predictions.
    existing = {f"TEST_forecast_{d}" for d in session_dates[-2:]}
    cosmos = _FakeCosmosBackfill(existing_ids=existing)
    provider = _FakeProvider(history)

    out = asyncio.run(backfill_symbol_forecasts(
        cosmos, provider, "TEST", sessions=25))

    assert out["created"] == 23
    assert out["skipped"] == 2


def test_backfill_insufficient_history():
    from src.forecast_cron import backfill_symbol_forecasts

    history = _make_history(periods=20)  # below MIN_HISTORY_BARS
    cosmos = _FakeCosmosBackfill()
    provider = _FakeProvider(history)

    out = asyncio.run(backfill_symbol_forecasts(
        cosmos, provider, "TEST", sessions=25))

    assert out["created"] == 0
    assert out["reason"] == "insufficient_history"


def test_backfill_handles_missing_provider():
    from src.forecast_cron import backfill_symbol_forecasts

    out = asyncio.run(backfill_symbol_forecasts(object(), None, "TEST"))
    assert out["created"] == 0
    assert out["reason"] == "unavailable"
