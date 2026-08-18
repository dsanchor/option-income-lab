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

import json

import pytest

from src.tv_options_chain_fetcher import _parse_tv_to_yfinance_format

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
