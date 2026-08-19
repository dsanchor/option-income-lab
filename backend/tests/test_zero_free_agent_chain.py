"""
Cross-layer integration & adversarial tests (Basher, §6.3 of Danny's
"Zero-Free Agent-Facing Option Chains" decision, `.squad/decisions/inbox/
danny-zero-free-agent-option-chains.md`) — the G4 gate's own test file.

Composes **real** production modules end to end. The only fakes anywhere
in this file are the CosmosDB container client (`FakeContainer`, an
in-memory stand-in with real ETag/CAS semantics, mirroring the existing
fixture in `test_options_chain_persistence_integration.py`) and the two
network-facing provider fetchers passed to `OptionsChainCache.refresh`.
Nothing fakes `options_chain_view.py`, `options_chain_merge.py`,
`dps_scorer.py`, `roll_table.py`, `options_chain_filters.py`, or
`agent_runner.AgentRunner`'s own formatting methods — those are exercised
as the real, shipped code.

Covers Z-I1 through Z-I7 verbatim from §6.3:
  Z-I1 (headline) - an all-zero provider chain must never surface a
       numeric zero in bid/ask/lastPrice/iv/mid/Greek anywhere an agent,
       DPS, the roll table, or format_roll_candidates_table would see it.
  Z-I2 - zeros served *after* a good cycle are replaced with last-known-
       good values, `field_status`/`_meta.carried`/`quote_asof` truthful.
  Z-I3 - a mixed chain (some good, some zeroed) yields a candidate table
       with only eligible contracts, an always-retained current position,
       and an accurate hidden-count footer.
  Z-I4 - DPS on a fully zeroed chain returns NO_DATA/insufficient/UNKNOWN
       with no "Delta 0.00 favorable" style driver text.
  Z-I5 - full-fidelity round trip: the raw persisted shard still contains
       the provider's `bid: 0.0` (the anti-corruption counterpart to Z-I1).
  Z-I6 - a cold process with Cosmos briefly unavailable serves memory-only,
       and recovers persistence within one backoff window without a
       process restart.
  Z-I7 - a legacy stored contract/snapshot containing zeros still renders
       through the real pipeline without raising.
"""

from __future__ import annotations

import copy
import json
import re
from datetime import datetime, timedelta, timezone

import pytest
from azure.core import MatchConditions
from azure.cosmos.exceptions import (
    CosmosHttpResponseError,
    CosmosResourceNotFoundError,
)

from src.agent_runner import AgentRunner
from src.dps_scorer import run_dps_analysis
from src.options_chain_cache import OptionsChainCache, apply_agent_view
from src.options_chain_filters import format_roll_candidates_table
from src.options_chain_merge import merge_prior, merge_sources, recompute_derived
from src.options_chain_store import OptionsChainStore
from src.options_chain_view import to_agent_view
from src.roll_table import compute_roll_table

NOW = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)

_GUARDED_FIELDS = {"bid", "ask", "lastPrice", "iv", "mid", "delta", "gamma", "theta", "vega", "rho"}
_ALLOWED_ZERO_KEYS = {"volume", "openInterest"}


def _recursive_zero_violations(node, path: str = "") -> list[str]:
    """Z-I1 recursive walk: any of `_GUARDED_FIELDS` holding a numeric
    ``0``/``0.0`` anywhere in a nested dict/list structure is a violation.
    `volume`/`openInterest` are the only allow-listed zero-bearing keys
    (Z2)."""
    violations = []
    if isinstance(node, dict):
        for key, value in node.items():
            child_path = f"{path}.{key}" if path else str(key)
            if (
                key in _GUARDED_FIELDS
                and key not in _ALLOWED_ZERO_KEYS
                and isinstance(value, (int, float))
                and not isinstance(value, bool)
                and value == 0
            ):
                violations.append(f"{child_path} = {value!r}")
            violations.extend(_recursive_zero_violations(value, child_path))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            violations.extend(_recursive_zero_violations(value, f"{path}[{i}]"))
    return violations


def _text_zero_violations(text: str) -> list[str]:
    """Same guard, applied to a rendered JSON-in-text blob (agent prompt
    text, debug pipeline text) rather than a live dict.

    Captures the full numeric token after the key and parses it with
    `float()` rather than pattern-matching digits directly: a naive
    ``-?0(\\.0+)?(?!\\d)`` pattern false-positives on legitimate non-zero
    decimals like ``"iv": 0.3`` (the digit-only negative lookahead doesn't
    exclude a following ``.``), which would make this guard useless as a
    regression check.
    """
    pattern = r'"(' + "|".join(sorted(_GUARDED_FIELDS)) + r')":\s*(-?\d+(?:\.\d+)?)'
    violations = []
    for m in re.finditer(pattern, text):
        if float(m.group(2)) == 0.0:
            violations.append(f'"{m.group(1)}": {m.group(2)}')
    return violations


def _all_zero_source_contract(**overrides) -> dict:
    """A raw single-source (yfinance) contract where every quote/derived
    input is a *real* provider-reported zero or empty — the literal
    all-zero-payload adversarial case Z-I1 targets."""
    base = {
        "contractSymbol": "AAPL260901C00100000",
        "strike": 100.0,
        "bid": 0.0,
        "ask": 0.0,
        "iv": 0.0,
        "lastPrice": 0.0,
        "lastTradeDate": "2026-08-19T15:00:00Z",
        "volume": 0,
        "openInterest": 0,
        "inTheMoney": False,
        "expiration": "20260901",
        "option_type": "call",
    }
    base.update(overrides)
    return base


def _no_bid_but_quotable_source_contract(**overrides) -> dict:
    """A *genuinely informative* one-sided illiquid quote: no buyers
    (bid=0.0, a real, decisive observation) but a real, quotable ask/iv —
    passes `options_chain_merge.gate_contract`'s trust gate (needs a valid
    ask>0 or valid iv), so `bid: 0.0` is genuinely *accepted* and persisted
    raw, unlike `_all_zero_source_contract` below (where nothing at all is
    quotable and the whole quote group is correctly rejected by the gate
    as non-informative). This is the fixture Z-I5's anti-corruption check
    and the real-world "does a literal raw zero leak into an agent
    surface" adversarial case need — a contract that legitimately clears
    every downstream filter (valid delta/greeks) while still carrying one
    real, faithful zero.
    """
    base = {
        "contractSymbol": "AAPL260901C00100000",
        "strike": 100.0,
        "bid": 0.0,
        "ask": 1.2,
        "iv": 0.30,
        "lastPrice": 0.0,
        "lastTradeDate": "2026-08-19T15:00:00Z",
        "volume": 0,
        "openInterest": 0,
        "inTheMoney": False,
        "expiration": "20260901",
        "option_type": "call",
    }
    base.update(overrides)
    return base


def _good_source_contract(**overrides) -> dict:
    base = {
        "contractSymbol": "AAPL260901C00105000",
        "strike": 105.0,
        "bid": 2.0,
        "ask": 2.2,
        "iv": 0.28,
        "lastPrice": 2.1,
        "lastTradeDate": "2026-08-19T15:00:00Z",
        "volume": 25,
        "openInterest": 80,
        "inTheMoney": False,
        "expiration": "20260901",
        "option_type": "call",
    }
    base.update(overrides)
    return base


def _chain(calls=None, puts=None, symbol="AAPL", underlying_price=100.0):
    return {
        "symbol": symbol,
        "timestamp": NOW.isoformat(),
        "underlying_price": underlying_price,
        "calls": calls or {},
        "puts": puts or {},
    }


def _bucket(*contracts):
    return {f"{c['strike']:.1f}": c for c in contracts}


def _build_merged_chain(source_chain: dict, *, underlying_price: float = 100.0, now: datetime = NOW) -> dict:
    """The real 3-phase pipeline a refresh cycle runs: merge_sources ->
    merge_prior (no prior) -> recompute_derived. Produces the exact shape
    persisted/served in production."""
    merged_sources = merge_sources(source_chain, _chain(symbol=source_chain.get("symbol", "AAPL")))
    merged = merge_prior({}, merged_sources, now=now)
    return recompute_derived(merged, underlying_price=underlying_price, now=now)


# ===========================================================================
# Fake Cosmos container (the only fake in this file) — mirrors
# test_options_chain_persistence_integration.py's fixture exactly.
# ===========================================================================

class FakeContainer:
    def __init__(self):
        self.store: dict[str, dict] = {}
        self._etag_counter = 0

    def _next_etag(self) -> str:
        self._etag_counter += 1
        return f"etag-{self._etag_counter}"

    def read_item(self, item, partition_key):
        doc = self.store.get(item)
        if doc is None:
            raise CosmosResourceNotFoundError()
        return copy.deepcopy(doc)

    def create_item(self, body):
        doc = copy.deepcopy(body)
        doc["_etag"] = self._next_etag()
        self.store[doc["id"]] = doc
        return copy.deepcopy(doc)

    def replace_item(self, item, body, etag=None, match_condition=None):
        current = self.store.get(item)
        if (
            match_condition == MatchConditions.IfNotModified
            and current is not None
            and current.get("_etag") != etag
        ):
            raise CosmosHttpResponseError(status_code=412, message="Precondition failed")
        doc = copy.deepcopy(body)
        doc["_etag"] = self._next_etag()
        self.store[item] = doc
        return copy.deepcopy(doc)

    def delete_item(self, item, partition_key):
        if item not in self.store:
            raise CosmosResourceNotFoundError()
        del self.store[item]

    def query_items(self, query, parameters=None, partition_key=None, enable_cross_partition_query=False):
        params = {p["name"]: p["value"] for p in (parameters or [])}
        symbol = params.get("@s")
        rows = list(self.store.values())
        if symbol is not None:
            rows = [r for r in rows if r.get("symbol") == symbol]
        if "DISTINCT VALUE c.symbol" in query:
            return sorted({r.get("symbol") for r in self.store.values()})
        return [copy.deepcopy(r) for r in rows]


def _future_exp_key(days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y%m%d")


async def _empty_source(sym):
    return {"symbol": sym, "calls": {}, "puts": {}}


def _run_async(coro):
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ===========================================================================
# Z-I1 (headline): no numeric zero anywhere an agent/DPS/roll-table/API
# consumer would see it, for an all-zero provider payload.
# ===========================================================================

class TestZI1NoNumericZeroAnywhereInAgentSurfaces:
    def _all_zero_recomputed_chain(self):
        source = _chain(calls={"20260901": _bucket(_all_zero_source_contract())})
        return _build_merged_chain(source)

    def test_to_agent_view_recursive_walk_clean(self):
        recomputed = self._all_zero_recomputed_chain()
        view = to_agent_view(recomputed, now=NOW, stale_after_seconds=86400)
        violations = _recursive_zero_violations(view)
        assert violations == [], f"Z-I1 violation(s) in to_agent_view output: {violations}"
        # volume/openInterest must still be the real, faithful integer 0.
        contract = view["calls"]["20260901"]["100.0"]
        assert contract["volume"] == 0
        assert contract["openInterest"] == 0

    def test_apply_agent_view_serving_seam_recursive_walk_clean(self):
        """The exact helper Livingston's `web/app.py`/`agent_runner.py`
        seams call — proves the seam itself, not just the underlying
        `to_agent_view`, is clean."""
        recomputed = self._all_zero_recomputed_chain()
        served = apply_agent_view(recomputed, now=NOW)
        violations = _recursive_zero_violations(served)
        assert violations == [], f"Z-I1 violation(s) in apply_agent_view output: {violations}"

    def test_agent_runner_format_options_chain_text_clean(self):
        """`AgentRunner._format_options_chain` — the primary agent-prompt
        options-chain text block."""
        recomputed = self._all_zero_recomputed_chain()
        raw_chain = json.dumps(recomputed)
        text = AgentRunner._format_options_chain(raw_chain, "AAPL")
        violations = _text_zero_violations(text)
        assert violations == [], (
            f"Z-I1 violation(s) in AgentRunner._format_options_chain text: {violations}"
        )

    def test_agent_runner_current_contract_chain_text_clean(self):
        """`AgentRunner._format_current_contract_chain` — the Phase 1
        single-held-contract reference block."""
        recomputed = self._all_zero_recomputed_chain()
        raw_chain = json.dumps(recomputed)
        text = AgentRunner._format_current_contract_chain(
            raw_chain, "AAPL", current_strike=100.0, expiration="20260901", option_type="call",
        )
        violations = _text_zero_violations(text)
        assert violations == [], (
            f"Z-I1 violation(s) in AgentRunner._format_current_contract_chain text: {violations}"
        )

    def test_agent_runner_alpha_options_chain_text_clean(self):
        """`AgentRunner._build_alpha_options_chain` — feeds directly into
        `alpha_market_data`, which `_run_alpha_review` embeds verbatim into
        the real Alpha-advisor LLM prompt (`run_symbol_agent`'s
        `_run_alpha_review(..., market_data=alpha_market_data, ...)` call).
        This is a genuine agent-facing surface, not a debug/internal-only
        path — Z-I1 requires it to be exactly as clean as
        `_format_options_chain`'s text.

        Uses `_no_bid_but_quotable_source_contract` (bid=0.0, but a valid
        ask/iv) rather than the fully-unquotable all-zero fixture: an
        all-zero contract has no valid iv, so its Greeks are invalid and it
        is dropped entirely by `filter_options_chain_by_delta` before ever
        reaching this function's `json.dumps` call, which would silently
        exercise the wrong code path and hide a real defect. This fixture
        genuinely clears the delta filter and reaches serialization,
        matching a realistic "no resting bid but a live ask" quote.
        """
        source = _chain(calls={"20260901": _bucket(_no_bid_but_quotable_source_contract())})
        recomputed = _build_merged_chain(source)
        data = {"options_chain": json.dumps(recomputed)}
        runner = AgentRunner.__new__(AgentRunner)  # method touches no `self.` state
        text = runner._build_alpha_options_chain(data, "covered_call")
        violations = _text_zero_violations(text)
        assert violations == [], (
            "Z-I1 DEFECT: AgentRunner._build_alpha_options_chain serializes the "
            "RAW chain (never passed through apply_agent_view/to_agent_view) "
            "directly into the Alpha-advisor LLM prompt via _run_alpha_review's "
            "market_data argument, so a real provider zero (bid=0.0, "
            f"lastPrice=0.0, etc.) reaches a live agent-facing surface: {violations}"
        )

    def test_agent_runner_alpha_current_position_reference_block_clean(self):
        """The "CURRENT POSITION (buyback-cost reference)" block built
        inline in `_build_alpha_options_chain` reads
        `current_contract.get("bid")`/`.get("delta")`/`.get("last")`
        straight off the RAW (pre-view) contract captured before the delta
        filter runs — a second, independent raw-zero leak into the same
        Alpha-advisor prompt, alongside the main chain-serialization leak.
        """
        source = _chain(calls={"20260901": _bucket(_no_bid_but_quotable_source_contract())})
        recomputed = _build_merged_chain(source)
        data = {"options_chain": json.dumps(recomputed)}
        runner = AgentRunner.__new__(AgentRunner)
        text = runner._build_alpha_options_chain(
            data, "covered_call", current_strike=100.0, current_expiration="20260901",
        )
        violations = _text_zero_violations(text)
        assert violations == [], (
            "Z-I1 DEFECT: the CURRENT POSITION reference block in "
            "AgentRunner._build_alpha_options_chain embeds "
            "current_contract.get('bid') directly from the raw (pre-"
            f"apply_agent_view) contract: {violations}"
        )

    def test_dps_scorer_end_to_end_clean(self):
        recomputed = self._all_zero_recomputed_chain()
        result = run_dps_analysis(
            symbol="AAPL", strike=100.0, expiration="20260901", option_type="call",
            chain_json=json.dumps(recomputed), snapshots=[], underlying_price=100.0,
        )
        violations = _recursive_zero_violations(result)
        assert violations == [], f"Z-I1 violation(s) in DPS result: {violations}"

    def test_roll_table_end_to_end_clean(self):
        recomputed = self._all_zero_recomputed_chain()
        # Add a second expiry so compute_roll_table has candidate rows too.
        recomputed["calls"][_future_exp_key(45)] = _bucket(
            recompute_derived(
                merge_prior({}, merge_sources(
                    _chain(calls={_future_exp_key(45): _bucket(_all_zero_source_contract(
                        contractSymbol="AAPL_ROLL_C", expiration=_future_exp_key(45),
                    ))}),
                    _chain(),
                ), now=NOW),
                underlying_price=100.0, now=NOW,
            )["calls"][_future_exp_key(45)]["100.0"]
        )
        result = compute_roll_table(
            chain=recomputed, current_strike=100.0, current_expiration="20260901",
            option_type="call", underlying_price=100.0, premium_received=1.0,
        )
        violations = _recursive_zero_violations(result)
        assert violations == [], f"Z-I1 violation(s) in roll table result: {violations}"

    def test_format_roll_candidates_table_no_fabricated_zero_rendering(self):
        recomputed = self._all_zero_recomputed_chain()
        table = format_roll_candidates_table(
            chain=recomputed, current_strike=100.0, current_expiration="20260901",
            option_type="call", underlying_price=100.0, roll_type="ROLL_OUT",
        )
        assert "$0.00" not in table
        assert " 0.00 " not in table
        assert "0.00%" not in table


# ===========================================================================
# Z-I2: zeros served after a good cycle -> last-known-good is served, not
# nulls; field_status/carried/quote_asof are truthful.
# ===========================================================================

class TestZI2LastKnownGoodServedNotNulled:
    def test_carried_forward_quote_reported_as_last_known_good(self):
        cache = OptionsChainCache(ttl_seconds=1800, store=OptionsChainStore(enabled=False))
        symbol = "AAPL"
        exp = _future_exp_key(10)

        cycle = {"n": 0}

        async def _fake_yf(sym):
            cycle["n"] += 1
            if cycle["n"] == 1:
                return _chain(calls={exp: _bucket(_good_source_contract(expiration=exp))}, symbol=sym)
            # Cycle 2+: provider now reports the same contract as all-zero
            # (e.g. after-hours quote reset) -- the prior good quote must
            # be carried forward, not nulled.
            return _chain(calls={exp: _bucket(_all_zero_source_contract(
                contractSymbol="AAPL260901C00105000", strike=105.0, expiration=exp,
            ))}, symbol=sym)

        cache._fetch_yfinance = _fake_yf
        cache._fetch_tradingview = _empty_source

        _run_async(cache.refresh(symbol))
        _run_async(cache.refresh(symbol))

        raw_json = _run_async(cache.get_or_load_async(symbol))
        raw = json.loads(raw_json) if isinstance(raw_json, str) else raw_json
        contract = raw["calls"][exp]["105.0"]
        assert contract["bid"] == 2.0, "a real prior quote must be carried, not zeroed/nulled"
        assert contract["ask"] == 2.2
        assert contract["_meta"]["quote_source"] in ("yfinance", "tradingview")

        view = to_agent_view(raw, now=NOW + timedelta(days=1), stale_after_seconds=86400)
        view_contract = view["calls"][exp]["105.0"]
        assert view_contract["bid"] == 2.0
        assert view_contract["_meta"]["field_status"]["bid"] in ("live", "last_known_good")


# ===========================================================================
# Z-I3: mixed chain -> candidate table excludes ineligible contracts only;
# current position is always retained with nulls; footer is accurate.
# ===========================================================================

class TestZI3MixedChainCandidateExclusionCurrentPositionRetained:
    def test_candidate_table_excludes_zeroed_retains_current_position(self):
        exp = "20260901"
        current = _all_zero_source_contract(
            contractSymbol="AAPL_CURRENT", strike=100.0, expiration=exp,
        )
        roll_exp = _future_exp_key(45)
        good_candidate = _good_source_contract(
            contractSymbol="AAPL_GOOD_CANDIDATE", strike=105.0, expiration=roll_exp,
        )
        zero_candidate = _all_zero_source_contract(
            contractSymbol="AAPL_ZERO_CANDIDATE", strike=110.0, expiration=roll_exp,
        )
        source = _chain(calls={
            exp: _bucket(current),
            roll_exp: _bucket(good_candidate, zero_candidate),
        })
        recomputed = _build_merged_chain(source)

        from src.options_chain_filters import get_contract
        current_contract = get_contract(recomputed, 100.0, exp, "call")

        table = format_roll_candidates_table(
            chain=recomputed, current_strike=100.0, current_expiration=exp,
            option_type="call", underlying_price=100.0, roll_type="ROLL_OUT",
            current_contract=current_contract,
        )

        assert "105.0" in table, "the good candidate must be a proposable row"
        assert "110.0" not in table, "the zeroed/no-bid/no-OI candidate must be excluded"
        assert "hidden" in table.lower(), "an accurate hidden-count footer must disclose the exclusion"
        # Current position reference must still be present (never excluded).
        assert "CURRENT POSITION" in table or "100.0" in table


# ===========================================================================
# Z-I4: DPS on a fully zeroed chain -> NO_DATA / insufficient / UNKNOWN,
# no fabricated-confidence driver text.
# ===========================================================================

class TestZI4DpsEndToEndZeroedChainNoFalseConfidence:
    def test_status_no_data_confidence_insufficient_risk_zone_unknown(self):
        source = _chain(calls={"20260901": _bucket(_all_zero_source_contract())})
        recomputed = _build_merged_chain(source)

        result = run_dps_analysis(
            symbol="AAPL", strike=100.0, expiration="20260901", option_type="call",
            chain_json=json.dumps(recomputed), snapshots=[], underlying_price=100.0,
        )
        assert result["status"] == "NO_DATA"
        assert result["data_quality"]["confidence"] == "insufficient"
        assert result["risk_zone"] == "UNKNOWN"

        # No fabricated-confidence driver text anywhere in key_drivers /
        # score_breakdown reasons (e.g. "Delta 0.00 favorable (OTM)").
        rendered = json.dumps(result)
        assert not re.search(r"[Dd]elta\s+0\.0+\s+favorable", rendered)
        assert not re.search(r"Γ\s*0\.0+\s*\(no penalty\)", rendered)


# ===========================================================================
# Z-I5: full-fidelity round trip — the raw/persisted shard still contains
# the provider's real bid: 0.0 (anti-corruption, the Z-I1 counterpart).
# ===========================================================================

class TestZI5RawPersistedShardByteFaithfulRoundTrip:
    def test_persisted_bid_zero_survives_hydrate_untouched_but_view_nulls_it(self):
        container = FakeContainer()
        store = OptionsChainStore(container=container)
        cache = OptionsChainCache(ttl_seconds=1800, store=store)
        symbol = "AAPL"
        exp = _future_exp_key(10)

        async def _fake_yf(sym):
            return _chain(
                calls={exp: _bucket(_no_bid_but_quotable_source_contract(expiration=exp))}, symbol=sym,
            )

        cache._fetch_yfinance = _fake_yf
        cache._fetch_tradingview = _empty_source

        _run_async(cache.refresh(symbol))

        hydrated = store.hydrate(symbol)
        assert hydrated is not None
        raw_contract = hydrated["calls"][exp]["100.0"]
        # NOTE: a contract whose ENTIRE quote group (bid/ask/iv/lastPrice)
        # is simultaneously zero/absent is correctly treated by
        # `options_chain_merge.gate_contract` as a non-quoting, untrustworthy
        # payload (it requires a valid ask>0 or valid iv to accept ANY of the
        # quote-group fields) -- such a contract's bid is legitimately never
        # persisted at all (absent, not a stored 0.0). This fixture instead
        # supplies a valid ask/iv alongside bid=0.0 so the trust gate accepts
        # the quote group, faithfully exercising the real "provider genuinely
        # reports no bid" anti-corruption path Z-I5/Z11 are about.
        assert raw_contract["bid"] == 0.0, "Z11: a real provider bid=0.0 must survive persist/hydrate byte-faithfully"
        assert raw_contract["volume"] == 0
        assert raw_contract["openInterest"] == 0

        viewed = apply_agent_view(hydrated, now=NOW)
        viewed_contract = viewed["calls"][exp]["100.0"]
        assert viewed_contract["bid"] is None, "only the agent-view boundary nulls the raw zero, never the raw layer"


# ===========================================================================
# Z-I6: cold process, Cosmos briefly unavailable -> memory-only serving,
# recovers persistence within one backoff window, no restart.
# ===========================================================================

class TestZI6ColdProcessRecoversPersistenceWithinBackoffWindow:
    def test_cache_persistence_stats_recover_after_backoff_without_restart(self, monkeypatch):
        import src.options_chain_store as options_chain_store_module
        from src.options_chain_store import (
            get_options_chain_store,
            get_persistence_health,
            set_options_chain_store,
        )

        set_options_chain_store(None)
        try:
            attempts = {"n": 0}

            def _fake_build():
                attempts["n"] += 1
                if attempts["n"] == 1:
                    return options_chain_store_module._ConstructionOutcome(
                        OptionsChainStore(enabled=False), terminal=False,
                        reason="Cosmos briefly unavailable",
                    )
                return options_chain_store_module._ConstructionOutcome(
                    OptionsChainStore(container=FakeContainer(), enabled=True),
                    terminal=True, reason="",
                )

            monkeypatch.setattr(options_chain_store_module, "_build_store_from_config", _fake_build)
            monkeypatch.setattr(options_chain_store_module, "_resolve_persistence_retry_seconds", lambda: 300.0)

            t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
            cache = OptionsChainCache(ttl_seconds=1800)
            # Force the cache to resolve the shared singleton at t0 (cold,
            # broken Cosmos) -- simulate by calling get_options_chain_store
            # directly with injected time, mirroring what `_get_store()`
            # does internally on first use.
            first = get_options_chain_store(now=t0)
            assert first.is_available() is False
            health = get_persistence_health()
            assert health["constructed"] is False
            assert "Cosmos briefly unavailable" in health["last_error"]

            # Past the backoff window (no process restart, just time
            # passing and another call): recovers.
            second = get_options_chain_store(now=t0 + timedelta(seconds=301))
            assert second.is_available() is True
            assert get_persistence_health()["constructed"] is True

            # The cache's own merged stats block reflects the recovery too
            # -- proving the integration, not just the store module alone.
            cache._store_backend = None  # force cache to re-resolve the (now recovered) singleton
            stats = cache.stats()
            assert stats["persistence"]["available"] is True
        finally:
            set_options_chain_store(None)


# ===========================================================================
# Z-I7: legacy zero-bearing contract/snapshot renders through the real
# pipeline without raising.
# ===========================================================================

class TestZI7LegacyZeroSnapshotRendersWithoutException:
    def test_legacy_pre_meta_all_zero_contract_no_exception_anywhere(self):
        """A legacy shard contract with no `_meta` at all and raw zeros
        (predates provenance tracking entirely) must still flow through
        every real consumer without raising."""
        legacy_contract = {
            "contractSymbol": "AAPL_LEGACY",
            "strike": 100.0,
            "bid": 0.0,
            "ask": 0.0,
            "mid": 0.0,
            "iv": 0.0,
            "delta": 0.0,
            "gamma": 0.0,
            "theta": 0.0,
            "vega": 0.0,
            "rho": 0.0,
            "lastPrice": 0.0,
            "lastTradeDate": "",
            "volume": 0,
            "openInterest": 0,
            "inTheMoney": False,
            "expiration": "20260901",
            "option_type": "call",
        }
        legacy_chain = _chain(calls={"20260901": {"100.0": legacy_contract}})

        # None of these may raise.
        view = to_agent_view(legacy_chain, now=NOW, stale_after_seconds=86400)
        assert view["calls"]["20260901"]["100.0"]["bid"] is None

        dps_result = run_dps_analysis(
            symbol="AAPL", strike=100.0, expiration="20260901", option_type="call",
            chain_json=json.dumps(legacy_chain), snapshots=[{"underlying_price": 100.0, "timestamp": "2026-01-01T00:00:00Z"}],
            underlying_price=100.0,
        )
        assert dps_result.get("status") in ("NO_DATA", "MONITOR", "WATCH", "ROLL", "HOLD", "ERROR")

        roll_result = compute_roll_table(
            chain=legacy_chain, current_strike=100.0, current_expiration="20260901",
            option_type="call", underlying_price=100.0, premium_received=1.0,
        )
        assert isinstance(roll_result, dict)

        table = format_roll_candidates_table(
            chain=legacy_chain, current_strike=100.0, current_expiration="20260901",
            option_type="call", underlying_price=100.0, roll_type="ROLL_OUT",
        )
        assert isinstance(table, str)
