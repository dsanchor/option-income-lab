"""Tests for the TradingView options-chain normalizer
(src/tv_options_chain_fetcher.py::_parse_tv_to_yfinance_format).

Covers:
  - Rule S1: a field TradingView cannot observe is OMITTED, never
    fabricated as a destructive 0 / 0.0 / False / "" placeholder (the
    direct fix for G2 — those fabricated zeros used to clobber valid
    yfinance data during the source merge).
  - Rule S3: an expiration value that does not resolve to a real YYYYMMDD
    calendar date is rejected at ingestion, never stored as a junk
    fallback key (the direct fix for G5).

Hermetic: calls `_parse_tv_to_yfinance_format` directly with synthetic
scanner-API-shaped payloads; no Playwright/browser involved.
"""

import asyncio
import json
import sys
import types

import pytest

from src.tv_options_chain_fetcher import (
    _parse_tv_to_yfinance_format,
    fetch_tv_options_chain,
)

_FIELDS = ["ask", "bid", "delta", "expiration", "gamma", "iv", "option-type",
           "rho", "strike", "theta", "vega"]


def _raw_item(symbol, ask, bid, expiration, strike, option_type="call",
              delta=0.4, gamma=0.02, iv=0.30, rho=0.01, theta=-0.05, vega=0.1):
    return {
        "s": symbol,
        "f": [ask, bid, delta, expiration, gamma, iv, option_type, rho, strike, theta, vega],
    }


def _parse(items):
    body = {"fields": _FIELDS, "symbols": items}
    return _parse_tv_to_yfinance_format([{"body": json.dumps(body)}], "TEST")


# ===========================================================================
# Rule S1 — no fabricated placeholders
# ===========================================================================

class TestRuleS1NoFabricatedPlaceholders:
    def test_missing_bid_is_absent_not_zero(self):
        """A field TradingView genuinely didn't supply (None in the raw
        payload) must be OMITTED from the contract, not defaulted to 0.0 —
        conflating "no data" with "real zero bid" breaks the trust gate."""
        result = _parse([_raw_item("NASDAQ:AAPL", ask=4.25, bid=None,
                                    expiration=20260601, strike=185.0)])
        contract = result["calls"]["20260601"]["185.0"]
        assert "bid" not in contract
        assert contract["ask"] == 4.25

    def test_genuine_zero_bid_is_preserved_distinctly(self):
        """A real, observed zero bid (not merely absent) IS preserved —
        Rule S1 is about not fabricating placeholders, not about hiding
        real zeros."""
        result = _parse([_raw_item("NASDAQ:AAPL", ask=4.25, bid=0.0,
                                    expiration=20260601, strike=185.0)])
        contract = result["calls"]["20260601"]["185.0"]
        assert contract["bid"] == 0.0

    def test_missing_ask_and_iv_are_absent_not_zero(self):
        result = _parse([_raw_item("NASDAQ:AAPL", ask=None, bid=1.0,
                                    expiration=20260601, strike=185.0)])
        contract = result["calls"]["20260601"]["185.0"]
        assert "ask" not in contract
        assert contract["bid"] == 1.0

    def test_volume_open_interest_last_price_last_trade_date_in_the_money_never_present(self):
        """The TradingView scanner never supplies these fields — the
        normalizer must never fabricate 0 / 0.0 / None-as-placeholder /
        False for any of them; they must be entirely absent so the merger
        treats it as "no opinion", not "provider observed zero"."""
        result = _parse([_raw_item("NASDAQ:AAPL", ask=4.25, bid=1.0,
                                    expiration=20260601, strike=185.0)])
        contract = result["calls"]["20260601"]["185.0"]
        for field in ("volume", "openInterest", "lastPrice", "lastTradeDate", "inTheMoney"):
            assert field not in contract

    def test_empty_symbol_is_absent_not_empty_string(self):
        result = _parse([_raw_item("", ask=4.25, bid=1.0,
                                    expiration=20260601, strike=185.0)])
        contract = result["calls"]["20260601"]["185.0"]
        assert "contractSymbol" not in contract

    def test_nonempty_symbol_is_preserved(self):
        result = _parse([_raw_item("NASDAQ:AAPL", ask=4.25, bid=1.0,
                                    expiration=20260601, strike=185.0)])
        contract = result["calls"]["20260601"]["185.0"]
        assert contract["contractSymbol"] == "NASDAQ:AAPL"


# ===========================================================================
# Rule S3 — unparseable expirations rejected at ingestion
# ===========================================================================

class TestRuleS3RejectUnparseableExpiration:
    def test_non_numeric_expiration_is_rejected(self):
        """G5 regression: the old fallback `expiration = str(raw_exp)`
        stored an un-mergeable junk key. It must now be dropped entirely."""
        result = _parse([_raw_item("NASDAQ:AAPL", ask=4.25, bid=1.0,
                                    expiration="2026-08-21", strike=185.0)])
        assert result["calls"] == {}

    def test_small_out_of_range_numeric_expiration_is_rejected(self):
        # Neither a plausible unix timestamp (>1e9) nor a YYYYMMDD number
        # (>19000000) -- must be rejected, not silently stringified.
        result = _parse([_raw_item("NASDAQ:AAPL", ask=4.25, bid=1.0,
                                    expiration=123, strike=185.0)])
        assert result["calls"] == {}

    def test_valid_unix_timestamp_expiration_is_accepted(self):
        result = _parse([_raw_item("NASDAQ:AAPL", ask=4.25, bid=1.0,
                                    expiration=1798761600, strike=185.0)])
        assert "20270101" in result["calls"]

    def test_valid_yyyymmdd_numeric_expiration_is_accepted(self):
        result = _parse([_raw_item("NASDAQ:AAPL", ask=4.25, bid=1.0,
                                    expiration=20260601, strike=185.0)])
        assert "20260601" in result["calls"]

    def test_no_junk_key_ever_stored_alongside_valid_contracts(self):
        result = _parse([
            _raw_item("NASDAQ:AAPL", ask=4.25, bid=1.0, expiration=20260601, strike=185.0),
            _raw_item("NASDAQ:AAPL", ask=4.30, bid=1.1, expiration="garbage", strike=190.0),
        ])
        assert list(result["calls"].keys()) == ["20260601"]

    @pytest.mark.parametrize("bad_numeric_exp", [
        20261301,  # month 13 -- numerically "looks like" YYYYMMDD but isn't a real date
        20260230,  # Feb 30 never exists
        20260231,  # Feb 31 never exists
        20260132,  # day 32
        20260100,  # day 00
        20250229,  # Feb 29 in a non-leap year
    ])
    def test_calendar_invalid_numeric_yyyymmdd_expiration_rejected(self, bad_numeric_exp):
        """Basher review regression: a raw numeric expiration that is
        `> 19000000` (so it "looks like" a YYYYMMDD integer) but does not
        resolve to a real calendar date must still be rejected here, not
        merely relying on `options_chain_merge`'s downstream check -- the
        fetcher is the primary Rule S3 ingestion point."""
        result = _parse([_raw_item("NASDAQ:AAPL", ask=4.25, bid=1.0,
                                    expiration=bad_numeric_exp, strike=185.0)])
        assert result["calls"] == {}

    def test_calendar_valid_leap_year_numeric_yyyymmdd_expiration_accepted(self):
        result = _parse([_raw_item("NASDAQ:AAPL", ask=4.25, bid=1.0,
                                    expiration=20240229, strike=185.0)])
        assert "20240229" in result["calls"]


class _FakeResource:
    def __init__(self, name, events, *, close_error=None, close_action=None):
        self.name = name
        self.events = events
        self.close_error = close_error
        self.close_action = close_action
        self.close_calls = 0

    async def close(self):
        self.close_calls += 1
        self.events.append(f"{self.name}.close")
        if self.close_action is not None:
            await self.close_action()
        if self.close_error is not None:
            raise self.close_error


class _FakePage(_FakeResource):
    def __init__(
            self, events, *, goto=None, response=None, close_error=None,
            close_action=None):
        super().__init__(
            "page",
            events,
            close_error=close_error,
            close_action=close_action,
        )
        self._goto = goto
        self._response = response
        self._response_handler = None

    def on(self, event, handler):
        if event == "response":
            self._response_handler = handler

    async def goto(self, *_args, **_kwargs):
        if self._goto is not None:
            return await self._goto()
        if self._response is not None and self._response_handler is not None:
            await self._response_handler(self._response)
        return None

    def locator(self, _selector):
        locator = types.SimpleNamespace()
        locator.first = locator

        async def _not_visible(**_kwargs):
            return False

        locator.is_visible = _not_visible
        return locator

    async def wait_for_timeout(self, _timeout):
        return None


class _FakeContext(_FakeResource):
    def __init__(self, events, page, *, close_error=None):
        super().__init__("context", events, close_error=close_error)
        self.page = page

    async def add_init_script(self, _script):
        return None

    async def new_page(self):
        return self.page


class _FakeBrowser(_FakeResource):
    def __init__(self, events, context, *, close_error=None):
        super().__init__("browser", events, close_error=close_error)
        self.context = context

    async def new_context(self, **_kwargs):
        return self.context


class _FakePlaywright:
    def __init__(self, events, browser, *, launch_error=None, stop_error=None):
        self.events = events
        self.browser = browser
        self.launch_error = launch_error
        self.stop_error = stop_error
        self.stop_calls = 0
        self.chromium = types.SimpleNamespace(launch=self._launch)

    async def _launch(self, **_kwargs):
        if self.launch_error is not None:
            raise self.launch_error
        return self.browser

    async def stop(self):
        self.stop_calls += 1
        self.events.append("pw.stop")
        if self.stop_error is not None:
            raise self.stop_error


class _FakeResponse:
    ok = True
    url = (
        "https://scanner.tradingview.com/options/"
        "scan2?label-product=symbols-options"
    )

    async def text(self):
        return json.dumps({
            "totalCount": 2,
            "fields": _FIELDS,
            "symbols": [
                _raw_item(
                    "NASDAQ:TEST",
                    ask=2.0,
                    bid=1.0,
                    expiration=20270101,
                    strike=100.0,
                ),
                _raw_item(
                    "NASDAQ:TEST",
                    ask=2.5,
                    bid=1.5,
                    expiration=20270101,
                    strike=105.0,
                ),
            ],
        })


def _install_fake_playwright(monkeypatch, pw):
    starter = types.SimpleNamespace(start=lambda: _return(pw))
    async_api = types.ModuleType("playwright.async_api")
    async_api.async_playwright = lambda: starter
    package = types.ModuleType("playwright")
    package.async_api = async_api
    monkeypatch.setitem(sys.modules, "playwright", package)
    monkeypatch.setitem(sys.modules, "playwright.async_api", async_api)


async def _return(value):
    return value


def _playwright_stack(events, *, goto=None, response=None, page_error=None,
                      page_close=None,
                      context_error=None, browser_error=None, launch_error=None,
                      stop_error=None):
    page = _FakePage(
        events,
        goto=goto,
        response=response,
        close_error=page_error,
        close_action=page_close,
    )
    context = _FakeContext(events, page, close_error=context_error)
    browser = _FakeBrowser(events, context, close_error=browser_error)
    pw = _FakePlaywright(
        events,
        browser,
        launch_error=launch_error,
        stop_error=stop_error,
    )
    return pw, browser, context, page


class TestPlaywrightLifecycle:
    def test_success_stops_driver_once_after_inner_resources(self, monkeypatch):
        events = []
        pw, browser, context, page = _playwright_stack(events)
        _install_fake_playwright(monkeypatch, pw)

        result = asyncio.run(fetch_tv_options_chain("TEST"))

        assert result["calls"] == {}
        assert events == ["page.close", "context.close", "browser.close", "pw.stop"]
        assert page.close_calls == context.close_calls == browser.close_calls == 1
        assert pw.stop_calls == 1

    def test_intermediate_cleanup_failures_do_not_skip_outer_cleanup(
            self, monkeypatch, caplog):
        events = []
        pw, _browser, _context, _page = _playwright_stack(
            events,
            page_error=RuntimeError("page cleanup failed"),
            context_error=RuntimeError("context cleanup failed"),
            browser_error=RuntimeError("browser cleanup failed"),
        )
        _install_fake_playwright(monkeypatch, pw)

        result = asyncio.run(fetch_tv_options_chain("TEST"))

        assert result["calls"] == {}
        assert events == ["page.close", "context.close", "browser.close", "pw.stop"]
        assert pw.stop_calls == 1
        assert "page cleanup failed" in caplog.text
        assert "context cleanup failed" in caplog.text
        assert "browser cleanup failed" in caplog.text

    def test_partial_initialization_still_stops_started_driver(
            self, monkeypatch, caplog):
        events = []
        pw, browser, context, page = _playwright_stack(
            events,
            launch_error=RuntimeError("launch failed"),
        )
        _install_fake_playwright(monkeypatch, pw)

        result = asyncio.run(fetch_tv_options_chain("TEST"))

        assert result["calls"] == {}
        assert events == ["pw.stop"]
        assert page.close_calls == context.close_calls == browser.close_calls == 0
        assert pw.stop_calls == 1
        assert "launch failed" in caplog.text

    def test_cleanup_cancelled_error_does_not_mask_fetch_failure(
            self, monkeypatch, caplog):
        events = []

        async def _fail_navigation():
            raise ValueError("navigation failed")

        pw, _browser, _context, _page = _playwright_stack(
            events,
            goto=_fail_navigation,
            stop_error=asyncio.CancelledError("driver cleanup cancelled"),
        )
        _install_fake_playwright(monkeypatch, pw)

        result = asyncio.run(fetch_tv_options_chain("TEST"))

        assert result["calls"] == {}
        assert pw.stop_calls == 1
        assert "navigation failed" in caplog.text
        assert "CancelledError" in caplog.text

    def test_caller_cancellation_during_body_stops_driver_once(
            self, monkeypatch):
        events = []
        navigation_started = asyncio.Event()

        async def _block_navigation():
            navigation_started.set()
            await asyncio.Future()

        pw, browser, context, page = _playwright_stack(
            events,
            goto=_block_navigation,
        )
        _install_fake_playwright(monkeypatch, pw)

        async def _scenario():
            task = asyncio.create_task(fetch_tv_options_chain("TEST"))
            await navigation_started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        asyncio.run(_scenario())

        assert events == ["page.close", "context.close", "browser.close", "pw.stop"]
        assert page.close_calls == context.close_calls == browser.close_calls == 1
        assert pw.stop_calls == 1

    def test_caller_cancellation_during_cleanup_is_preserved(
            self, monkeypatch):
        events = []
        page_close_started = asyncio.Event()
        allow_page_close = asyncio.Event()

        async def _block_page_close():
            page_close_started.set()
            await allow_page_close.wait()

        pw, browser, context, page = _playwright_stack(
            events,
            page_close=_block_page_close,
        )
        _install_fake_playwright(monkeypatch, pw)

        async def _scenario():
            task = asyncio.create_task(fetch_tv_options_chain("TEST"))
            await page_close_started.wait()
            task.cancel()
            allow_page_close.set()
            with pytest.raises(asyncio.CancelledError):
                await task

        asyncio.run(_scenario())

        assert events == ["page.close", "context.close", "browser.close", "pw.stop"]
        assert page.close_calls == context.close_calls == browser.close_calls == 1
        assert pw.stop_calls == 1

    def test_external_cancellation_during_failed_fetch_is_not_swallowed(
            self, monkeypatch, caplog):
        events = []
        page_close_started = asyncio.Event()
        allow_page_close = asyncio.Event()

        async def _fail_navigation():
            raise ValueError("navigation failed")

        async def _block_page_close():
            page_close_started.set()
            await allow_page_close.wait()

        pw, _browser, _context, _page = _playwright_stack(
            events,
            goto=_fail_navigation,
            page_close=_block_page_close,
        )
        _install_fake_playwright(monkeypatch, pw)

        async def _scenario():
            task = asyncio.create_task(fetch_tv_options_chain("TEST"))
            await page_close_started.wait()
            task.cancel()
            allow_page_close.set()
            with pytest.raises(asyncio.CancelledError):
                await task

        asyncio.run(_scenario())

        assert pw.stop_calls == 1
        assert "navigation failed" in caplog.text

    def test_successful_body_cleanup_failure_returns_provider_fallback(
            self, monkeypatch, caplog):
        events = []
        pw, _browser, _context, _page = _playwright_stack(
            events,
            response=_FakeResponse(),
            stop_error=RuntimeError("driver cleanup failed"),
        )
        _install_fake_playwright(monkeypatch, pw)

        result = asyncio.run(fetch_tv_options_chain("TEST"))

        assert result["calls"] == {}
        assert pw.stop_calls == 1
        assert "fetch failed during cleanup" in caplog.text
        assert "driver cleanup failed" in caplog.text
