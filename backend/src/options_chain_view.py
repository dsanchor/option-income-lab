"""Agent-facing / scoring normalization boundary for option chains.

Decision: `.squad/decisions/inbox/danny-zero-free-agent-option-chains.md`
("Zero-Free Agent-Facing Option Chains", accepted 2026-08-19). This module
is the **single** place that converts a raw/persisted chain (in which a
provider-observed `bid = 0.0` or `volume = 0` is a real, faithfully-stored
market fact) into the representation every LLM agent, deterministic
scorer, or API/UI consumer must use, in which a numeric zero can never be
mistaken for a usable price/IV/Greek.

Two layers, two policies:

  * Raw / persisted (``options_chain_merge.py``) stays faithful. `bid=0.0`
    ("no buyers") and `volume=0` / `openInterest=0` (real liquidity facts)
    are never rewritten. Nothing in this module mutates or is consulted by
    the raw layer.
  * Agent-facing (here) never presents an unusable numeric zero as a
    quote/IV/Greek. `bid == 0` -> ``None`` (with `field_status == "no_market"`
    so the "no bid" fact is still visible, just relocated from the value
    channel to the status channel). `volume` / `openInterest` are the
    explicit Z2 carve-out: a real `0` is decisive liquidity evidence and is
    always kept as an integer `0`, never nulled.

Five frozen public functions (Danny's G0 contract, §2.2 of the decision):

    to_agent_view(chain, *, now, stale_after_seconds) -> dict
    contract_view(contract, *, now, stale_after_seconds) -> dict
    usable_quote(contract, field) -> float | None      # Z1 accessor
    usable_greek(contract, name) -> float | None        # Z4 accessor
    is_candidate_eligible(contract, *, min_open_interest=1) -> bool  # §3

Properties (test-enforced, §2.2): pure (never mutates its input), total
(never raises, even on malformed/None/empty input), idempotent
(``to_agent_view(to_agent_view(x)) == to_agent_view(x)``), and
shape-preserving (same ``symbol``/``timestamp``/``calls``/``puts`` nesting
and same keys; only value types widen from ``float`` to ``float | None``).

Every consumer must obtain quotes/Greeks through ``usable_quote`` /
``usable_greek`` (or a chain already passed through ``to_agent_view``).
Direct ``contract.get("bid")`` (etc.) in a consumer is a review-blocking
defect from this decision forward (§2.2).

Idempotence note on `field_status`: once a contract has been through this
boundary, an ambiguous field (`bid`, `lastPrice`) that was nulled from a
genuine zero is indistinguishable, by value alone, from one that was never
observed at all -- both read back as ``None``. To keep a second pass
byte-identical to the first (Z-V1), `contract_view` treats an existing
``_meta.field_status`` on the input as authoritative and carries it forward
verbatim rather than re-deriving it from already-nulled values.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

# Fields for which "usable" means the same thing everywhere: a real,
# finite, strictly positive number. Zero and absence are both unusable —
# only their *label* (field_status) differs by field.
_QUOTE_FIELDS = ("bid", "ask", "lastPrice", "iv", "mid")
_GREEK_FIELDS = ("delta", "gamma", "theta", "vega", "rho")

_FIELD_STATUS_VOCABULARY = frozenset(
    {"live", "last_known_good", "no_market", "no_trades", "unavailable"}
)

_EMPTY_CHAIN_VIEW = {"symbol": None, "timestamp": None, "calls": {}, "puts": {}}


def _is_finite_number(value: Any) -> bool:
    if value is None or isinstance(value, bool):
        return False
    if not isinstance(value, (int, float)):
        return False
    return math.isfinite(value)


def _is_positive_number(value: Any) -> bool:
    return _is_finite_number(value) and float(value) > 0


def usable_quote(contract: Mapping, field: str) -> Optional[float]:
    """Z1 accessor. Returns the field's value as a float only when it is a
    real, finite, strictly positive number -- for ``bid``, ``ask``,
    ``lastPrice``, ``iv``, and ``mid`` alike. Zero and absence both yield
    ``None``. Total: never raises, regardless of ``contract``/``field``
    shape."""
    try:
        if not isinstance(contract, Mapping) or field not in _QUOTE_FIELDS:
            return None
        value = contract.get(field)
        return float(value) if _is_positive_number(value) else None
    except Exception:
        return None


def usable_greek(contract: Mapping, name: str) -> Optional[float]:
    """Z4 accessor. ``_meta.greeks_valid`` is binding: an *explicit*
    ``False`` means "the Greek does not exist" and always yields ``None``,
    even if the contract still carries a legacy numeric Greek (Z-V6).
    A contract with no ``_meta``/``greeks_valid`` at all (never computed by
    ``recompute_derived`` -- e.g. a hand-built fixture predating this
    boundary) is not the contamination Z4 targets; its raw numeric value is
    trusted if present. Total: never raises."""
    try:
        if not isinstance(contract, Mapping) or name not in _GREEK_FIELDS:
            return None
        meta = contract.get("_meta")
        if isinstance(meta, Mapping) and meta.get("greeks_valid") is False:
            return None
        value = contract.get(name)
        return float(value) if _is_finite_number(value) else None
    except Exception:
        return None


def is_candidate_eligible(contract: Mapping, *, min_open_interest: int = 1) -> bool:
    """Rule Z10 (§3): a contract is a proposable roll/open candidate only
    when it has a usable bid, meets the open-interest floor, and its
    Greeks are valid (so risk can actually be assessed).

    ``min_open_interest`` defaults to ``1`` (i.e. ``openInterest > 0``),
    the documented default from the decision's open question #1; callers
    needing the stricter pre-existing roll-table liquidity heuristic may
    pass a larger threshold. Total: never raises."""
    try:
        if not isinstance(contract, Mapping):
            return False
        if usable_quote(contract, "bid") is None:
            return False
        open_interest = contract.get("openInterest")
        if not _is_finite_number(open_interest) or float(open_interest) < min_open_interest:
            return False
        meta = contract.get("_meta")
        if isinstance(meta, Mapping) and meta.get("greeks_valid") is False:
            return False
        return True
    except Exception:
        return False


def _parse_iso(value: Any) -> Optional[datetime]:
    """Small, locally-implemented ISO-8601 parser -- deliberately not
    coupled to options_chain_merge.py's private timestamp helpers, so this
    module has no dependency on the merge layer's internals."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value.strip():
        try:
            return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _is_stale(quote_asof: Any, now: datetime, stale_after_seconds: int) -> bool:
    """Conservative by design: an absent/unparseable `quote_asof` is
    treated as stale (we have no evidence it is fresh)."""
    asof_dt = _parse_iso(quote_asof)
    if asof_dt is None:
        return True
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    age_seconds = (now - asof_dt).total_seconds()
    return age_seconds > stale_after_seconds


def _fresh_field_status(contract: Mapping, meta: Mapping) -> dict:
    """Derive field_status from raw (not-yet-nulled) field values. Only
    called when the input has not already been through this boundary."""
    carried = meta.get("carried") is True

    def _liveness() -> str:
        return "last_known_good" if carried else "live"

    bid = contract.get("bid")
    if bid is None:
        bid_status = "unavailable"
    elif _is_finite_number(bid) and float(bid) == 0:
        bid_status = "no_market"
    elif _is_positive_number(bid):
        bid_status = _liveness()
    else:
        bid_status = "unavailable"

    ask = contract.get("ask")
    ask_status = _liveness() if _is_positive_number(ask) else "unavailable"

    last_price = contract.get("lastPrice")
    if last_price is None:
        last_price_status = "unavailable"
    elif _is_finite_number(last_price) and float(last_price) == 0:
        last_price_status = "no_trades"
    elif _is_positive_number(last_price):
        last_price_status = _liveness()
    else:
        last_price_status = "unavailable"

    iv = contract.get("iv")
    iv_status = _liveness() if _is_positive_number(iv) else "unavailable"

    # mid/greeks are recomputed fresh every cycle -- there is no
    # last-known-good concept for a derived value (Z3): either the
    # inputs were usable this cycle and it is "live", or they were not.
    # "greeks" tracks the same explicit-False binding rule as
    # usable_greek (Z4) -- an absent greeks_valid does not itself imply
    # unavailability; a genuinely usable Greek value does.
    mid_status = "live" if _is_positive_number(contract.get("mid")) else "unavailable"
    greeks_usable = meta.get("greeks_valid") is not False and any(
        _is_finite_number(contract.get(g)) for g in _GREEK_FIELDS
    )
    greeks_status = "live" if greeks_usable else "unavailable"

    return {
        "bid": bid_status,
        "ask": ask_status,
        "lastPrice": last_price_status,
        "iv": iv_status,
        "mid": mid_status,
        "greeks": greeks_status,
    }


def contract_view(contract: dict, *, now: datetime, stale_after_seconds: int) -> dict:
    """Convert one raw/persisted contract into its agent-facing view.

    Pure (the input ``contract`` and its nested ``_meta`` are never
    mutated), total (malformed input yields a safe empty-ish shape rather
    than raising), and idempotent (re-applying to already-viewed output
    reproduces it exactly)."""
    try:
        if not isinstance(contract, Mapping):
            return {}

        meta_in = contract.get("_meta")
        meta_in = dict(meta_in) if isinstance(meta_in, Mapping) else {}

        existing_field_status = meta_in.get("field_status")
        if isinstance(existing_field_status, Mapping):
            # Already view-normalized: reuse verbatim rather than
            # re-deriving from now-nulled raw values (see module
            # docstring's idempotence note).
            field_status = dict(existing_field_status)
        else:
            field_status = _fresh_field_status(contract, meta_in)

        view = dict(contract)
        for field in _QUOTE_FIELDS:
            view[field] = usable_quote(contract, field)
        for greek in _GREEK_FIELDS:
            view[greek] = usable_greek(contract, greek)
        # volume/openInterest: Z2 carve-out -- a real 0 is kept as 0,
        # never nulled. Pass through unchanged (already copied above).

        new_meta = dict(meta_in)
        new_meta["stale"] = _is_stale(meta_in.get("quote_asof"), now, stale_after_seconds)
        new_meta["tradable"] = view["bid"] is not None and view["ask"] is not None
        new_meta["field_status"] = field_status
        new_meta.setdefault("greeks_asof", None)
        view["_meta"] = new_meta
        return view
    except Exception:
        return {}


def to_agent_view(chain: dict, *, now: datetime, stale_after_seconds: int) -> dict:
    """Convert a whole raw/persisted chain (``{symbol, timestamp, calls,
    puts}``) into its agent-facing view. Pure, total, idempotent, and
    shape-preserving -- see module docstring."""
    try:
        if not isinstance(chain, Mapping):
            return dict(_EMPTY_CHAIN_VIEW)

        result: dict = {
            "symbol": chain.get("symbol"),
            "timestamp": chain.get("timestamp"),
            "calls": {},
            "puts": {},
        }
        for side in ("calls", "puts"):
            side_bucket = chain.get(side)
            if not isinstance(side_bucket, Mapping):
                continue
            new_side: dict = {}
            for exp_key, strikes in side_bucket.items():
                if not isinstance(strikes, Mapping):
                    new_side[exp_key] = {}
                    continue
                new_strikes = {
                    strike_key: contract_view(contract, now=now, stale_after_seconds=stale_after_seconds)
                    for strike_key, contract in strikes.items()
                }
                new_side[exp_key] = new_strikes
            result[side] = new_side
        return result
    except Exception:
        return dict(_EMPTY_CHAIN_VIEW)
