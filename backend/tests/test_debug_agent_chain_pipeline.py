"""
Regression test for GET /api/debug/agent-chain/{symbol} (web/app.py) — the
"Debug > Agent Chain Pipeline View" endpoint.

Reproduces the exact reported scenario: simulating a CURRENT POSITION MSFT
call, strike 525, expiration 2026-09-04 (17 DTE as of 2026-08-18), ROLL_OUT.

Root cause: the held contract's own computed delta can legitimately fall
outside the standard candidate delta band (e.g. yfinance returns a
degenerate/near-zero implied volatility while the market is closed, which
Black-Scholes turns into a ~0.0 delta for a strike that is not actually deep
OTM). filter_options_chain_by_delta drops such a contract before later
stages ever run. The debug endpoint used to compute the buyback cost from
`position_filtered` (a *delta-filtered* derivative of the chain), so it
never found the held contract's ask — reporting "no chain data" /
"NO EXECUTABLE BUYBACK QUOTE" for a contract that in fact exists with a
valid ask. The fix looks the contract up on the RAW (pre-filter) chain,
mirroring the production agent_runner.py monitor pipeline.

Hermetic: NO network, NO real Cosmos, NO real yfinance/TradingView.
Pattern mirrors test_watchlist_symbols.py / test_activity_chat.py:
TestClient + FakeCosmos + a fake yf_provider injected into app.state.
"""

import json

import pytest
from starlette.testclient import TestClient


class FakeCosmos:
    """Minimal in-memory fake for CosmosDBService — only get_symbol is used
    by the debug endpoint."""

    def __init__(self, docs=None):
        self._docs = dict(docs or {})

    def get_symbol(self, symbol):
        return self._docs.get(symbol.upper())


class FakeProvider:
    """Fake yfinance provider — returns a canned options_chain + technicals
    payload shaped exactly like the reported MSFT case, no network calls."""

    def __init__(self, options_chain: dict, price: float):
        self._options_chain = options_chain
        self._price = price

    async def fetch_all(self, symbol, **kwargs):
        return {
            "options_chain": json.dumps(self._options_chain, default=str),
            "technicals": json.dumps({"price": self._price}),
        }


def _msft_chain():
    """Held $525 call at 2026-09-04 (17 DTE): valid non-zero ask, but a
    ~0.0 delta from a degenerate/closed-market IV — exactly the shape that
    was silently dropped by the delta filter. A healthy later-dated
    candidate is included so ROLL_OUT has something to roll into.
    """
    return {
        "symbol": "MSFT",
        "timestamp": "2026-08-18T06:00:00Z",
        "calls": {
            "20260904": {
                "525.0": {
                    "contractSymbol": "MSFT260904C00525000",
                    "strike": 525.0,
                    "bid": 0.0,
                    "ask": 3.20,
                    "mid": 1.6,
                    "iv": 0.062509,
                    "delta": 0.0,
                    "gamma": 0.0,
                    "theta": -0.0,
                    "vega": 0.0,
                    "rho": 0.0,
                    "volume": 195,
                    "openInterest": 0,
                    "lastPrice": 0.92,
                    "lastTradeDate": "2026-08-17T19:36:44Z",
                    "inTheMoney": False,
                    "expiration": "20260904",
                    "option_type": "call",
                },
            },
            "20261016": {
                "530.0": {
                    "strike": 530.0, "bid": 9.20, "ask": 9.50, "mid": 9.35,
                    "iv": 0.22, "delta": 0.42, "gamma": 0.01, "theta": -0.05,
                    "vega": 0.4, "rho": 0.1, "volume": 500, "openInterest": 1000,
                    "lastPrice": 9.30, "lastTradeDate": "2026-08-17T20:00:00Z",
                    "inTheMoney": False, "expiration": "20261016", "option_type": "call",
                },
            },
        },
        "puts": {},
    }


def _make_client(options_chain, price=480.35, symbol_doc=None):
    from web.app import app

    cosmos = FakeCosmos({"MSFT": symbol_doc or {"symbol": "MSFT", "display_name": "MSFT"}})
    app.state.cosmos = cosmos
    app.state.yf_provider = FakeProvider(options_chain, price)
    return TestClient(app, raise_server_exceptions=False)


class TestAgentChainPipelineViewMsftRollOut:
    def test_current_contract_surfaces_buyback_cost_despite_delta_filter(self):
        client = _make_client(_msft_chain())

        resp = client.get(
            "/api/debug/agent-chain/MSFT",
            params={
                "option_type": "call",
                "strike": 525,
                "expiration": "2026-09-04",
                "roll_type": "ROLL_OUT",
            },
        )

        assert resp.status_code == 200
        data = resp.json()

        # Root cause check: the delta filter really does drop the held
        # contract from stage 1 onward (this is expected/correct — it's
        # for CANDIDATE selection, not for the current-position reference).
        stage1_text = data["pipeline"]["stage_1_delta_filtered"]["text"]
        assert '"20260904"' not in stage1_text

        # The fix: stage 4's candidate table still surfaces the held
        # contract's real buyback cost, sourced from the raw chain.
        assert "stage_4_candidate_table" in data["pipeline"]
        table = data["pipeline"]["stage_4_candidate_table"]["text"]
        assert "Buyback cost (ask): $3.20" in table
        assert "Buyback available: true" in table
        assert "NO EXECUTABLE BUYBACK QUOTE" not in table
        assert "17 DTE" in table

    def test_zero_ask_current_contract_reports_incomplete_not_missing(self):
        """When the ask is genuinely zero (market truly closed), the table
        must still report incomplete data — the fix must not fabricate an
        executable price, only stop losing a *valid* one."""
        chain = _msft_chain()
        chain["calls"]["20260904"]["525.0"]["ask"] = 0.0
        del chain["calls"]["20261016"]  # no other candidates for this check
        client = _make_client(chain)

        resp = client.get(
            "/api/debug/agent-chain/MSFT",
            params={
                "option_type": "call",
                "strike": 525,
                "expiration": "2026-09-04",
                "roll_type": "ROLL_OUT",
            },
        )

        assert resp.status_code == 200
        table = resp.json()["pipeline"]["stage_4_candidate_table"]["text"]
        assert "Buyback cost: N/A" in table
        assert "NO EXECUTABLE BUYBACK QUOTE for ROLL_OUT" in table
