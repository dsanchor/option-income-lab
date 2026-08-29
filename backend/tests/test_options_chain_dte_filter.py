"""Test suite for src/options_chain_filters.py::filter_options_chain_by_dte
-- the sole row-inclusion filter for the Best Options evaluator (design
.squad/decisions/inbox/danny-best-options-design.md section 4.1/7, finding F1).

Hermetic: pure in-memory dict fixtures, no network calls.
"""

import copy
from datetime import date, timedelta

from src.options_chain_filters import filter_options_chain_by_dte

TODAY = date(2026, 8, 29)


def _contract(strike=100.0):
    return {"strike": strike, "bid": 1.0, "ask": 1.2, "delta": 0.3}


def _bucket(*strikes):
    return {f"{s:.1f}": _contract(s) for s in strikes}


def _exp_key(days_from_today: int) -> str:
    return (TODAY + timedelta(days=days_from_today)).strftime("%Y%m%d")


class TestWindowInclusion:
    def test_keeps_expirations_strictly_inside_the_window(self):
        chain = {
            "symbol": "TEST",
            "underlying_price": 100.0,
            "calls": {_exp_key(10): _bucket(100.0), _exp_key(30): _bucket(105.0)},
            "puts": {},
        }
        result = filter_options_chain_by_dte(chain, min_dte=0, max_dte=49, today_et=TODAY)
        assert set(result["calls"].keys()) == {_exp_key(10), _exp_key(30)}

    def test_drops_expirations_outside_the_window(self):
        chain = {
            "calls": {_exp_key(60): _bucket(100.0)},
            "puts": {_exp_key(-5): _bucket(90.0)},
        }
        result = filter_options_chain_by_dte(chain, min_dte=0, max_dte=49, today_et=TODAY)
        assert result["calls"] == {}
        assert result["puts"] == {}

    def test_boundaries_are_inclusive(self):
        chain = {
            "calls": {
                _exp_key(0): _bucket(100.0),
                _exp_key(49): _bucket(100.0),
                _exp_key(-1): _bucket(100.0),
                _exp_key(50): _bucket(100.0),
            },
            "puts": {},
        }
        result = filter_options_chain_by_dte(chain, min_dte=0, max_dte=49, today_et=TODAY)
        assert set(result["calls"].keys()) == {_exp_key(0), _exp_key(49)}

    def test_calls_and_puts_are_filtered_independently(self):
        chain = {
            "calls": {_exp_key(10): _bucket(100.0), _exp_key(60): _bucket(100.0)},
            "puts": {_exp_key(20): _bucket(90.0)},
        }
        result = filter_options_chain_by_dte(chain, min_dte=0, max_dte=49, today_et=TODAY)
        assert set(result["calls"].keys()) == {_exp_key(10)}
        assert set(result["puts"].keys()) == {_exp_key(20)}


class TestWholeBucketSemantics:
    def test_never_drops_an_individual_contract_within_a_kept_expiration(self):
        many_strikes = _bucket(80.0, 90.0, 100.0, 110.0, 120.0)
        chain = {"calls": {_exp_key(10): many_strikes}, "puts": {}}
        result = filter_options_chain_by_dte(chain, min_dte=0, max_dte=49, today_et=TODAY)
        assert result["calls"][_exp_key(10)] == many_strikes

    def test_malformed_expiration_keys_are_dropped(self):
        chain = {
            "calls": {
                "not-a-date": _bucket(100.0),
                "2026": _bucket(100.0),
                _exp_key(10): _bucket(100.0),
            },
            "puts": {},
        }
        result = filter_options_chain_by_dte(chain, min_dte=0, max_dte=49, today_et=TODAY)
        assert set(result["calls"].keys()) == {_exp_key(10)}


class TestPurityAndPassthrough:
    def test_preserves_other_top_level_chain_fields(self):
        chain = {
            "symbol": "AAPL",
            "timestamp": "2026-08-29T12:00:00Z",
            "underlying_price": 231.5,
            "calls": {_exp_key(10): _bucket(100.0)},
            "puts": {},
        }
        result = filter_options_chain_by_dte(chain, min_dte=0, max_dte=49, today_et=TODAY)
        assert result["symbol"] == "AAPL"
        assert result["timestamp"] == "2026-08-29T12:00:00Z"
        assert result["underlying_price"] == 231.5

    def test_does_not_mutate_the_input_chain(self):
        chain = {"calls": {_exp_key(10): _bucket(100.0), _exp_key(60): _bucket(100.0)}, "puts": {}}
        original = copy.deepcopy(chain)
        filter_options_chain_by_dte(chain, min_dte=0, max_dte=49, today_et=TODAY)
        assert chain == original

    def test_missing_calls_or_puts_key_defaults_to_empty(self):
        result = filter_options_chain_by_dte({}, min_dte=0, max_dte=49, today_et=TODAY)
        assert result["calls"] == {}
        assert result["puts"] == {}
