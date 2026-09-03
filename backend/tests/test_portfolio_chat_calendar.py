"""
Test suite for Portfolio Chat calendar context (POST /api/chat, mode=portfolio).

Covers the `include_calendar_events` flag per docs/chat.md contract:

  1. Flag false/omitted  → no get_calendar_events() call, no UPCOMING CALENDAR section.
  2. Flag true           → exactly one call; events filtered to context_symbols only.
  3. Date window         → today (inclusive) through +3 calendar months (inclusive);
                           yesterday and the day after window_end are excluded.
  4. Month-end arithmetic → true calendar-month clamping, not 90-day approximation.
  5. Unknown types, invalid dates, missing symbols silently ignored.
  6. Deduplication       → (symbol, type, date) deduplicated; output sorted by
                           (date, symbol, type).
  7. has_active_position → "[active position]" label when true; absent otherwise.
  8. Enabled but empty   → explicit "No earnings or ex-dividend events found …" marker.
  9. Calendar failure    → request succeeds; activities context preserved;
                           "(Calendar data unavailable)" marker in prompt.
  10. Frontend contract  → default-off toggle; include_calendar_events sent only
                           in portfolio mode payload.

Pattern: hermetic — no network, no real LLM, no real CosmosDB.
"""

import datetime as _dt_mod
import re
from pathlib import Path

import pytest
from starlette.testclient import TestClient


# ===========================================================================
# Captured LLM messages (module-level; cleared by autouse fixture)
# ===========================================================================

_CAPTURED: list = []


# ===========================================================================
# Fakes
# ===========================================================================

class FakeCosmos:
    """Minimal in-memory CosmosDB fake for portfolio-chat tests."""

    def __init__(self, symbols=None, calendar_events=None, calendar_raises=False):
        self._symbols: list = symbols or []
        self._calendar_events: list = calendar_events or []
        self._calendar_raises = calendar_raises
        self.calendar_call_count = 0

    def list_symbols(self):
        return list(self._symbols)

    def get_recent_activities(self, symbol, agent_key, max_entries=3,
                               position_id=None, include_alerts=False):
        return []

    def get_calendar_events(self):
        self.calendar_call_count += 1
        if self._calendar_raises:
            raise RuntimeError("Simulated calendar read failure")
        return list(self._calendar_events)


def _sym(symbol, agents=("buy_tracker",)):
    """Return a minimal symbol-config dict that registers `symbol` in `context_symbols`."""
    return {
        "symbol": symbol,
        "display_name": symbol,
        "watchlist": {a: True for a in agents},
        "positions": [],
        "enrichment": None,
    }


def _cal(symbol, ev_type, ev_date, has_active_position=None):
    """Return a raw calendar-event dict as get_calendar_events() yields."""
    ev = {"symbol": symbol, "type": ev_type, "date": ev_date}
    if has_active_position is not None:
        ev["has_active_position"] = has_active_position
    return ev


class FakeConfig:
    """Minimal Config stub — always valid, returns sentinel model name."""

    def model_for(self, function_id):
        return "fake-model"

    def llm_config_for_function(self, function_id):
        return {"endpoint": "https://fake.azure.com", "key": "fake-key"}

    def llm_config(self):
        return {"endpoint": "https://fake.azure.com", "key": "fake-key"}


# ===========================================================================
# LLM stubs
# ===========================================================================

def _fake_validate(cfg):
    return None  # always valid


def _fake_sync_client(cfg):
    return object()


def _fake_chat_completion(client, model, messages, temperature=0.7,
                           max_completion_tokens=2048):
    _CAPTURED.clear()
    _CAPTURED.extend(messages)
    return "MOCK REPLY"


def _system_prompt():
    """Extract the system-role message content from the last captured call."""
    for m in _CAPTURED:
        if isinstance(m, dict) and m.get("role") == "system":
            return m["content"]
    return ""


# ===========================================================================
# Fixtures
# ===========================================================================

@pytest.fixture(autouse=True)
def _clear_captured():
    _CAPTURED.clear()
    yield
    _CAPTURED.clear()


@pytest.fixture
def make_client(monkeypatch):
    """
    Factory fixture: make_client(cosmos) → TestClient.

    Patches Config, validate_llm_config, create_sync_chat_client, and
    chat_completion so tests are hermetic.
    """
    from web.app import app  # ensures web.app is in sys.modules

    monkeypatch.setattr("src.config.Config", lambda: FakeConfig())
    monkeypatch.setattr("src.llm.validate_llm_config", _fake_validate)
    monkeypatch.setattr("src.llm.create_sync_chat_client", _fake_sync_client)
    monkeypatch.setattr("src.llm.chat_completion", _fake_chat_completion)
    app.router.on_startup = []

    def _make(cosmos):
        app.state.cosmos = cosmos
        return TestClient(app, raise_server_exceptions=False)

    return _make


def _post(client, *, include_calendar_events=False,
          selected_agents=("buy_tracker",), message="context please"):
    """POST /api/chat in portfolio mode and return the response."""
    return client.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "content": message}],
            "mode": "portfolio",
            "selected_agents": list(selected_agents),
            "activities_limit": 1,
            "include_symbol_data": False,
            "include_calendar_events": include_calendar_events,
        },
    )


def _window():
    """
    Return (yesterday, today, window_end, day_after_end) using the same
    calendar-month arithmetic as _add_three_months in web.app.
    """
    from calendar import monthrange
    today = _dt_mod.date.today()
    yesterday = today - _dt_mod.timedelta(days=1)
    m = today.month - 1 + 3
    year = today.year + m // 12
    month = m % 12 + 1
    day = min(today.day, monthrange(year, month)[1])
    window_end = _dt_mod.date(year, month, day)
    day_after = window_end + _dt_mod.timedelta(days=1)
    return yesterday, today, window_end, day_after


# ===========================================================================
# 1. Flag false / omitted — no calendar read, no context section
# ===========================================================================

class TestCalendarFlagOff:

    def test_false_flag_never_calls_get_calendar_events(self, make_client):
        cosmos = FakeCosmos(symbols=[_sym("AAPL")])
        client = make_client(cosmos)
        resp = _post(client, include_calendar_events=False)
        assert resp.status_code == 200
        assert cosmos.calendar_call_count == 0

    def test_false_flag_produces_no_upcoming_calendar_section(self, make_client):
        cosmos = FakeCosmos(symbols=[_sym("AAPL")])
        client = make_client(cosmos)
        _post(client, include_calendar_events=False)
        # The static advisor instructions mention "UPCOMING CALENDAR" in prose;
        # the data section header is always "=== UPCOMING CALENDAR (NEXT 3 MONTHS) ===".
        assert "=== UPCOMING CALENDAR" not in _system_prompt()

    def test_omitted_flag_defaults_to_no_calendar_read(self, make_client):
        cosmos = FakeCosmos(symbols=[_sym("AAPL")])
        client = make_client(cosmos)
        resp = client.post(
            "/api/chat",
            json={
                "messages": [{"role": "user", "content": "Hi"}],
                "mode": "portfolio",
                "selected_agents": ["buy_tracker"],
                "activities_limit": 1,
                # include_calendar_events intentionally absent
            },
        )
        assert resp.status_code == 200
        assert cosmos.calendar_call_count == 0
        assert "=== UPCOMING CALENDAR" not in _system_prompt()


# ===========================================================================
# 2. Flag true — exactly one call; filtered to context_symbols
# ===========================================================================

class TestCalendarFlagOn:

    def test_exactly_one_get_calendar_events_call(self, make_client):
        _, today, _, _ = _window()
        cosmos = FakeCosmos(
            symbols=[_sym("AAPL")],
            calendar_events=[_cal("AAPL", "earnings", today.isoformat())],
        )
        client = make_client(cosmos)
        _post(client, include_calendar_events=True)
        assert cosmos.calendar_call_count == 1

    def test_events_for_non_context_symbol_are_excluded(self, make_client):
        _, today, _, _ = _window()
        # TSLA is NOT in list_symbols (not a context symbol).
        cosmos = FakeCosmos(
            symbols=[_sym("AAPL")],
            calendar_events=[
                _cal("AAPL", "earnings", today.isoformat()),
                _cal("TSLA", "earnings", today.isoformat()),
            ],
        )
        client = make_client(cosmos)
        _post(client, include_calendar_events=True)
        prompt = _system_prompt()
        assert "AAPL" in prompt
        assert "TSLA" not in prompt

    def test_multiple_context_symbols_all_included(self, make_client):
        _, today, _, _ = _window()
        cosmos = FakeCosmos(
            symbols=[_sym("AAPL"), _sym("MSFT")],
            calendar_events=[
                _cal("AAPL", "earnings", today.isoformat()),
                _cal("MSFT", "ex_dividend", today.isoformat()),
                _cal("NVDA", "earnings", today.isoformat()),  # not in context
            ],
        )
        client = make_client(cosmos)
        _post(client, include_calendar_events=True)
        prompt = _system_prompt()
        assert "AAPL" in prompt
        assert "MSFT" in prompt
        assert "NVDA" not in prompt

    def test_symbols_in_list_but_not_on_watchlist_are_not_context(self, make_client):
        # MSFT is in list_symbols but has no buy_tracker watchlist entry.
        _, today, _, _ = _window()
        cosmos = FakeCosmos(
            symbols=[
                _sym("AAPL"),
                {"symbol": "MSFT", "display_name": "MSFT",
                 "watchlist": {}, "positions": [], "enrichment": None},
            ],
            calendar_events=[
                _cal("AAPL", "earnings", today.isoformat()),
                _cal("MSFT", "earnings", today.isoformat()),
            ],
        )
        client = make_client(cosmos)
        _post(client, include_calendar_events=True)
        prompt = _system_prompt()
        # MSFT has no watchlist entry for buy_tracker, so it's not a context symbol.
        cal_section = prompt[prompt.find("=== UPCOMING CALENDAR"):]
        assert "MSFT" not in cal_section


# ===========================================================================
# 3. Inclusive date boundaries
# ===========================================================================

class TestDateWindowBoundaries:

    def test_today_is_included_in_window(self, make_client):
        _, today, _, _ = _window()
        cosmos = FakeCosmos(
            symbols=[_sym("AAPL")],
            calendar_events=[_cal("AAPL", "earnings", today.isoformat())],
        )
        client = make_client(cosmos)
        _post(client, include_calendar_events=True)
        prompt = _system_prompt()
        assert today.isoformat() in prompt

    def test_window_end_is_included_in_window(self, make_client):
        _, _, window_end, _ = _window()
        cosmos = FakeCosmos(
            symbols=[_sym("AAPL")],
            calendar_events=[_cal("AAPL", "earnings", window_end.isoformat())],
        )
        client = make_client(cosmos)
        _post(client, include_calendar_events=True)
        prompt = _system_prompt()
        assert window_end.isoformat() in prompt

    def test_yesterday_is_excluded_from_window(self, make_client):
        yesterday, _, _, _ = _window()
        # Provide only yesterday's event — if excluded, empty-calendar marker appears.
        cosmos = FakeCosmos(
            symbols=[_sym("AAPL")],
            calendar_events=[_cal("AAPL", "earnings", yesterday.isoformat())],
        )
        client = make_client(cosmos)
        _post(client, include_calendar_events=True)
        prompt = _system_prompt()
        # No events in window → empty-calendar marker.
        assert "No earnings or ex-dividend events found" in prompt

    def test_day_after_window_end_is_excluded_from_window(self, make_client):
        _, _, _, day_after = _window()
        cosmos = FakeCosmos(
            symbols=[_sym("AAPL")],
            calendar_events=[_cal("AAPL", "earnings", day_after.isoformat())],
        )
        client = make_client(cosmos)
        _post(client, include_calendar_events=True)
        prompt = _system_prompt()
        # No events in window → empty-calendar marker.
        assert "No earnings or ex-dividend events found" in prompt

    def test_boundary_events_mixed_with_out_of_window_events(self, make_client):
        yesterday, today, window_end, day_after = _window()
        cosmos = FakeCosmos(
            symbols=[_sym("AAPL")],
            calendar_events=[
                _cal("AAPL", "earnings", yesterday.isoformat()),   # excluded
                _cal("AAPL", "ex_dividend", today.isoformat()),    # included
                _cal("AAPL", "earnings", window_end.isoformat()),  # included
                _cal("AAPL", "ex_dividend", day_after.isoformat()), # excluded
            ],
        )
        client = make_client(cosmos)
        _post(client, include_calendar_events=True)
        prompt = _system_prompt()
        cal_section = prompt[prompt.find("=== UPCOMING CALENDAR"):]
        assert today.isoformat() in cal_section
        assert window_end.isoformat() in cal_section
        assert yesterday.isoformat() not in cal_section
        assert day_after.isoformat() not in cal_section


# ===========================================================================
# 4. True calendar-month arithmetic at a month-end case
# ===========================================================================

class TestCalendarMonthEndArithmetic:
    """
    Verify _add_three_months uses true month clamping (e.g. Jan 31 → Apr 30),
    not 90-day approximation (Jan 31 + 90 days in a non-leap year = May 1).
    """

    def test_add_three_months_january_end_non_leap_year(self):
        from web.app import _add_three_months
        result = _add_three_months(_dt_mod.date(2025, 1, 31))
        assert result == _dt_mod.date(2025, 4, 30), (
            "_add_three_months(2025-01-31) must return 2025-04-30, not 2025-05-01 "
            "(90-day approximation would give May 1 in a non-leap year)"
        )

    def test_add_three_months_november_end_year_wrap(self):
        from web.app import _add_three_months
        result = _add_three_months(_dt_mod.date(2025, 11, 30))
        assert result == _dt_mod.date(2026, 2, 28), (
            "_add_three_months(2025-11-30) must clamp to 2026-02-28 (Feb has 28 days)"
        )

    def test_add_three_months_october_end_wraps_to_january(self):
        from web.app import _add_three_months
        result = _add_three_months(_dt_mod.date(2025, 10, 31))
        assert result == _dt_mod.date(2026, 1, 31), (
            "_add_three_months(2025-10-31) must return 2026-01-31"
        )

    def test_month_end_boundary_inclusion_with_frozen_today(self, make_client, monkeypatch):
        """
        With today frozen to 2025-01-31: window_end = 2025-04-30.
        An event on 2025-04-30 is included; an event on 2025-05-01 is excluded.
        90-day approximation would give window_end = 2025-05-01, incorrectly including May 1.
        """
        import datetime as _dt

        class _FakeDT(_dt.datetime):
            @classmethod
            def now(cls, tz=None):
                return _dt.datetime(2025, 1, 31, 12, 0, 0, tzinfo=_dt.timezone.utc)

        monkeypatch.setattr("web.app.datetime", _FakeDT)

        cosmos = FakeCosmos(
            symbols=[_sym("AAPL")],
            calendar_events=[
                _cal("AAPL", "earnings", "2025-04-30"),  # window_end → included
                _cal("AAPL", "ex_dividend", "2025-05-01"),  # excluded by true math
            ],
        )
        client = make_client(cosmos)
        _post(client, include_calendar_events=True)
        prompt = _system_prompt()
        cal_section = prompt[prompt.find("=== UPCOMING CALENDAR"):]
        assert "2025-04-30" in cal_section
        assert "2025-05-01" not in cal_section


# ===========================================================================
# 5. Unknown types, invalid dates, and missing symbols ignored safely
# ===========================================================================

class TestCalendarFiltering:

    def test_unknown_event_type_is_silently_ignored(self, make_client):
        _, today, _, _ = _window()
        cosmos = FakeCosmos(
            symbols=[_sym("AAPL")],
            calendar_events=[
                _cal("AAPL", "split", today.isoformat()),         # unknown type
                _cal("AAPL", "special_dividend", today.isoformat()),
                _cal("AAPL", "earnings", today.isoformat()),      # valid
            ],
        )
        client = make_client(cosmos)
        _post(client, include_calendar_events=True)
        prompt = _system_prompt()
        cal_section = prompt[prompt.find("=== UPCOMING CALENDAR"):]
        # Only the earnings line should appear.
        assert cal_section.count("AAPL") == 1
        assert "Earnings" in cal_section
        assert "split" not in cal_section
        assert "special" not in cal_section

    def test_invalid_date_format_is_silently_ignored(self, make_client):
        _, today, _, _ = _window()
        cosmos = FakeCosmos(
            symbols=[_sym("AAPL")],
            calendar_events=[
                _cal("AAPL", "earnings", "not-a-date"),
                _cal("AAPL", "earnings", "2025/06/15"),  # wrong separator
                _cal("AAPL", "earnings", ""),             # empty
                _cal("AAPL", "earnings", today.isoformat()),  # valid
            ],
        )
        client = make_client(cosmos)
        resp = _post(client, include_calendar_events=True)
        assert resp.status_code == 200
        prompt = _system_prompt()
        cal_section = prompt[prompt.find("=== UPCOMING CALENDAR"):]
        # Only the valid-date earnings row should appear.
        assert cal_section.count("AAPL") == 1

    def test_empty_symbol_is_silently_ignored(self, make_client):
        _, today, _, _ = _window()
        cosmos = FakeCosmos(
            symbols=[_sym("AAPL")],
            calendar_events=[
                _cal("", "earnings", today.isoformat()),     # empty symbol
                _cal("AAPL", "earnings", today.isoformat()), # valid
            ],
        )
        client = make_client(cosmos)
        resp = _post(client, include_calendar_events=True)
        assert resp.status_code == 200
        prompt = _system_prompt()
        cal_section = prompt[prompt.find("=== UPCOMING CALENDAR"):]
        assert "AAPL" in cal_section
        # Empty-symbol row should not introduce a bare line starting with whitespace only.
        lines = [ln for ln in cal_section.splitlines() if ln.strip().startswith("2")]
        assert all(ln.split()[1] != "" for ln in lines)

    def test_event_with_no_symbol_field_is_silently_ignored(self, make_client):
        _, today, _, _ = _window()
        cosmos = FakeCosmos(
            symbols=[_sym("AAPL")],
            calendar_events=[
                {"type": "earnings", "date": today.isoformat()},  # no symbol key
                _cal("AAPL", "ex_dividend", today.isoformat()),
            ],
        )
        client = make_client(cosmos)
        resp = _post(client, include_calendar_events=True)
        assert resp.status_code == 200

    def test_event_with_no_type_field_is_silently_ignored(self, make_client):
        _, today, _, _ = _window()
        cosmos = FakeCosmos(
            symbols=[_sym("AAPL")],
            calendar_events=[
                {"symbol": "AAPL", "date": today.isoformat()},   # no type key
                _cal("AAPL", "earnings", today.isoformat()),
            ],
        )
        client = make_client(cosmos)
        resp = _post(client, include_calendar_events=True)
        assert resp.status_code == 200
        # Only the valid event should appear once.
        prompt = _system_prompt()
        cal_section = prompt[prompt.find("=== UPCOMING CALENDAR"):]
        assert cal_section.count("AAPL") == 1


# ===========================================================================
# 6. Deduplication and deterministic sort
# ===========================================================================

class TestCalendarDeduplicationAndSort:

    def test_duplicate_symbol_type_date_triplet_emitted_once(self, make_client):
        _, today, _, _ = _window()
        today_str = today.isoformat()
        cosmos = FakeCosmos(
            symbols=[_sym("AAPL")],
            calendar_events=[
                _cal("AAPL", "earnings", today_str),
                _cal("AAPL", "earnings", today_str),  # exact duplicate
                _cal("AAPL", "earnings", today_str),  # exact duplicate
            ],
        )
        client = make_client(cosmos)
        _post(client, include_calendar_events=True)
        cal_section = _system_prompt()[_system_prompt().find("=== UPCOMING CALENDAR"):]
        # Exactly one AAPL Earnings line.
        earnings_lines = [ln for ln in cal_section.splitlines()
                          if "AAPL" in ln and "Earnings" in ln]
        assert len(earnings_lines) == 1

    def test_output_sorted_by_date_then_symbol_then_type(self, make_client, monkeypatch):
        """
        Provide three events in reverse order; verify the sorted output order
        is (date ASC, symbol ASC, type ASC) regardless of input order.
        Frozen to 2024-05-01 so window_end = 2024-08-01; all events fit.
        """
        import datetime as _dt

        class _FakeDT(_dt.datetime):
            @classmethod
            def now(cls, tz=None):
                return _dt.datetime(2024, 5, 1, 12, 0, 0, tzinfo=_dt.timezone.utc)

        monkeypatch.setattr("web.app.datetime", _FakeDT)

        cosmos = FakeCosmos(
            symbols=[_sym("AAPL"), _sym("MSFT")],
            calendar_events=[
                _cal("MSFT", "earnings", "2024-07-15"),     # should be last
                _cal("AAPL", "ex_dividend", "2024-06-01"),  # should be second
                _cal("AAPL", "earnings", "2024-06-01"),     # should be first (earnings < ex_dividend)
            ],
        )
        client = make_client(cosmos)
        _post(client, include_calendar_events=True)
        prompt = _system_prompt()
        cal_section = prompt[prompt.find("=== UPCOMING CALENDAR"):]
        # Extract data lines (start with a date string YYYY-MM-DD)
        data_lines = [ln.strip() for ln in cal_section.splitlines()
                      if re.match(r"^\d{4}-\d{2}-\d{2}", ln.strip())]
        assert len(data_lines) == 3
        assert data_lines[0].startswith("2024-06-01") and "Earnings" in data_lines[0]
        assert data_lines[1].startswith("2024-06-01") and "Ex-Dividend" in data_lines[1]
        assert data_lines[2].startswith("2024-07-15") and "MSFT" in data_lines[2]

    def test_different_dates_same_symbol_deduped_per_key(self, make_client):
        _, today, _, _ = _window()
        from datetime import timedelta
        tomorrow = today + timedelta(days=1)
        cosmos = FakeCosmos(
            symbols=[_sym("AAPL")],
            calendar_events=[
                _cal("AAPL", "earnings", today.isoformat()),
                _cal("AAPL", "earnings", today.isoformat()),    # dup
                _cal("AAPL", "earnings", tomorrow.isoformat()), # different date → kept
            ],
        )
        client = make_client(cosmos)
        _post(client, include_calendar_events=True)
        cal_section = _system_prompt()[_system_prompt().find("=== UPCOMING CALENDAR"):]
        earnings_lines = [ln for ln in cal_section.splitlines()
                          if "AAPL" in ln and "Earnings" in ln]
        assert len(earnings_lines) == 2  # today and tomorrow, each once


# ===========================================================================
# 7. has_active_position label
# ===========================================================================

class TestCalendarActivePositionLabel:

    def test_has_active_position_true_shows_label(self, make_client):
        _, today, _, _ = _window()
        cosmos = FakeCosmos(
            symbols=[_sym("AAPL")],
            calendar_events=[
                _cal("AAPL", "earnings", today.isoformat(), has_active_position=True),
            ],
        )
        client = make_client(cosmos)
        _post(client, include_calendar_events=True)
        cal_section = _system_prompt()[_system_prompt().find("=== UPCOMING CALENDAR"):]
        assert "[active position]" in cal_section

    def test_has_active_position_false_no_label(self, make_client):
        _, today, _, _ = _window()
        cosmos = FakeCosmos(
            symbols=[_sym("AAPL")],
            calendar_events=[
                _cal("AAPL", "earnings", today.isoformat(), has_active_position=False),
            ],
        )
        client = make_client(cosmos)
        _post(client, include_calendar_events=True)
        cal_section = _system_prompt()[_system_prompt().find("=== UPCOMING CALENDAR"):]
        assert "[active position]" not in cal_section

    def test_has_active_position_absent_no_label(self, make_client):
        _, today, _, _ = _window()
        cosmos = FakeCosmos(
            symbols=[_sym("AAPL")],
            # has_active_position key not present at all
            calendar_events=[_cal("AAPL", "ex_dividend", today.isoformat())],
        )
        client = make_client(cosmos)
        _post(client, include_calendar_events=True)
        cal_section = _system_prompt()[_system_prompt().find("=== UPCOMING CALENDAR"):]
        assert "[active position]" not in cal_section

    def test_ex_dividend_event_labeled_correctly(self, make_client):
        _, today, _, _ = _window()
        cosmos = FakeCosmos(
            symbols=[_sym("AAPL")],
            calendar_events=[_cal("AAPL", "ex_dividend", today.isoformat())],
        )
        client = make_client(cosmos)
        _post(client, include_calendar_events=True)
        cal_section = _system_prompt()[_system_prompt().find("=== UPCOMING CALENDAR"):]
        assert "Ex-Dividend" in cal_section
        assert "Earnings" not in cal_section

    def test_mixed_active_and_inactive_positions(self, make_client):
        _, today, _, _ = _window()
        from datetime import timedelta
        d2 = (today + timedelta(days=7)).isoformat()
        cosmos = FakeCosmos(
            symbols=[_sym("AAPL"), _sym("MSFT")],
            calendar_events=[
                _cal("AAPL", "earnings", today.isoformat(), has_active_position=True),
                _cal("MSFT", "earnings", d2, has_active_position=False),
            ],
        )
        client = make_client(cosmos)
        _post(client, include_calendar_events=True)
        cal_section = _system_prompt()[_system_prompt().find("=== UPCOMING CALENDAR"):]
        aapl_line = next(ln for ln in cal_section.splitlines() if "AAPL" in ln)
        msft_line = next(ln for ln in cal_section.splitlines() if "MSFT" in ln)
        assert "[active position]" in aapl_line
        assert "[active position]" not in msft_line


# ===========================================================================
# 8. Enabled but no matching events → explicit empty-calendar marker
# ===========================================================================

class TestCalendarEmptyResult:

    def test_no_events_at_all_produces_empty_marker(self, make_client):
        cosmos = FakeCosmos(symbols=[_sym("AAPL")], calendar_events=[])
        client = make_client(cosmos)
        _post(client, include_calendar_events=True)
        prompt = _system_prompt()
        assert "=== UPCOMING CALENDAR" in prompt
        assert "No earnings or ex-dividend events found" in prompt

    def test_all_events_out_of_window_produces_empty_marker(self, make_client):
        yesterday, _, _, day_after = _window()
        cosmos = FakeCosmos(
            symbols=[_sym("AAPL")],
            calendar_events=[
                _cal("AAPL", "earnings", yesterday.isoformat()),
                _cal("AAPL", "ex_dividend", day_after.isoformat()),
            ],
        )
        client = make_client(cosmos)
        _post(client, include_calendar_events=True)
        prompt = _system_prompt()
        assert "=== UPCOMING CALENDAR" in prompt
        assert "No earnings or ex-dividend events found" in prompt

    def test_all_events_wrong_symbol_produces_empty_marker(self, make_client):
        _, today, _, _ = _window()
        cosmos = FakeCosmos(
            symbols=[_sym("AAPL")],
            calendar_events=[
                _cal("TSLA", "earnings", today.isoformat()),  # not a context symbol
                _cal("NVDA", "ex_dividend", today.isoformat()),
            ],
        )
        client = make_client(cosmos)
        _post(client, include_calendar_events=True)
        prompt = _system_prompt()
        assert "No earnings or ex-dividend events found" in prompt

    def test_all_events_unknown_type_produces_empty_marker(self, make_client):
        _, today, _, _ = _window()
        cosmos = FakeCosmos(
            symbols=[_sym("AAPL")],
            calendar_events=[
                _cal("AAPL", "split", today.isoformat()),
                _cal("AAPL", "dividend", today.isoformat()),  # not earnings/ex_dividend
            ],
        )
        client = make_client(cosmos)
        _post(client, include_calendar_events=True)
        prompt = _system_prompt()
        assert "No earnings or ex-dividend events found" in prompt

    def test_empty_calendar_does_not_suppress_existing_symbol_context(self, make_client):
        """When calendar returns nothing, the activity/symbol sections must still appear."""
        cosmos = FakeCosmos(symbols=[_sym("AAPL")], calendar_events=[])
        client = make_client(cosmos)
        _post(client, include_calendar_events=True)
        prompt = _system_prompt()
        # Activity/symbol sections are built before the calendar block.
        assert "buy_tracker" in prompt.lower() or "Following" in prompt


# ===========================================================================
# 9. Calendar retrieval failure — request succeeds; activities preserved
# ===========================================================================

class TestCalendarFailureDegradation:

    def test_calendar_exception_returns_200(self, make_client):
        cosmos = FakeCosmos(
            symbols=[_sym("AAPL")],
            calendar_raises=True,
        )
        client = make_client(cosmos)
        resp = _post(client, include_calendar_events=True)
        assert resp.status_code == 200
        data = resp.json()
        assert "reply" in data
        assert data["reply"] == "MOCK REPLY"

    def test_calendar_exception_appends_unavailable_marker(self, make_client):
        cosmos = FakeCosmos(
            symbols=[_sym("AAPL")],
            calendar_raises=True,
        )
        client = make_client(cosmos)
        _post(client, include_calendar_events=True)
        prompt = _system_prompt()
        assert "(Calendar data unavailable)" in prompt

    def test_calendar_exception_does_not_discard_activities_context(self, make_client):
        """The outer portfolio context (agents, symbols) must survive a calendar failure."""
        cosmos = FakeCosmos(
            symbols=[_sym("AAPL")],
            calendar_raises=True,
        )
        client = make_client(cosmos)
        _post(client, include_calendar_events=True)
        prompt = _system_prompt()
        # Agent header line is always added before calendar retrieval.
        assert "Buy Tracker" in prompt or "AAPL" in prompt

    def test_calendar_exception_still_adds_calendar_section_header(self, make_client):
        """Even on failure, the UPCOMING CALENDAR header must appear (with the marker)."""
        cosmos = FakeCosmos(
            symbols=[_sym("AAPL")],
            calendar_raises=True,
        )
        client = make_client(cosmos)
        _post(client, include_calendar_events=True)
        prompt = _system_prompt()
        assert "=== UPCOMING CALENDAR" in prompt

    def test_calendar_disabled_and_cosmos_error_does_not_show_unavailable_marker(
        self, make_client
    ):
        """When flag is false, a calendar error is never triggered and the marker absent."""
        cosmos = FakeCosmos(
            symbols=[_sym("AAPL")],
            calendar_raises=True,
        )
        client = make_client(cosmos)
        _post(client, include_calendar_events=False)
        prompt = _system_prompt()
        assert "(Calendar data unavailable)" not in prompt


# ===========================================================================
# 10. Frontend contract: default-off; sent only in portfolio mode payload
# ===========================================================================

class TestFrontendCalendarContract:
    """
    Static source analysis of GlobalChatView.tsx.
    No runtime test runner — asserts invariants on the source text directly,
    following the pattern established for frontend contract coverage in this project.
    """

    @pytest.fixture(scope="class")
    def tsx_source(self):
        path = Path(__file__).parent.parent.parent / (
            "frontend/src/components/GlobalChatView.tsx"
        )
        return path.read_text(encoding="utf-8")

    def test_include_calendar_events_default_is_false(self, tsx_source):
        """useState(false) initialises includeCalendarEvents to OFF."""
        assert "includeCalendarEvents, setIncludeCalendarEvents] = useState(false)" in tsx_source

    def test_include_calendar_events_sent_only_in_portfolio_else_branch(self, tsx_source):
        """
        The send() function sends include_calendar_events inside the else (portfolio) branch,
        NOT inside the if (quick-analysis) branch.
        """
        # Find the send() function block.
        send_idx = tsx_source.find("async function send(")
        assert send_idx != -1, "send() function not found in GlobalChatView.tsx"
        send_block = tsx_source[send_idx: send_idx + 2000]

        # The if branch is for quick-analysis; the else is for portfolio.
        if_idx = send_block.find('if (mode === "quick-analysis")')
        else_idx = send_block.find("} else {", if_idx)
        cal_idx = send_block.find("include_calendar_events")

        assert cal_idx != -1, "include_calendar_events not found in send()"
        # calendar key must appear after the else { (inside portfolio branch)
        assert cal_idx > else_idx, (
            "include_calendar_events must be inside the portfolio else-branch of send(), "
            "not in the quick-analysis if-branch"
        )

    def test_include_calendar_events_not_in_quick_analysis_branch(self, tsx_source):
        send_idx = tsx_source.find("async function send(")
        send_block = tsx_source[send_idx: send_idx + 2000]

        if_idx = send_block.find('if (mode === "quick-analysis")')
        else_idx = send_block.find("} else {", if_idx)
        # The quick-analysis branch is between if_idx and else_idx.
        qa_branch = send_block[if_idx:else_idx]
        assert "include_calendar_events" not in qa_branch

    def test_toggle_rendered_only_in_portfolio_config_phase(self, tsx_source):
        """The toggle UI element is inside the portfolio-config phase block."""
        # Find the portfolio-config phase render block.
        portfolio_cfg_idx = tsx_source.find('phase === "portfolio-config"')
        assert portfolio_cfg_idx != -1
        # The calendar toggle label text should appear in that section.
        toggle_label_idx = tsx_source.find(
            "Include earnings", portfolio_cfg_idx
        )
        assert toggle_label_idx != -1, (
            "Calendar toggle label not found inside portfolio-config render block"
        )

    def test_include_calendar_events_state_is_independent_of_include_symbol_data(
        self, tsx_source
    ):
        """Both toggles are separate useState declarations with their own default."""
        assert "includeSymbolData, setIncludeSymbolData] = useState(false)" in tsx_source
        assert "includeCalendarEvents, setIncludeCalendarEvents] = useState(false)" in tsx_source
        # They must be separate declarations (not sharing state).
        sym_idx = tsx_source.find("includeSymbolData, setIncludeSymbolData")
        cal_idx = tsx_source.find("includeCalendarEvents, setIncludeCalendarEvents")
        assert sym_idx != cal_idx
