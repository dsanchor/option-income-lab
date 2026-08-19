"""Pure options-chain merge semantics — accumulate-and-merge design.

Implements the seven frozen functions specified in
``.squad/decisions/inbox/danny-persistent-option-chain-merge.md`` (§8,
Linus's ownership). This module is intentionally **pure**: no Cosmos, no
threading/locks, no network I/O, no cache lifecycle. Callers (the cache /
persistence layer) own hydration, concurrency, and storage; this module only
implements the validity/merge/prune *semantics*.

Chain shape (unchanged, additive ``_meta`` only):
    {
      "symbol": "<TICKER>", "timestamp": "<ISO 8601>",
      "calls": {"<YYYYMMDD>": {"<strike>": {contract}, ...}, ...},
      "puts":  {"<YYYYMMDD>": {"<strike>": {contract}, ...}, ...},
    }

Field classes (design §2.1):
  * Identity      — strike, expiration, option_type: never merged, always
                     taken from the dict key path (side/expiration/strike).
  * Near-identity — contractSymbol: first non-empty wins; an existing
                     OCC-format symbol is never downgraded.
  * Observed      — bid, ask, iv, lastPrice, lastTradeDate, volume,
                     openInterest, inTheMoney: field-level merge per §2.3.
  * Derived       — mid, delta, gamma, theta, vega, rho: never merged from
                     any source; always recomputed by ``recompute_derived``.
                     Rule Z3 (danny-zero-free-agent-option-chains.md): a
                     derived field is ``None`` whenever its inputs were not
                     valid — ``mid`` is ``None`` when neither bid nor ask is
                     usable (``robust_mid_optional``), and all five Greeks
                     are ``None`` together whenever ``_meta.greeks_valid``
                     would be False (the intrinsic-only ``_expired_greeks``
                     fallback is never persisted or served). This nulling
                     happens in the raw/persisted layer too — a fabricated
                     derived number is our own artifact, not a provider
                     observation, so it carries no provenance to protect.
  * Provenance    — ``_meta``: written by the merger only, never read from
                     a source. ``greeks_asof`` mirrors ``quote_asof``: set
                     only when greeks were actually recomputed this cycle
                     (i.e. whenever ``greeks_valid`` is True).

Absence is not zero (Rule S1 / S2): a provider that cannot observe a field
MUST emit ``None`` or omit the key. The merger treats a missing key and an
explicit ``None`` identically — no opinion, keep whatever is already stored.

Rule S3: an expiration key that is not ``^\\d{8}$`` and does not parse as a
real calendar date is rejected at ingestion (``merge_sources``), never
stored, and defensively re-checked in ``merge_prior``/``prune_by_expiration``.
"""

import copy
import math
import re
from datetime import date, datetime, timezone
from typing import Any, Dict, Mapping, Optional, Tuple

from src.options_math import robust_mid_optional

# ---------------------------------------------------------------------------
# Field-class constants
# ---------------------------------------------------------------------------

# The "quote group" — trust-gated together per design §2.4 (the intro to
# that section explicitly names all five fields as one unit).
_QUOTE_GROUP_FIELDS: Tuple[str, ...] = ("bid", "ask", "iv", "lastPrice", "lastTradeDate")

# Independent observations — never gated (bucket or per-contract), never
# subject to the trust gate; contractSymbol gets its own OCC-aware rule.
_OTHER_OBSERVED_FIELDS: Tuple[str, ...] = ("volume", "openInterest", "inTheMoney", "contractSymbol")

# Never accepted from any source — always recomputed in recompute_derived.
_DERIVED_FIELDS: Tuple[str, ...] = ("mid", "delta", "gamma", "theta", "vega", "rho")

# Identity fields — taken from the dict key path (side/expiration/strike),
# never merged from a payload.
_IDENTITY_FIELDS: Tuple[str, ...] = ("strike", "expiration", "option_type")

# IV sanity cap (design §2.3): 500% is treated as a data-quality failure.
_IV_MAX = 5.0

# Per-bucket degeneracy floor (design §2.4): below this a thin expiration is
# not misread as a feed outage; it falls through to the per-contract gate.
_DEGENERACY_MIN_CONTRACTS = 3

_EXP_KEY_RE = re.compile(r"^\d{8}$")
_OCC_SYMBOL_RE = re.compile(r"^[A-Z]{1,6}\d{6}[CP]\d{8}$")

# Fixed risk-free rate: this module must stay pure/offline (no network), so
# GreeksCalculator's lazy ^TNX yfinance fetch is deliberately bypassed by
# always supplying a rate explicitly. Matches GreeksCalculator's own
# hardcoded fallback when a live fetch is unavailable.
_DEFAULT_RISK_FREE_RATE = 0.045

_greeks_calculator = None


def _get_greeks_calculator():
    """Lazily construct a GreeksCalculator with a fixed risk-free rate so
    this module never makes a network call."""
    global _greeks_calculator
    if _greeks_calculator is None:
        from src.greeks_calculator import GreeksCalculator
        _greeks_calculator = GreeksCalculator(risk_free_rate=_DEFAULT_RISK_FREE_RATE)
    return _greeks_calculator


# ---------------------------------------------------------------------------
# Low-level value helpers
# ---------------------------------------------------------------------------

def _finite_number(value: Any) -> bool:
    """True if `value` is a real (non-bool) int/float and finite."""
    if isinstance(value, bool):
        return False
    if not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value))


def _is_nonneg_integer_like(value: Any) -> bool:
    if not _finite_number(value):
        return False
    f = float(value)
    return f >= 0 and f == int(f)


def _parse_timestamp(value: Any) -> Optional[datetime]:
    """Best-effort parse of a timestamp-like value. Returns None if it
    doesn't parse as a real point in time."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (ValueError, OverflowError, OSError):
            return None
    if isinstance(value, str) and value.strip():
        text = value.strip()
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _is_newer_timestamp(candidate: Any, current: Any) -> bool:
    """True if `candidate` parses and is not older than `current` (or
    `current` is absent/unparseable, in which case any parseable candidate
    wins)."""
    candidate_dt = _parse_timestamp(candidate)
    if candidate_dt is None:
        return False
    current_dt = _parse_timestamp(current)
    if current_dt is None:
        return True
    return candidate_dt >= current_dt


def _is_occ_symbol(value: Any) -> bool:
    return isinstance(value, str) and bool(_OCC_SYMBOL_RE.match(value))


def _accept_contract_symbol(existing: Any, candidate: Any) -> Optional[str]:
    """Near-identity merge rule: first non-empty wins; an existing
    OCC-format symbol is never replaced by a non-OCC or empty one."""
    if not is_accepted("contractSymbol", candidate):
        return existing
    if existing and _is_occ_symbol(existing) and not _is_occ_symbol(candidate):
        return existing
    return candidate


def _is_valid_expiration_key(key: Any) -> bool:
    """Rule S3: an expiration key must be ``^\\d{8}$`` and parse as a real
    calendar date."""
    if not isinstance(key, str) or not _EXP_KEY_RE.match(key):
        return False
    try:
        datetime.strptime(key, "%Y%m%d")
    except ValueError:
        return False
    return True


def _parse_expiration_date(exp_key: Any) -> Optional[date]:
    if not _is_valid_expiration_key(exp_key):
        return None
    return datetime.strptime(exp_key, "%Y%m%d").date()


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# 1. is_accepted — per-field acceptance predicate (design §2.3)
# ---------------------------------------------------------------------------

def is_accepted(field: str, value: Any) -> bool:
    """True if `value` is a semantically valid candidate for `field`.

    This is a pure, single-value predicate — it does NOT apply the trust
    gate (that needs the whole contract; see `gate_contract`). For `bid`
    and `lastPrice`, zero is a real, acceptable value on its own; whether it
    is *trustworthy* this cycle is decided separately by `gate_contract`.
    Derived fields (mid/delta/gamma/theta/vega/rho) and identity fields
    (strike/expiration/option_type) are never accepted from a source here —
    the former are always recomputed, the latter always come from the dict
    key path.
    """
    if field == "ask":
        return _finite_number(value) and value > 0
    if field == "iv":
        return _finite_number(value) and 0 < value < _IV_MAX
    if field in ("bid", "lastPrice"):
        return _finite_number(value) and value >= 0
    if field in ("volume", "openInterest"):
        return _is_nonneg_integer_like(value)
    if field == "lastTradeDate":
        return _parse_timestamp(value) is not None
    if field == "inTheMoney":
        return isinstance(value, bool)
    if field == "contractSymbol":
        return isinstance(value, str) and value.strip() != ""
    # Derived fields and identity fields: never accepted from a source.
    return False


# ---------------------------------------------------------------------------
# 2. gate_contract — per-contract trust gate (design §2.4)
# ---------------------------------------------------------------------------

def gate_contract(contract: Mapping[str, Any]) -> bool:
    """True if this contract's quote group (bid/ask/iv/lastPrice/
    lastTradeDate) can be trusted from this payload: it supplies at least
    one valid ask (>0) or valid iv. A source that can quote neither an
    offer nor an implied vol is not quoting — its zeros carry no
    information."""
    if not isinstance(contract, dict):
        return False
    return is_accepted("ask", contract.get("ask")) or is_accepted("iv", contract.get("iv"))


# ---------------------------------------------------------------------------
# 3. gate_bucket — per-(source, side, expiration) degeneracy gate (§2.4)
# ---------------------------------------------------------------------------

def gate_bucket(bucket: Mapping[str, Any]) -> bool:
    """True if this bucket (strike_key -> contract, for one source/side/
    expiration) is usable. False only when it has >= 3 contracts and every
    single one fails `gate_contract` — the observed "provider returned an
    all-zero chain" failure mode. Below the floor, per-contract gating
    alone is the safe/adequate mechanism."""
    if not bucket:
        return True
    contracts = list(bucket.values())
    if len(contracts) >= _DEGENERACY_MIN_CONTRACTS and all(not gate_contract(c) for c in contracts):
        return False
    return True


# ---------------------------------------------------------------------------
# 4. merge_sources — Phase 1: yfinance + TradingView (design §3.1)
# ---------------------------------------------------------------------------

def _merge_two_contracts(
    yf_contract: Optional[dict],
    tv_contract: Optional[dict],
    *,
    yf_quote_ok: bool,
    tv_quote_ok: bool,
) -> dict:
    """Merge one strike's yfinance and TradingView contracts (Phase 1).

    Quote group: TradingView > yfinance, field by field, only for fields
    each source actually supplies and that pass the trust gate. Other
    observed fields: yfinance authoritative, TradingView fallback (a
    no-op in practice since TV never supplies them under Rule S1).
    """
    merged: Dict[str, Any] = {}
    quote_source = None

    for field in _QUOTE_GROUP_FIELDS:
        value = None
        if tv_quote_ok and tv_contract is not None:
            candidate = tv_contract.get(field)
            if is_accepted(field, candidate):
                value = candidate
                quote_source = "tradingview"
        if value is None and yf_quote_ok and yf_contract is not None:
            candidate = yf_contract.get(field)
            if is_accepted(field, candidate):
                value = candidate
                quote_source = quote_source or "yfinance"
        if value is not None:
            merged[field] = value

    for field in _OTHER_OBSERVED_FIELDS:
        if field == "contractSymbol":
            symbol = _accept_contract_symbol(
                None, yf_contract.get(field) if yf_contract is not None else None
            )
            symbol = _accept_contract_symbol(
                symbol, tv_contract.get(field) if tv_contract is not None else None
            )
            if symbol:
                merged[field] = symbol
            continue
        value = None
        if yf_contract is not None:
            candidate = yf_contract.get(field)
            if is_accepted(field, candidate):
                value = candidate
        if value is None and tv_contract is not None:
            candidate = tv_contract.get(field)
            if is_accepted(field, candidate):
                value = candidate
        if value is not None:
            merged[field] = value

    if quote_source is not None:
        # Internal handoff to merge_prior — not part of the public schema.
        merged["_quote_source"] = quote_source
    return merged


def merge_sources(yf_chain: dict, tv_chain: dict) -> dict:
    """Phase 1: merge two freshly fetched chains (yfinance base, TradingView
    overlay) with explicit field-level precedence. Does not mutate either
    input. Malformed expiration keys are rejected here (Rule S3)."""
    yf_chain = yf_chain or {}
    tv_chain = tv_chain or {}

    result: Dict[str, Any] = {
        "symbol": yf_chain.get("symbol") or tv_chain.get("symbol"),
        "timestamp": yf_chain.get("timestamp") or tv_chain.get("timestamp"),
        "calls": {},
        "puts": {},
    }

    for side, option_type in (("calls", "call"), ("puts", "put")):
        yf_side = yf_chain.get(side) or {}
        tv_side = tv_chain.get(side) or {}
        merged_side: Dict[str, Any] = {}

        for exp_key in set(yf_side) | set(tv_side):
            if not _is_valid_expiration_key(exp_key):
                # Rule S3: reject unparseable expirations at ingestion.
                continue

            yf_bucket = yf_side.get(exp_key) or {}
            tv_bucket = tv_side.get(exp_key) or {}
            yf_bucket_ok = gate_bucket(yf_bucket)
            tv_bucket_ok = gate_bucket(tv_bucket)

            merged_bucket: Dict[str, Any] = {}
            for strike_key in set(yf_bucket) | set(tv_bucket):
                yf_contract = yf_bucket.get(strike_key)
                tv_contract = tv_bucket.get(strike_key)
                if yf_contract is None and tv_contract is None:
                    continue

                yf_quote_ok = yf_bucket_ok and yf_contract is not None and gate_contract(yf_contract)
                tv_quote_ok = tv_bucket_ok and tv_contract is not None and gate_contract(tv_contract)

                merged_contract = _merge_two_contracts(
                    yf_contract, tv_contract,
                    yf_quote_ok=yf_quote_ok, tv_quote_ok=tv_quote_ok,
                )
                merged_contract["strike"] = float(strike_key)
                merged_contract["expiration"] = exp_key
                merged_contract["option_type"] = option_type
                merged_bucket[strike_key] = merged_contract

            if merged_bucket:
                merged_side[exp_key] = merged_bucket

        result[side] = merged_side

    return result


# ---------------------------------------------------------------------------
# 5. merge_prior — Phase 2: accumulate against prior (design §3.2)
# ---------------------------------------------------------------------------

def _select_quote_field(field: str, live_contract: Mapping[str, Any], prior_value: Any) -> Tuple[Any, bool]:
    """Return (value, accepted) for one quote-group field: the live
    candidate if it's present, individually valid, and the whole quote
    group passes the trust gate for this live contract; otherwise the
    prior value (with accepted=False)."""
    if field not in live_contract:
        return prior_value, False
    candidate = live_contract[field]
    if not is_accepted(field, candidate):
        return prior_value, False
    if not gate_contract(live_contract):
        return prior_value, False
    if field == "lastTradeDate" and not _is_newer_timestamp(candidate, prior_value):
        return prior_value, False
    return candidate, True


def _select_observed_field(field: str, live_contract: Mapping[str, Any], prior_value: Any) -> Any:
    """Independent observations (volume/openInterest/inTheMoney): never
    gated — take the live value whenever it's individually valid."""
    if field not in live_contract:
        return prior_value
    candidate = live_contract[field]
    return candidate if is_accepted(field, candidate) else prior_value


def _merge_prior_contract(
    prior_contract: Optional[dict],
    live_contract: Optional[dict],
    *,
    side: str,
    exp_key: str,
    strike_key: str,
    now_iso: str,
) -> dict:
    option_type = "call" if side == "calls" else "put"

    if live_contract is None:
        # No source listed this contract at all this cycle: carry forward
        # verbatim. A contract never disappears because a provider omitted
        # it. quote_asof/quote_source/first_seen/last_seen are NOT advanced.
        carried = copy.deepcopy(prior_contract) if prior_contract is not None else {}
        meta = dict(carried.get("_meta") or {})
        meta["carried"] = True
        meta.setdefault("quote_asof", None)
        meta.setdefault("quote_source", None)
        meta.setdefault("first_seen", now_iso)
        meta.setdefault("last_seen", now_iso)
        meta.setdefault("greeks_valid", False)
        carried["_meta"] = meta
        carried["strike"] = float(strike_key)
        carried["expiration"] = exp_key
        carried["option_type"] = option_type
        return carried

    live_contract = dict(live_contract)
    quote_source_hint = live_contract.pop("_quote_source", None)
    prior_contract = prior_contract or {}
    prior_meta = dict(prior_contract.get("_meta") or {})

    merged: Dict[str, Any] = {}
    quote_updated = False
    for field in _QUOTE_GROUP_FIELDS:
        prior_value = prior_contract.get(field)
        value, accepted = _select_quote_field(field, live_contract, prior_value)
        if value is not None:
            merged[field] = value
        quote_updated = quote_updated or accepted

    for field in _OTHER_OBSERVED_FIELDS:
        if field == "contractSymbol":
            symbol = _accept_contract_symbol(prior_contract.get(field), live_contract.get(field))
            if symbol:
                merged[field] = symbol
            continue
        prior_value = prior_contract.get(field)
        value = _select_observed_field(field, live_contract, prior_value)
        if value is not None:
            merged[field] = value

    meta = dict(prior_meta)
    meta["carried"] = False
    meta.setdefault("quote_asof", None)
    meta.setdefault("quote_source", None)
    meta.setdefault("first_seen", now_iso)
    meta["last_seen"] = now_iso
    meta.setdefault("greeks_valid", False)
    if quote_updated:
        meta["quote_asof"] = now_iso
        meta["quote_source"] = quote_source_hint or "yfinance"
    merged["_meta"] = meta
    merged["strike"] = float(strike_key)
    merged["expiration"] = exp_key
    merged["option_type"] = option_type
    return merged


def merge_prior(prior: dict, live: dict, *, now: datetime) -> dict:
    """Phase 2: contract-level union of `prior` (accumulated store) and
    `live` (this cycle's merge_sources output), field-level overwrite-if-
    accepted. Does not mutate either input. Monotone: only adds contracts,
    only advances `quote_asof` forward."""
    prior = prior or {}
    live = live or {}
    now_iso = _iso(now)

    result: Dict[str, Any] = {
        "symbol": live.get("symbol") or prior.get("symbol"),
        "timestamp": live.get("timestamp") or prior.get("timestamp"),
        "calls": {},
        "puts": {},
    }

    for side in ("calls", "puts"):
        prior_side = prior.get(side) or {}
        live_side = live.get(side) or {}
        merged_side: Dict[str, Any] = {}

        for exp_key in set(prior_side) | set(live_side):
            if not _is_valid_expiration_key(exp_key):
                # Defense in depth — Rule S3 is enforced at merge_sources;
                # never propagate a junk key that somehow got this far.
                continue

            prior_bucket = prior_side.get(exp_key) or {}
            live_bucket = live_side.get(exp_key) or {}
            merged_bucket: Dict[str, Any] = {}

            for strike_key in set(prior_bucket) | set(live_bucket):
                merged_bucket[strike_key] = _merge_prior_contract(
                    prior_bucket.get(strike_key),
                    live_bucket.get(strike_key),
                    side=side, exp_key=exp_key, strike_key=strike_key,
                    now_iso=now_iso,
                )

            if merged_bucket:
                merged_side[exp_key] = merged_bucket

        result[side] = merged_side

    return result


# ---------------------------------------------------------------------------
# 6. recompute_derived — Phase 3: mid + greeks (design §3.3)
# ---------------------------------------------------------------------------

def _time_to_expiry_years(exp_key: str, now: datetime) -> float:
    exp_date = _parse_expiration_date(exp_key)
    if exp_date is None:
        return 1e-10
    now_utc = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    exp_dt = datetime(exp_date.year, exp_date.month, exp_date.day, tzinfo=timezone.utc)
    dte = (exp_dt - now_utc).days
    return max(dte / 365.0, 1e-10)


def _recompute_contract(
    contract: dict, underlying_price: Any, T: float, flag: str, greeks_calc, now_iso: str
) -> dict:
    """Recompute mid + greeks for one contract (design §1.3 / Rule Z3-Z4).

    A derived field is `None` whenever its inputs were not valid — never a
    placeholder number. `mid` is `None` when neither bid nor ask is usable
    (`robust_mid_optional`). The five Greeks are `None` together whenever
    `greeks_valid` would be False; `GreeksCalculator.compute()` (and its
    `_expired_greeks()` intrinsic-only fallback) is simply never called in
    that case, so no manufactured intrinsic-limit number is ever produced,
    persisted, or served. `_meta.greeks_asof` mirrors `_meta.quote_asof`:
    it is set only when greeks were actually (re)computed this cycle.
    """
    new_contract = dict(contract)

    bid = contract.get("bid")
    ask = contract.get("ask")
    last_price = contract.get("lastPrice")
    iv = contract.get("iv")
    strike = contract.get("strike")

    new_contract["mid"] = robust_mid_optional(bid, ask, last_price)

    iv_valid = is_accepted("iv", iv)
    price_ok = _finite_number(underlying_price) and underlying_price > 0
    strike_ok = _finite_number(strike) and strike > 0
    greeks_valid = bool(iv_valid and price_ok and strike_ok)

    if greeks_valid:
        greeks = greeks_calc.compute(flag, float(underlying_price), float(strike), T, float(iv))
        new_contract["delta"] = greeks["delta"]
        new_contract["gamma"] = greeks["gamma"]
        new_contract["theta"] = greeks["theta"]
        new_contract["vega"] = greeks["vega"]
        new_contract["rho"] = greeks["rho"]
    else:
        new_contract["delta"] = None
        new_contract["gamma"] = None
        new_contract["theta"] = None
        new_contract["vega"] = None
        new_contract["rho"] = None

    meta = dict(contract.get("_meta") or {})
    meta["greeks_valid"] = greeks_valid
    meta["greeks_asof"] = now_iso if greeks_valid else None
    new_contract["_meta"] = meta
    return new_contract


def recompute_derived(chain: dict, underlying_price: float, *, now: datetime) -> dict:
    """Phase 3: recompute mid + greeks for every contract from the merged
    primitives, the current underlying price, and the current time-to-
    expiry. Derived fields are never carried as observations — this is the
    sole writer of mid/delta/gamma/theta/vega/rho. Does not mutate the
    input chain."""
    chain = chain or {}
    result: Dict[str, Any] = {
        "symbol": chain.get("symbol"),
        "timestamp": chain.get("timestamp"),
        "calls": {},
        "puts": {},
    }
    greeks_calc = _get_greeks_calculator()
    now_iso = _iso(now)

    for side, flag in (("calls", "c"), ("puts", "p")):
        side_bucket = chain.get(side) or {}
        merged_side: Dict[str, Any] = {}
        for exp_key, strikes in side_bucket.items():
            T = _time_to_expiry_years(exp_key, now)
            merged_strikes: Dict[str, Any] = {}
            for strike_key, contract in strikes.items():
                merged_strikes[strike_key] = _recompute_contract(
                    contract, underlying_price, T, flag, greeks_calc, now_iso
                )
            merged_side[exp_key] = merged_strikes
        result[side] = merged_side

    return result


# ---------------------------------------------------------------------------
# 7. prune_by_expiration — retention by real calendar date (design §4.1)
# ---------------------------------------------------------------------------

def prune_by_expiration(chain: dict, *, today_et: date) -> dict:
    """Drop expiration buckets whose actual contract expiration date has
    passed (`exp_date_ET < today_ET`). The whole expiration day is kept.
    Never prunes on TTL/staleness/carried-age/absence. Does not mutate the
    input chain."""
    chain = chain or {}
    result: Dict[str, Any] = {
        "symbol": chain.get("symbol"),
        "timestamp": chain.get("timestamp"),
        "calls": {},
        "puts": {},
    }
    for side in ("calls", "puts"):
        side_bucket = chain.get(side) or {}
        kept: Dict[str, Any] = {}
        for exp_key, strikes in side_bucket.items():
            exp_date = _parse_expiration_date(exp_key)
            if exp_date is not None and exp_date < today_et:
                continue
            kept[exp_key] = dict(strikes)
        result[side] = kept
    return result
