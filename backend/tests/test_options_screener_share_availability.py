"""Basher's adversarial tests for the Share Availability feature.

Design authority: `.squad/decisions/inbox/danny-options-screener-share-availability.md`

These tests exercise the REAL FastAPI screener endpoint (`GET /api/screener/options`)
together with a REAL `src.best_options_cache.BestOptionsCache` and REAL
`src.options_screener.evaluate_options_screener`.  The chain-evaluation seam
is bypassed by injecting precomputed envelopes directly into `BestOptionsCache`
(the "precomputed-only" path the endpoint requires post-refactor).  Only the
true external edges are faked:
  - Cosmos (`FakeShareAvailabilityCosmos`) — supports `positions` on every symbol doc.
  - Precomputed envelopes built once via a real `evaluate_best_options` call.

Hermetic: no network, no real Cosmos, no real LLM.

Coverage:
  1.  total=0 and total=99 → `no_shares`; exact numeric metadata.
  2.  total=100, zero active calls → `available`, free=100, free_lots=1.
  3.  total=100, one active call → `shares_committed`, committed=100, free=0.
  4.  total=200, one active call → `available`, free=100, free_lots=1 (key user example).
  5.  total=200, two active calls → `shares_committed`.
  6.  Closed calls and active puts do NOT commit shares.
  7.  Malformed/negative total_shares is clamped → `no_shares`.
  8.  Fields on call rows only; put rows untouched; filter silently ignored on puts.
  9.  Filter: single value, multi-value OR, omit/empty = all, unknown = 400.
  10. Filter precedes pagination: total_matching/returned/has_more are post-filter.
  11. Show-all (filter omitted) returns every admitted row regardless of status.
  12. Single-symbol Best Options section-level `no_shares_held` unchanged.
  13. Frontend contract: shape assertions for Rusty's TypeScript implementation.
"""

from __future__ import annotations

import pathlib
from datetime import date, datetime, timedelta, timezone

import pytest
from starlette.testclient import TestClient

from src.best_options import evaluate_best_options
from src.best_options_cache import BestOptionsCache, set_best_options_cache
from web.app import app


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc)
_TODAY = date(2026, 9, 5)
_REPO_ROOT = pathlib.Path(__file__).parents[2]
_FRONTEND_SRC = _REPO_ROOT / "frontend" / "src"


def _exp_key(days: int) -> str:
    return (_TODAY + timedelta(days=days)).strftime("%Y%m%d")


# ---------------------------------------------------------------------------
# Helpers — chain and envelope builders
# ---------------------------------------------------------------------------

def _call_chain(symbol: str, *, strike: float = 105.0, days: int = 20) -> dict:
    """Minimal chain that produces at least one admitted covered-call row (balanced
    category, delta ~0.25, in-band [0.20, 0.30]).  Every test that needs a real
    row uses this; the exact strike/delta choice is deliberately the same recipe
    proven by test_options_screener.py so it reliably admits without re-deriving
    the scoring math here."""
    mid = round((1.2 + 1.3) / 2, 4)
    return {
        "symbol": symbol,
        "timestamp": "2026-09-05T11:00:00Z",
        "underlying_price": 100.0,
        "calls": {
            _exp_key(days): {
                f"{strike}": {
                    "strike": strike,
                    "bid": 1.2,
                    "ask": 1.3,
                    "mid": mid,
                    "iv": 0.30,
                    "delta": 0.25,
                    "gamma": 0.01,
                    "theta": -0.02,
                    "vega": 0.05,
                    "rho": 0.01,
                    "lastPrice": 1.2,
                    "openInterest": 500,
                    "volume": 10,
                    "inTheMoney": False,
                    "_meta": {
                        "quote_asof": "2026-09-05T11:00:00Z",
                        "greeks_valid": True,
                        "greeks_asof": "2026-09-05T11:00:00Z",
                    },
                }
            }
        },
        "puts": {
            _exp_key(days): {
                "96.0": {
                    "strike": 96.0,
                    "bid": 1.0,
                    "ask": 1.05,
                    "mid": 1.025,
                    "iv": 0.30,
                    "delta": -0.25,
                    "gamma": 0.01,
                    "theta": -0.02,
                    "vega": 0.05,
                    "rho": -0.01,
                    "lastPrice": 1.0,
                    "openInterest": 2000,
                    "volume": 10,
                    "inTheMoney": False,
                    "_meta": {
                        "quote_asof": "2026-09-05T11:00:00Z",
                        "greeks_valid": True,
                        "greeks_asof": "2026-09-05T11:00:00Z",
                    },
                }
            }
        },
    }


def _make_envelope(chain: dict) -> dict:
    return evaluate_best_options(
        chain,
        side="both",
        category="balanced",
        total_shares=0,
        next_earnings_date=None,
        ex_dividend_date=None,
        support_level=None,
        dte_min=0,
        dte_max=45,
        now=_NOW,
    )


def _cache_entry(symbol: str, envelope: dict) -> dict:
    return {
        "symbol": symbol,
        "status": "ok",
        "envelope": envelope,
        "generation": 1,
        "computed_at": "2026-09-05T00:00:00Z",
        "chain_stale_at_compute": False,
        "inputs": {"category": "balanced", "total_shares": 0},
        "error": None,
        "reason": None,
        "refreshing": False,
        "refresh_started_at": None,
        "refresh_completed_at": None,
        "refresh_error": None,
        "chain_refresh_error": None,
    }


def _snapshot(entries: dict) -> dict:
    return {
        "generation": 1,
        "entries": entries,
        "cycle_started_at": "2026-09-05T00:00:00Z",
        "cycle_finished_at": "2026-09-05T00:01:00Z",
        "cycle_duration_seconds": 60.0,
        "trigger": "scheduled",
        "truncated": False,
        "counts": {
            "ok": len(entries),
            "stale": 0,
            "error": 0,
            "warming": 0,
        },
    }


def _active_call() -> dict:
    return {"status": "active", "type": "call"}


def _closed_call() -> dict:
    return {"status": "closed", "type": "call"}


def _active_put() -> dict:
    return {"status": "active", "type": "put"}


# ---------------------------------------------------------------------------
# Cosmos fake — independently authored, supports `positions`
# ---------------------------------------------------------------------------

class FakeShareAvailabilityCosmos:
    """Minimal fake Cosmos for share-availability tests.  Each symbol doc may
    carry `total_shares` and `positions` (list of dicts with `status`/`type`)
    to exercise the `_build_share_availability_map` logic in app.py."""

    def __init__(self):
        self._docs: list[dict] = []

    def add_symbol(
        self,
        symbol: str,
        *,
        total_shares: int | None = 0,
        positions: list[dict] | None = None,
        category: str = "balanced",
    ) -> None:
        self._docs.append(
            {
                "symbol": symbol,
                "enrichment": {"category": category},
                "total_shares": total_shares,
                "positions": positions if positions is not None else [],
            }
        )

    def list_symbols(self) -> list[dict]:
        return list(self._docs)

    def get_calendar_events(self) -> list[dict]:
        return []


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# Envelopes are computed once at module load (real evaluate_best_options call)
# to keep individual test run time minimal.  One chain per test-alphabet symbol.
_ENVELOPES: dict[str, dict] = {}


def _get_envelope(symbol: str) -> dict:
    if symbol not in _ENVELOPES:
        _ENVELOPES[symbol] = _make_envelope(_call_chain(symbol))
    return _ENVELOPES[symbol]


@pytest.fixture(autouse=True)
def _reset_best_options_cache_singleton():
    """Save/restore the BestOptionsCache process singleton around every test so
    injected snapshots never bleed between tests."""
    import src.best_options_cache as mod
    saved = mod._cache_instance
    yield
    set_best_options_cache(saved)


@pytest.fixture
def client():
    app.router.on_startup = []
    return TestClient(app, raise_server_exceptions=False)


def _setup_cache(*symbols: str) -> None:
    """Inject precomputed envelopes into BestOptionsCache for each symbol."""
    entries = {sym: _cache_entry(sym, _get_envelope(sym)) for sym in symbols}
    cache = BestOptionsCache()
    cache.publish_snapshot(_snapshot(entries))
    set_best_options_cache(cache)


def _set_cosmos(client: TestClient, cosmos: FakeShareAvailabilityCosmos) -> None:
    client.app.state.cosmos = cosmos


# ---------------------------------------------------------------------------
# Helper: build and inject a single-symbol scenario
# ---------------------------------------------------------------------------

def _make_scenario(
    client: TestClient,
    symbol: str,
    *,
    total_shares: int | None = 0,
    positions: list[dict] | None = None,
) -> None:
    """Inject one symbol with a precomputed envelope and cosmos metadata."""
    _setup_cache(symbol)
    cosmos = FakeShareAvailabilityCosmos()
    cosmos.add_symbol(symbol, total_shares=total_shares, positions=positions)
    _set_cosmos(client, cosmos)


# ---------------------------------------------------------------------------
# Case 1 — zero and sub-lot shares → no_shares, exact numeric metadata
# ---------------------------------------------------------------------------

class TestNoSharesStatus:
    def test_zero_total_shares_yields_no_shares(self, client):
        _make_scenario(client, "SYM1A", total_shares=0, positions=[])
        resp = client.get("/api/screener/options", params={"side": "call"})
        assert resp.status_code == 200
        rows = resp.json()["rows"]
        assert len(rows) >= 1, "fixture must produce at least one call row"
        for row in rows:
            assert row["share_status"] == "no_shares"
            assert row["total_shares"] == 0
            assert row["active_call_count"] == 0
            assert row["free_lots"] == 0

    def test_99_total_shares_yields_no_shares(self, client):
        _make_scenario(client, "SYM1B", total_shares=99, positions=[])
        resp = client.get("/api/screener/options", params={"side": "call"})
        assert resp.status_code == 200
        rows = resp.json()["rows"]
        assert len(rows) >= 1
        for row in rows:
            assert row["share_status"] == "no_shares"
            assert row["total_shares"] == 99
            assert row["active_call_count"] == 0
            assert row["free_lots"] == 0


# ---------------------------------------------------------------------------
# Case 2 — exactly one lot, no active calls → available
# ---------------------------------------------------------------------------

class TestAvailableStatus:
    def test_100_shares_no_active_calls_yields_available_with_one_free_lot(self, client):
        _make_scenario(client, "SYM2", total_shares=100, positions=[])
        resp = client.get("/api/screener/options", params={"side": "call"})
        assert resp.status_code == 200
        rows = resp.json()["rows"]
        assert len(rows) >= 1
        for row in rows:
            assert row["share_status"] == "available"
            assert row["total_shares"] == 100
            assert row["active_call_count"] == 0
            assert row["free_lots"] == 1


# ---------------------------------------------------------------------------
# Case 3 — 100 shares, one active call → shares_committed
# ---------------------------------------------------------------------------

class TestSharesCommittedStatus:
    def test_100_shares_one_active_call_yields_shares_committed(self, client):
        _make_scenario(client, "SYM3", total_shares=100, positions=[_active_call()])
        resp = client.get("/api/screener/options", params={"side": "call"})
        assert resp.status_code == 200
        rows = resp.json()["rows"]
        assert len(rows) >= 1
        for row in rows:
            assert row["share_status"] == "shares_committed"
            assert row["total_shares"] == 100
            assert row["active_call_count"] == 1
            assert row["free_lots"] == 0


# ---------------------------------------------------------------------------
# Case 4 — the key user example: 200 shares, 1 active call → available
# ---------------------------------------------------------------------------

class TestUserKeyExample:
    def test_200_shares_one_active_call_yields_available_one_free_lot(self, client):
        _make_scenario(client, "SYM4", total_shares=200, positions=[_active_call()])
        resp = client.get("/api/screener/options", params={"side": "call"})
        assert resp.status_code == 200
        rows = resp.json()["rows"]
        assert len(rows) >= 1
        for row in rows:
            assert row["share_status"] == "available"
            assert row["total_shares"] == 200
            assert row["active_call_count"] == 1
            assert row["free_lots"] == 1


# ---------------------------------------------------------------------------
# Case 5 — 200 shares, two active calls → shares_committed
# ---------------------------------------------------------------------------

class TestTwoActiveCallsCommit:
    def test_200_shares_two_active_calls_yields_shares_committed(self, client):
        _make_scenario(client, "SYM5", total_shares=200, positions=[_active_call(), _active_call()])
        resp = client.get("/api/screener/options", params={"side": "call"})
        assert resp.status_code == 200
        rows = resp.json()["rows"]
        assert len(rows) >= 1
        for row in rows:
            assert row["share_status"] == "shares_committed"
            assert row["total_shares"] == 200
            assert row["active_call_count"] == 2
            assert row["free_lots"] == 0


# ---------------------------------------------------------------------------
# Case 6 — closed calls and active puts do NOT commit shares
# ---------------------------------------------------------------------------

class TestNonActivePositionsIgnored:
    def test_closed_call_does_not_commit_shares(self, client):
        """A closed (expired/rolled/assigned) call must not consume a lot."""
        _make_scenario(
            client, "SYM6A",
            total_shares=100,
            positions=[_closed_call()],  # closed — must be ignored
        )
        resp = client.get("/api/screener/options", params={"side": "call"})
        assert resp.status_code == 200
        rows = resp.json()["rows"]
        assert len(rows) >= 1
        for row in rows:
            assert row["share_status"] == "available", (
                "A closed call must not count toward active_call_count"
            )
            assert row["active_call_count"] == 0

    def test_active_put_does_not_commit_shares(self, client):
        """An active cash-secured put is irrelevant to share availability."""
        _make_scenario(
            client, "SYM6B",
            total_shares=100,
            positions=[_active_put()],  # active put — must be ignored
        )
        resp = client.get("/api/screener/options", params={"side": "call"})
        assert resp.status_code == 200
        rows = resp.json()["rows"]
        assert len(rows) >= 1
        for row in rows:
            assert row["share_status"] == "available", (
                "An active put must not count toward active_call_count"
            )
            assert row["active_call_count"] == 0

    def test_mixed_closed_calls_and_active_puts_with_100_shares_yields_available(self, client):
        """100 shares + 1 closed call + 1 active put = 0 committed → available."""
        _make_scenario(
            client, "SYM6C",
            total_shares=100,
            positions=[_closed_call(), _active_put()],
        )
        resp = client.get("/api/screener/options", params={"side": "call"})
        assert resp.status_code == 200
        rows = resp.json()["rows"]
        assert len(rows) >= 1
        for row in rows:
            assert row["share_status"] == "available"
            assert row["active_call_count"] == 0
            assert row["free_lots"] == 1


# ---------------------------------------------------------------------------
# Case 7 — malformed / negative total_shares clamped to 0 → no_shares
# ---------------------------------------------------------------------------

class TestMalformedShareCounts:
    def test_none_total_shares_clamped_to_zero_yields_no_shares(self, client):
        _make_scenario(client, "SYM7A", total_shares=None, positions=[])
        resp = client.get("/api/screener/options", params={"side": "call"})
        assert resp.status_code == 200
        rows = resp.json()["rows"]
        assert len(rows) >= 1
        for row in rows:
            assert row["share_status"] == "no_shares"
            assert row["total_shares"] == 0

    def test_negative_total_shares_clamped_to_zero_yields_no_shares(self, client):
        _make_scenario(client, "SYM7B", total_shares=-50, positions=[])
        resp = client.get("/api/screener/options", params={"side": "call"})
        assert resp.status_code == 200
        rows = resp.json()["rows"]
        assert len(rows) >= 1
        for row in rows:
            assert row["share_status"] == "no_shares"
            assert row["total_shares"] == 0

    def test_overcommitted_position_clamps_free_shares_to_zero(self, client):
        """300 shares, 4 active calls = 400 committed → free clamped to 0."""
        _make_scenario(
            client, "SYM7C",
            total_shares=300,
            positions=[_active_call(), _active_call(), _active_call(), _active_call()],
        )
        resp = client.get("/api/screener/options", params={"side": "call"})
        assert resp.status_code == 200
        rows = resp.json()["rows"]
        assert len(rows) >= 1
        for row in rows:
            assert row["share_status"] == "shares_committed"
            assert row["active_call_count"] == 4
            assert row["free_lots"] == 0

    def test_no_positions_key_in_doc_treated_as_zero_active_calls(self, client):
        """A symbol doc that omits the positions array is safe — no KeyError."""
        # Directly inject a doc without 'positions' key
        _setup_cache("SYM7D")
        cosmos = FakeShareAvailabilityCosmos()
        cosmos._docs.append(
            {"symbol": "SYM7D", "enrichment": {"category": "balanced"}, "total_shares": 100}
        )
        _set_cosmos(client, cosmos)
        resp = client.get("/api/screener/options", params={"side": "call"})
        assert resp.status_code == 200
        rows = resp.json()["rows"]
        assert len(rows) >= 1
        for row in rows:
            assert row["share_status"] == "available"
            assert row["active_call_count"] == 0


# ---------------------------------------------------------------------------
# Case 8 — call rows carry share fields; put rows do not
# ---------------------------------------------------------------------------

class TestShareStatusPutSideDefect:
    """`share_status`, `total_shares`, `active_call_count`, `free_lots` are
    covered-call-only fields.  They must never appear on put rows.
    `share_availability` filter is silently ignored on side=put."""

    def test_put_side_rows_do_not_carry_share_status_field(self, client):
        _make_scenario(client, "PUTSYM8", total_shares=200, positions=[_active_call()])
        resp = client.get("/api/screener/options", params={"side": "put"})
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["rows"]) >= 1, "fixture must admit a put row"
        for row in body["rows"]:
            assert "share_status" not in row, "share_status must not appear on put rows"
            assert "total_shares" not in row, "total_shares must not appear on put rows"
            assert "active_call_count" not in row, "active_call_count must not appear on put rows"
            assert "free_lots" not in row, "free_lots must not appear on put rows"
            assert "no_shares_held" not in row, "legacy no_shares_held must not appear on any row"

    def test_put_side_ignores_share_availability_filter_silently(self, client):
        """share_availability=available on side=put must return all put rows,
        not zero (the filter is silently ignored, never applied)."""
        _make_scenario(client, "PUTSYM8B", total_shares=0, positions=[])
        resp_all = client.get("/api/screener/options", params={"side": "put"})
        resp_filtered = client.get(
            "/api/screener/options",
            params={"side": "put", "share_availability": "available"},
        )
        assert resp_filtered.status_code == 200
        # Filtering on puts must produce same row count as unfiltered
        assert len(resp_filtered.json()["rows"]) == len(resp_all.json()["rows"])

    def test_call_side_rows_carry_all_four_share_fields(self, client):
        _make_scenario(client, "CALLSYM8", total_shares=200, positions=[_active_call()])
        resp = client.get("/api/screener/options", params={"side": "call"})
        assert resp.status_code == 200
        rows = resp.json()["rows"]
        assert len(rows) >= 1
        for row in rows:
            assert "share_status" in row
            assert "total_shares" in row
            assert "active_call_count" in row
            assert "free_lots" in row
            assert "no_shares_held" not in row

    def test_no_shares_held_absent_from_all_rows(self, client):
        """Legacy `no_shares_held` must be removed from every call row."""
        _make_scenario(client, "LEGACYSYM", total_shares=0, positions=[])
        resp = client.get("/api/screener/options", params={"side": "call"})
        assert resp.status_code == 200
        for row in resp.json()["rows"]:
            assert "no_shares_held" not in row, (
                "no_shares_held is a removed field — must not appear on call rows"
            )


# ---------------------------------------------------------------------------
# Case 9 — filter: single value, multi-value OR, omitted = all, unknown = 400
# ---------------------------------------------------------------------------

class TestShareAvailabilityFilter:
    """Filter correctness: each value, combinations, defaults, invalid input."""

    @pytest.fixture
    def three_symbol_client(self, client):
        """Three symbols: AVAIL (200 shares, 1 call), COMMT (100 shares, 1 call),
        NOSHA (0 shares).  Returns (client, expected_statuses_by_symbol)."""
        _setup_cache("AVAIL9", "COMMT9", "NOSHA9")
        cosmos = FakeShareAvailabilityCosmos()
        cosmos.add_symbol("AVAIL9", total_shares=200, positions=[_active_call()])
        cosmos.add_symbol("COMMT9", total_shares=100, positions=[_active_call()])
        cosmos.add_symbol("NOSHA9", total_shares=0, positions=[])
        _set_cosmos(client, cosmos)
        return client

    def test_filter_available_returns_only_available_rows(self, three_symbol_client):
        resp = three_symbol_client.get(
            "/api/screener/options",
            params={"side": "call", "share_availability": "available"},
        )
        assert resp.status_code == 200
        rows = resp.json()["rows"]
        assert len(rows) >= 1
        for row in rows:
            assert row["share_status"] == "available", (
                f"Expected only available rows; got {row['share_status']} for {row['symbol']}"
            )

    def test_filter_no_shares_returns_only_no_shares_rows(self, three_symbol_client):
        resp = three_symbol_client.get(
            "/api/screener/options",
            params={"side": "call", "share_availability": "no_shares"},
        )
        assert resp.status_code == 200
        rows = resp.json()["rows"]
        assert len(rows) >= 1
        for row in rows:
            assert row["share_status"] == "no_shares"

    def test_filter_shares_committed_returns_only_committed_rows(self, three_symbol_client):
        resp = three_symbol_client.get(
            "/api/screener/options",
            params={"side": "call", "share_availability": "shares_committed"},
        )
        assert resp.status_code == 200
        rows = resp.json()["rows"]
        assert len(rows) >= 1
        for row in rows:
            assert row["share_status"] == "shares_committed"

    def test_filter_multi_value_or_excludes_third_status(self, three_symbol_client):
        """no_shares,shares_committed must exclude available rows."""
        resp = three_symbol_client.get(
            "/api/screener/options",
            params={"side": "call", "share_availability": "no_shares,shares_committed"},
        )
        assert resp.status_code == 200
        rows = resp.json()["rows"]
        statuses = {r["share_status"] for r in rows}
        assert "available" not in statuses
        assert len(rows) >= 2, "Expected at least one no_shares + one shares_committed row"

    def test_filter_available_committed_includes_both_statuses(self, three_symbol_client):
        """available,shares_committed = all symbols with ≥100 shares."""
        resp = three_symbol_client.get(
            "/api/screener/options",
            params={"side": "call", "share_availability": "available,shares_committed"},
        )
        assert resp.status_code == 200
        rows = resp.json()["rows"]
        statuses = {r["share_status"] for r in rows}
        assert "no_shares" not in statuses

    def test_filter_omitted_returns_all_rows(self, three_symbol_client):
        """Omitting share_availability must return rows for all three statuses."""
        resp = three_symbol_client.get("/api/screener/options", params={"side": "call"})
        assert resp.status_code == 200
        rows = resp.json()["rows"]
        statuses = {r["share_status"] for r in rows}
        assert "available" in statuses
        assert "shares_committed" in statuses
        assert "no_shares" in statuses

    def test_filter_null_in_response_when_omitted(self, three_symbol_client):
        """Omitting share_availability → filters.share_availability == null."""
        resp = three_symbol_client.get("/api/screener/options", params={"side": "call"})
        assert resp.json()["filters"]["share_availability"] is None

    def test_filter_echoed_in_response_when_set(self, three_symbol_client):
        resp = three_symbol_client.get(
            "/api/screener/options",
            params={"side": "call", "share_availability": "available"},
        )
        assert resp.json()["filters"]["share_availability"] == ["available"]

    def test_unknown_share_availability_value_returns_400(self, client):
        resp = client.get(
            "/api/screener/options",
            params={"side": "call", "share_availability": "bogus"},
        )
        assert resp.status_code == 400
        assert "share_availability" in resp.json()["error"].lower() or "bogus" in resp.json()["error"]

    def test_unknown_value_in_comma_list_returns_400(self, client):
        resp = client.get(
            "/api/screener/options",
            params={"side": "call", "share_availability": "available,garbage"},
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Case 10 — filter before pagination: total_matching/returned/has_more
# ---------------------------------------------------------------------------

class TestFilterBeforePagination:
    """share_availability filtering must apply before pagination, so
    total_matching reflects the post-filter count and has_more is accurate."""

    def test_total_matching_reflects_post_filter_count(self, client):
        # Three symbols, each producing one call row; inject all three.
        _setup_cache("PGN_AV", "PGN_CM", "PGN_NS")
        cosmos = FakeShareAvailabilityCosmos()
        cosmos.add_symbol("PGN_AV", total_shares=200, positions=[_active_call()])
        cosmos.add_symbol("PGN_CM", total_shares=100, positions=[_active_call()])
        cosmos.add_symbol("PGN_NS", total_shares=0, positions=[])
        _set_cosmos(client, cosmos)

        # Without filter: all three symbols should appear
        resp_all = client.get("/api/screener/options", params={"side": "call"})
        assert resp_all.status_code == 200
        total_all = resp_all.json()["pagination"]["total_matching"]
        assert total_all >= 3

        # With filter=available: only PGN_AV rows
        resp_filt = client.get(
            "/api/screener/options",
            params={"side": "call", "share_availability": "available"},
        )
        assert resp_filt.status_code == 200
        body_filt = resp_filt.json()
        total_filt = body_filt["pagination"]["total_matching"]
        assert total_filt < total_all, "post-filter total_matching must be lower than unfiltered"
        assert total_filt == len(body_filt["rows"]), "returned must equal total_matching when limit > count"
        for row in body_filt["rows"]:
            assert row["share_status"] == "available"

    def test_has_more_correct_across_mixed_statuses(self, client):
        """limit=1 with filter=no_shares,shares_committed: has_more reflects
        post-filter count, not total universe count."""
        _setup_cache("HM_AV", "HM_CM", "HM_NS")
        cosmos = FakeShareAvailabilityCosmos()
        cosmos.add_symbol("HM_AV", total_shares=200, positions=[_active_call()])
        cosmos.add_symbol("HM_CM", total_shares=100, positions=[_active_call()])
        cosmos.add_symbol("HM_NS", total_shares=0, positions=[])
        _set_cosmos(client, cosmos)

        resp = client.get(
            "/api/screener/options",
            params={
                "side": "call",
                "share_availability": "no_shares,shares_committed",
                "limit": 1,
                "offset": 0,
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        pgn = body["pagination"]
        assert pgn["returned"] == 1
        # total_matching must be at least 2 (one no_shares + one shares_committed)
        assert pgn["total_matching"] >= 2
        assert pgn["has_more"] is True


# ---------------------------------------------------------------------------
# Case 11 — show-all: filter omitted → all contracts visible
# ---------------------------------------------------------------------------

class TestShowAll:
    def test_omitted_filter_shows_all_admitted_contracts_regardless_of_status(self, client):
        """Absence of share_availability must not hide any admitted rows."""
        _setup_cache("SHOWALL_AV", "SHOWALL_CM", "SHOWALL_NS")
        cosmos = FakeShareAvailabilityCosmos()
        cosmos.add_symbol("SHOWALL_AV", total_shares=200, positions=[_active_call()])
        cosmos.add_symbol("SHOWALL_CM", total_shares=100, positions=[_active_call()])
        cosmos.add_symbol("SHOWALL_NS", total_shares=0, positions=[])
        _set_cosmos(client, cosmos)

        resp_all = client.get("/api/screener/options", params={"side": "call"})
        assert resp_all.status_code == 200
        symbols = {r["symbol"] for r in resp_all.json()["rows"]}
        assert "SHOWALL_AV" in symbols
        assert "SHOWALL_CM" in symbols
        assert "SHOWALL_NS" in symbols

    def test_filters_echo_null_for_share_availability_when_show_all(self, client):
        _make_scenario(client, "SA_ECHO", total_shares=100, positions=[])
        resp = client.get("/api/screener/options", params={"side": "call"})
        assert resp.json()["filters"]["share_availability"] is None


# ---------------------------------------------------------------------------
# NEW — Numeric row contract: committed_shares and free_shares on call rows
# DEFECT REGRESSION TESTS — these FAIL until D1/D2 are fixed
# ---------------------------------------------------------------------------

class TestNumericRowContract:
    """The accepted design's full numeric row contract requires every call row
    to carry `committed_shares` and `free_shares` — not merely the values the
    frontend could derive from `active_call_count` or `free_lots`.

    Design authority: `danny-options-screener-share-availability.md` §2.1
    (the share_availability map includes committed_shares and free_shares)
    and §4.2 tooltip template which references `{committed_shares}` as a
    named backend field, not a frontend derivation.

    All tests here document **DEFECT D1** (backend enrichment omits
    committed_shares / free_shares from call rows) and will fail until Linus
    (not the original author, per lockout) fixes app.py.
    """

    def test_committed_shares_present_on_call_row(self, client):
        """committed_shares must be a first-class field on call rows (DEFECT D1)."""
        _make_scenario(client, "D1_CM", total_shares=200, positions=[_active_call()])
        resp = client.get("/api/screener/options", params={"side": "call"})
        assert resp.status_code == 200
        rows = resp.json()["rows"]
        assert len(rows) >= 1
        for row in rows:
            assert "committed_shares" in row, (
                "DEFECT D1: committed_shares missing from call row API response. "
                "_build_share_availability_map computes it but app.py enrichment does not forward it."
            )

    def test_free_shares_present_on_call_row(self, client):
        """free_shares must be a first-class field on call rows (DEFECT D1)."""
        _make_scenario(client, "D1_FS", total_shares=200, positions=[_active_call()])
        resp = client.get("/api/screener/options", params={"side": "call"})
        assert resp.status_code == 200
        rows = resp.json()["rows"]
        assert len(rows) >= 1
        for row in rows:
            assert "free_shares" in row, (
                "DEFECT D1: free_shares missing from call row API response. "
                "_build_share_availability_map computes it but app.py enrichment does not forward it."
            )

    def test_committed_shares_value_correct_normal(self, client):
        """committed_shares = active_call_count * 100."""
        _make_scenario(client, "D1_CMV", total_shares=200, positions=[_active_call()])
        resp = client.get("/api/screener/options", params={"side": "call"})
        assert resp.status_code == 200
        for row in resp.json()["rows"]:
            assert row.get("committed_shares") == 100, (
                f"Expected committed_shares=100 (1 call * 100), got {row.get('committed_shares')}"
            )

    def test_free_shares_value_correct_normal(self, client):
        """free_shares = total_shares - committed_shares = 200 - 100 = 100."""
        _make_scenario(client, "D1_FSV", total_shares=200, positions=[_active_call()])
        resp = client.get("/api/screener/options", params={"side": "call"})
        assert resp.status_code == 200
        for row in resp.json()["rows"]:
            assert row.get("free_shares") == 100, (
                f"Expected free_shares=100, got {row.get('free_shares')}"
            )

    def test_committed_shares_and_free_shares_correct_when_zero_active_calls(self, client):
        """0 active calls: committed=0, free=total."""
        _make_scenario(client, "D1_ZERO", total_shares=300, positions=[])
        resp = client.get("/api/screener/options", params={"side": "call"})
        assert resp.status_code == 200
        for row in resp.json()["rows"]:
            assert row.get("committed_shares") == 0
            assert row.get("free_shares") == 300

    def test_free_shares_clamped_at_zero_when_overcommitted(self, client):
        """300 shares, 4 active calls: committed=400, free clamped to 0 (not -100)."""
        _make_scenario(
            client, "D1_OC",
            total_shares=300,
            positions=[_active_call(), _active_call(), _active_call(), _active_call()],
        )
        resp = client.get("/api/screener/options", params={"side": "call"})
        assert resp.status_code == 200
        for row in resp.json()["rows"]:
            assert row.get("committed_shares") == 400
            assert row.get("free_shares") == 0, (
                "free_shares must be clamped at 0, never negative"
            )

    def test_committed_shares_and_free_shares_absent_from_put_rows(self, client):
        """committed_shares and free_shares must not appear on put rows."""
        # Use a chain that produces put rows
        _setup_cache("D1_PUT")
        cosmos = FakeShareAvailabilityCosmos()
        cosmos.add_symbol("D1_PUT", total_shares=200, positions=[_active_call()])
        _set_cosmos(client, cosmos)
        resp = client.get("/api/screener/options", params={"side": "put"})
        assert resp.status_code == 200
        for row in resp.json()["rows"]:
            assert "committed_shares" not in row
            assert "free_shares" not in row


class TestPaginationWithFilter:
    """Verify filter-before-pagination for both default and non-default sort.
    These test that `needs_unbounded` triggers correctly in both paths."""

    @pytest.fixture
    def three_sym_client_with_oi(self, client):
        """Three symbols with DISTINCT open_interest values so OI sort is deterministic.
        PGN2_NS: oi=100 (no shares), PGN2_CM: oi=200 (committed), PGN2_AV: oi=300 (available)."""
        from src.best_options import evaluate_best_options

        def _chain_with_oi(sym: str, oi: int) -> dict:
            chain = _call_chain(sym)
            # Replace the oi in the calls bucket
            for exp_buckets in chain["calls"].values():
                for contract in exp_buckets.values():
                    contract["openInterest"] = oi
            return chain

        def _entry_with_oi(sym: str, oi: int) -> dict:
            chain = _chain_with_oi(sym, oi)
            envelope = _make_envelope(chain)
            return _cache_entry(sym, envelope)

        cache = BestOptionsCache()
        cache.publish_snapshot(_snapshot({
            "PGN2_AV": _entry_with_oi("PGN2_AV", 300),
            "PGN2_CM": _entry_with_oi("PGN2_CM", 200),
            "PGN2_NS": _entry_with_oi("PGN2_NS", 100),
        }))
        set_best_options_cache(cache)

        cosmos = FakeShareAvailabilityCosmos()
        cosmos.add_symbol("PGN2_AV", total_shares=200, positions=[_active_call()])
        cosmos.add_symbol("PGN2_CM", total_shares=100, positions=[_active_call()])
        cosmos.add_symbol("PGN2_NS", total_shares=0, positions=[])
        _set_cosmos(client, cosmos)
        return client

    def test_filter_default_sort_pagination_page_2(self, three_sym_client_with_oi):
        """With default sort + filter, offset=1 returns the 2nd post-filter row."""
        client = three_sym_client_with_oi
        resp = client.get("/api/screener/options", params={
            "side": "call", "share_availability": "no_shares,shares_committed",
            "limit": 1, "offset": 1,
        })
        assert resp.status_code == 200
        pgn = resp.json()["pagination"]
        assert pgn["total_matching"] == 2
        assert pgn["returned"] == 1
        assert pgn["has_more"] is False
        # The row must be one of the two non-available symbols
        row_sym = resp.json()["rows"][0]["symbol"]
        assert row_sym in {"PGN2_CM", "PGN2_NS"}

    def test_filter_nondefault_sort_pagination_sorted_and_filtered(self, three_sym_client_with_oi):
        """share_availability filter + open_interest asc: sorted THEN filtered THEN paginated.
        NOSHA(oi=100) < COMMT(oi=200) → NOSHA first, COMMT second; AVAIL excluded."""
        client = three_sym_client_with_oi

        # Page 1 (offset=0, limit=1)
        resp1 = client.get("/api/screener/options", params={
            "side": "call", "share_availability": "no_shares,shares_committed",
            "sort": "open_interest", "dir": "asc", "limit": 1, "offset": 0,
        })
        assert resp1.status_code == 200
        b1 = resp1.json()
        assert b1["pagination"]["total_matching"] == 2
        assert b1["pagination"]["returned"] == 1
        assert b1["pagination"]["has_more"] is True
        assert b1["rows"][0]["symbol"] == "PGN2_NS", (
            "After filtering to no_shares+shares_committed, ascending OI sort must put "
            "PGN2_NS (oi=100) first"
        )

        # Page 2 (offset=1, limit=1)
        resp2 = client.get("/api/screener/options", params={
            "side": "call", "share_availability": "no_shares,shares_committed",
            "sort": "open_interest", "dir": "asc", "limit": 1, "offset": 1,
        })
        b2 = resp2.json()
        assert b2["pagination"]["total_matching"] == 2
        assert b2["pagination"]["returned"] == 1
        assert b2["pagination"]["has_more"] is False
        assert b2["rows"][0]["symbol"] == "PGN2_CM", (
            "Page 2 must be PGN2_CM (oi=200) after PGN2_NS (oi=100)"
        )
        # PGN2_AV must never appear in either page — it's filtered out
        assert "PGN2_AV" not in {b1["rows"][0]["symbol"], b2["rows"][0]["symbol"]}


class TestFrontendContractExtended:
    """Extended frontend contract tests for the committed_shares / free_shares
    fields that DEFECT D2 makes fail.  Documents what Rusty must fix."""

    def _read(self, *rel: str) -> str:
        path = _FRONTEND_SRC.joinpath(*rel)
        if not path.exists():
            pytest.skip(f"Frontend source not available at {path}")
        return path.read_text(encoding="utf-8")

    def test_screener_ts_has_committed_shares_field(self):
        """ScreenerOptionRow must declare committed_shares?: number (DEFECT D2)."""
        src = self._read("types", "screener.ts")
        assert "committed_shares" in src and "?" in src, (
            "DEFECT D2: ScreenerOptionRow must declare committed_shares?: number "
            "(the backend sends it as a backend-computed value, not a frontend derivation)"
        )

    def test_screener_ts_has_free_shares_field(self):
        """ScreenerOptionRow must declare free_shares?: number (DEFECT D2)."""
        src = self._read("types", "screener.ts")
        import re
        # Must be a property declaration, not just a comment
        field_decl = re.search(r"^\s*free_shares\s*\??\s*:", src, re.MULTILINE)
        assert field_decl is not None, (
            "DEFECT D2: ScreenerOptionRow must declare free_shares?: number; "
            "currently only mentioned in a comment"
        )

    def test_options_screener_view_uses_row_committed_shares_not_derived(self):
        """The shares_committed tooltip must read row.committed_shares, not recompute it
        as active_call_count * 100 (DEFECT D2)."""
        src = self._read("components", "OptionsScreenerView.tsx")
        import re
        # Detect the recomputation anti-pattern:
        # (row.active_call_count ?? 0) * 100
        recompute_pattern = re.search(
            r"active_call_count\s*[\?\?]*\s*0\)\s*\*\s*100", src
        )
        assert recompute_pattern is None, (
            "DEFECT D2: OptionsScreenerView.tsx recomputes committed_shares as "
            "(active_call_count ?? 0) * 100 instead of reading row.committed_shares. "
            "The backend must send committed_shares and the frontend must use it."
        )



class TestBestOptionsNoSharesHeldUnchanged:
    """The single-symbol `/api/symbols/{symbol}/best-options` endpoint is OUT
    OF SCOPE for the share-availability redesign.  Its section-level
    `no_shares_held` boolean (on `calls`, never on `puts`) must remain intact
    and correct — not replaced by `share_status`.  This class is a read-only
    regression guard; it does NOT test the screener endpoint.

    Fixture pattern mirrors `test_best_options_frontend_contract.py`'s
    FakeCosmos + _make_cache + cache.get_or_load approach verbatim."""

    @pytest.fixture
    def bo_client(self, monkeypatch):
        from src.options_chain_cache import OptionsChainCache, set_options_chain_cache
        from src.options_chain_store import OptionsChainStore

        class _FakeBOCosmos:
            def __init__(self):
                self.symbols: dict = {}

            def get_symbol(self, sym: str):
                return self.symbols.get(sym)

            def get_next_earnings_date(self, sym: str):
                return None

            def get_next_calendar_event_date(self, sym: str, event_type: str):
                return None

        fake_cosmos = _FakeBOCosmos()
        app.router.on_startup = []
        app.state.cosmos = fake_cosmos

        def _make_bo_cache(chain: dict) -> OptionsChainCache:
            cache = OptionsChainCache(
                ttl_seconds=1800, store=OptionsChainStore(enabled=False)
            )

            async def _yf(_sym: str) -> dict:
                return chain

            async def _tv(_sym: str) -> dict:
                return chain

            monkeypatch.setattr(cache, "_fetch_yfinance", _yf)
            monkeypatch.setattr(cache, "_fetch_tradingview", _tv)
            return cache

        yield TestClient(app, raise_server_exceptions=False), fake_cosmos, _make_bo_cache

    def test_no_shares_held_is_true_on_call_section_when_zero_shares(self, bo_client):
        from src.options_chain_cache import set_options_chain_cache

        c, fake_cosmos, make_cache = bo_client
        fake_cosmos.symbols["BOTEST"] = {
            "enrichment": {"category": "balanced"}, "total_shares": 0
        }
        cache = make_cache(_call_chain("BOTEST"))
        cache.get_or_load("BOTEST")
        set_options_chain_cache(cache)

        body = c.get("/api/symbols/BOTEST/best-options?support_level=100.0").json()
        assert body["calls"]["no_shares_held"] is True, (
            "Best Options section-level no_shares_held must be True for 0 shares"
        )
        assert "no_shares_held" not in body["puts"], (
            "no_shares_held must not appear on the puts section"
        )
        for row in body["calls"].get("rows", []):
            assert "share_status" not in row.get("flags", {}), (
                "share_status is a screener-only field; best-options rows must not carry it"
            )

    def test_no_shares_held_is_false_on_call_section_when_shares_held(self, bo_client):
        from src.options_chain_cache import set_options_chain_cache

        c, fake_cosmos, make_cache = bo_client
        fake_cosmos.symbols["BOTEST2"] = {
            "enrichment": {"category": "balanced"}, "total_shares": 300
        }
        cache = make_cache(_call_chain("BOTEST2"))
        cache.get_or_load("BOTEST2")
        set_options_chain_cache(cache)

        body = c.get("/api/symbols/BOTEST2/best-options?support_level=100.0").json()
        assert body["calls"]["no_shares_held"] is False


# ---------------------------------------------------------------------------
# Case 13 — frontend contract: static TypeScript source assertions
# ---------------------------------------------------------------------------

class TestFrontendContract:
    """Static assertions against TypeScript source files that Rusty owns.
    Each test reads the source text and asserts required patterns; a compile
    check (`npx tsc --noEmit`) is the definitive gate — these are
    regression-prevention guards for the shapes and patterns that a pure
    compile cannot catch (wrong field names, legacy `no_shares_held` use).

    Tests in this class will PASS immediately if Rusty's implementation is
    correct, and FAIL with a clear message if a required change is missing.
    """

    def _read(self, *rel: str) -> str:
        path = _FRONTEND_SRC.joinpath(*rel)
        if not path.exists():
            pytest.skip(f"Frontend source not available at {path}")
        return path.read_text(encoding="utf-8")

    def test_screener_ts_has_ShareStatus_type(self):
        """screener.ts must export ShareStatus union type per spec §7."""
        src = self._read("types", "screener.ts")
        assert "ShareStatus" in src, (
            "screener.ts must export a `ShareStatus` type "
            "(\"available\" | \"shares_committed\" | \"no_shares\")"
        )
        assert "shares_committed" in src
        assert "no_shares" in src

    def test_screener_ts_has_share_status_field_on_row(self):
        """ScreenerOptionRow must have share_status, total_shares, active_call_count, free_lots."""
        src = self._read("types", "screener.ts")
        assert "share_status" in src, "ScreenerOptionRow must declare share_status"
        assert "active_call_count" in src, "ScreenerOptionRow must declare active_call_count"
        assert "free_lots" in src, "ScreenerOptionRow must declare free_lots"

    def test_screener_ts_share_availability_on_filters(self):
        """ScreenerFilters must have share_availability field."""
        src = self._read("types", "screener.ts")
        assert "share_availability" in src, (
            "ScreenerFilters must declare share_availability: ShareStatus[] | null"
        )

    def test_screener_ts_no_shares_held_removed_from_row(self):
        """The per-row `no_shares_held?: boolean` must be removed from ScreenerOptionRow.
        It is a section-level field on best-options only, never per-row on the screener."""
        src = self._read("types", "screener.ts")
        # The type should no longer declare no_shares_held on ScreenerOptionRow.
        # A comment referencing it in the docstring is acceptable; a field declaration is not.
        import re
        # Find field declarations (lines like `no_shares_held?: boolean;`)
        field_decl = re.search(r"^\s*no_shares_held\s*\??\s*:", src, re.MULTILINE)
        assert field_decl is None, (
            "no_shares_held must be removed from ScreenerOptionRow; "
            "use share_status instead"
        )

    def test_options_screener_view_has_share_availability_multiselect(self):
        """OptionsScreenerView.tsx must include a Share Availability MultiSelect
        visible only on the Calls tab (per spec §4.1)."""
        src = self._read("components", "OptionsScreenerView.tsx")
        assert "share_availability" in src.lower() or "Share Availability" in src, (
            "OptionsScreenerView.tsx must include a Share Availability filter widget"
        )

    def test_options_screener_view_no_legacy_no_shares_held_row_badge(self):
        """The old `row.no_shares_held &&` badge branch must be removed; replaced
        by share_status-driven badges.  Any remaining reference to the old field
        on a screener row is a defect."""
        src = self._read("components", "OptionsScreenerView.tsx")
        import re
        # Detect: row.no_shares_held
        assert not re.search(r"row\.no_shares_held", src), (
            "OptionsScreenerView.tsx still references the removed row.no_shares_held field; "
            "replace with share_status badge logic"
        )

    def test_options_screener_view_has_share_status_badge(self):
        """Call rows must render a badge for share_status values other than 'available'."""
        src = self._read("components", "OptionsScreenerView.tsx")
        assert "share_status" in src, (
            "OptionsScreenerView.tsx must render badges based on row.share_status"
        )

    def test_options_row_format_no_shares_held_removed_from_flag_labels(self):
        """FLAG_LABELS in options-row-format.tsx must not contain 'no_shares_held'
        (it was removed from screener rows; the Best Options per-row flags section
        never had it either — it was section-level only)."""
        src = self._read("lib", "options-row-format.tsx")
        import re
        # A FLAG_LABELS entry for no_shares_held must be removed
        flag_entry = re.search(r"no_shares_held\s*:", src)
        assert flag_entry is None, (
            "FLAG_LABELS in options-row-format.tsx still contains 'no_shares_held'; "
            "this was removed in the share-availability redesign"
        )
