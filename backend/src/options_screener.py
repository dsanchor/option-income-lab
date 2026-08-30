"""Deterministic Options Screener aggregation.

Design: `.squad/decisions/inbox/copilot-options-screener-approved.md`
("Options Screener approved", 2026-08-29). Pure, no I/O, no LLM, no
Cosmos, no FastAPI, no chain fetching/warming: this module is handed
already-assembled per-symbol inputs (a chain plus the small set of
symbol-level facts `evaluate_best_options` needs -- category, share
count, earnings/ex-dividend dates, support level -- and an explicit
`status` describing whether that symbol's chain is ready, still
warming, or failed) and produces one flattened, filtered, sorted,
paginated view across every symbol, per side.

**Reuse, not duplication (the whole point of this module):** every row
in the output is produced by calling `best_options.evaluate_best_options`
literally, once per ready symbol, with that module's own default DTE
window (`DEFAULT_DTE_MIN`/`DEFAULT_DTE_MAX`) and whatever category the
caller supplies. No scoring, gating, delta-band, or DTE-window logic is
reimplemented here. This module's only two jobs are (1) run that
function once per symbol and (2) aggregate/narrow/sort/paginate the
rows it returns.

**Global filters only narrow, they never widen.** Each symbol's own
category already picked its own delta band, and every symbol's window is
already `evaluate_best_options`'s own default (`[0, 45]` inclusive) --
that per-symbol admission is not renegotiated here. The screener's own
`min_abs_delta`/`max_abs_delta`/`min_dte`/`max_dte`/`min_open_interest`/
`min_annualized_return_pct`/`preferences`/`symbols` parameters are strictly
additional row-level filters applied *after* a symbol's own rows are
already produced -- they can only remove rows a symbol's own category
rules already admitted, never reach past them.

**A metric filter with no data on a row fails that filter.** If the
caller sets `min_open_interest` and a row's `open_interest` is `None`,
that row cannot be shown to satisfy the requested minimum and is
excluded -- absence is never treated as satisfying a "no less than X"
constraint. Symmetric for `min_annualized_return_pct`, `min_abs_delta`/
`max_abs_delta`, and `min_dte`/`max_dte`. A filter left unset (`None`)
never excludes a row on that axis regardless of whether the row's own
metric is present.

**Calls/Puts separation is preserved end to end.** Rows from the two
sides are never merged into one ranked list; the result's `"calls"` and
`"puts"` sections are independent -- their own rows, own pagination, own
`nearest_miss` list, own sort. Requesting `side="call"` (or `"put"`)
skips evaluating the other side entirely (passed straight through to
`evaluate_best_options`'s own `side` parameter, which already skips the
unrequested side's real work) -- this is a performance path, not merely
a smaller response.

**`nearest_miss` is per zero-row *symbol*, not per filtered-out row.** A
symbol contributes a `nearest_miss` entry to a side's `nearest_miss` list
only when that symbol's own `evaluate_best_options` call admitted zero
rows for that side (`section["total"] == 0`, i.e. before any screener
filter runs at all). A symbol that *did* have admitted rows, all of
which the screener's own filters subsequently excluded, is simply absent
from that page's rows -- it is not "explained" via `nearest_miss`; that
would conflate "your category rules found nothing" with "you asked for a
narrower view than what exists," which are different facts.

**Sorting (default, and the only order this module implements):** score
descending (`None` last), then DTE ascending (`None` last), then
category-relative delta fit ascending -- `|abs_delta - category_delta_
midpoint|`, using *that row's own symbol's own side's own category*
delta band midpoint, not one shared midpoint across symbols (`None` on
either side of the subtraction sorts last). Ties remaining after all
three are broken by an explicit, stable, total order (symbol, then
expiration, then strike) so output ordering never depends on input
iteration order or the underlying `sorted()` implementation's stability
guarantees alone.

**Memoization (a narrow, explicitly-scoped optimisation, not a second
scorer or a persistence layer):** `evaluate_best_options` is pure and
declares "identical input produces byte-identical output" -- the memo
here exploits exactly that guarantee for a fixed, deliberately small key:
`(symbol, side, chain timestamp, category, total_shares,
next_earnings_date, ex_dividend_date, support_level)`. Notably absent:
`now`/wall-clock time. This is deliberate, not an oversight -- the
memo's freshness signal is the *chain's own* `timestamp` field (stamped
by the cache layer whenever the chain's content actually changes), not
the moment this function happens to be called; two calls against the
same still-current chain produce the same rows regardless of how many
seconds apart they were made, and the screener's own `generated_at`
field (always `now`, on every call, hit or miss) is what tells a caller
when *this aggregated view* was produced. The one accepted, bounded
edge case: `evaluate_best_options`'s 24-hour quote-staleness threshold is
evaluated against whatever `now` was in effect the first time a given
key was computed; if the same chain (same `timestamp`) is still being
reused by the cache layer many hours later, a `stale_quote` flag could
in principle lag a fresh recomputation by that same margin. Given a
24-hour threshold this is a low-value edge case, and re-warming a chain
well before 24 hours elapses is the cache layer's responsibility, not
this module's. Memoization is entirely opt-in and caller-owned: pass a
plain `dict` as `memo` and reuse it across calls to benefit from it, or
omit it (or pass a fresh `dict`/`None`) for a fully from-scratch
evaluation every time -- either way this module remains pure with
respect to its own inputs (the memo is an explicit argument, never
global/module state).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from src.best_options import DEFAULT_DTE_MAX, DEFAULT_DTE_MIN, evaluate_best_options

SCHEMA_VERSION = 1

_SIDES: Tuple[str, ...] = ("call", "put")

# best_options.evaluate_best_options's own top-level result keys for each
# side are plural ("calls"/"puts"); this is the same mapping that
# module's own private _SIDE_BUCKET encodes, reproduced here rather than
# reaching into that private name (matching this module's own stated
# "reuse the public surface, don't reach into internals" discipline).
_SIDE_BUCKET_KEY = {"call": "calls", "put": "puts"}

# Design: "Default preference filter is Preferred + Acceptable; Avoid
# remains selectable." Matches best_options._COLOR_LABELS's own values
# verbatim -- this module reads a row's already-computed `label`, it does
# not re-derive colour/label from score itself.
DEFAULT_PREFERENCES: frozenset = frozenset({"Preferred", "Acceptable"})
_VALID_PREFERENCES: frozenset = frozenset({"Preferred", "Acceptable", "Avoid"})

_VALID_STATUSES: Tuple[str, ...] = ("ready", "warming", "error")

# Memo key shape (see module docstring): every field that actually
# determines evaluate_best_options's output for a symbol/side, and
# nothing that doesn't (deliberately excludes `now`).
_MemoKey = Tuple[Any, ...]


def _normalize_symbol(value: Any) -> str:
    """Total ticker normalisation -- matches the `.strip().upper()`
    convention already used across this codebase (dgi_screener.py,
    report_agent.py, technical_analysis_agent.py, cosmos_db.py)."""
    return str(value if value is not None else "").strip().upper()


def _normalize_side(value: Any) -> str:
    return value if value in ("call", "put", "both") else "both"


def _normalize_preferences(value: Optional[Iterable[str]]) -> frozenset:
    """`None` means "caller didn't specify" -> the documented default
    (Preferred + Acceptable). An explicit empty iterable is honoured
    literally (shows nothing) -- that is a deliberate caller choice, not
    equivalent to "unset". Unrecognised labels are dropped rather than
    raising (total-function style, matching best_options.py's own
    defensive posture) since a malformed label can never match a row's
    `label` anyway."""
    if value is None:
        return DEFAULT_PREFERENCES
    return frozenset(p for p in value if p in _VALID_PREFERENCES)


def _normalize_symbol_filter(value: Optional[Iterable[str]]) -> Optional[frozenset]:
    """`None` = no symbol filter (every symbol considered). An explicit
    (possibly empty) iterable is a whitelist of tickers to include."""
    if value is None:
        return None
    return frozenset(_normalize_symbol(s) for s in value)


def _passes_min_max(value: Optional[float], lo: Optional[float], hi: Optional[float]) -> bool:
    """`None` on `value` fails any bound that is actually set (see module
    docstring's "absence never satisfies a minimum" rule); an unset bound
    (`lo`/`hi` is `None`) never excludes anything on that side."""
    if lo is not None and (value is None or value < lo):
        return False
    if hi is not None and (value is None or value > hi):
        return False
    return True


def _compute_gap_pct(strike: Optional[float], underlying_price: Optional[float]) -> Optional[float]:
    """Compute signed gap percentage: (strike - underlying_price) / underlying_price * 100.

    Returns None if strike, underlying_price is None, zero, or non-finite.
    The formula preserves sign and is identical for calls/puts.
    """
    import math
    if strike is None or underlying_price is None:
        return None
    if underlying_price == 0 or not math.isfinite(strike) or not math.isfinite(underlying_price):
        return None
    return (strike - underlying_price) / underlying_price * 100.0


def _row_passes_filters(
    row: Dict[str, Any],
    *,
    preferences: frozenset,
    min_annualized_return_pct: Optional[float],
    min_abs_delta: Optional[float],
    max_abs_delta: Optional[float],
    min_dte: Optional[int],
    max_dte: Optional[int],
    min_open_interest: Optional[float],
    min_gap_pct: Optional[float],
    max_gap_pct: Optional[float],
) -> bool:
    if row["label"] not in preferences:
        return False
    if not _passes_min_max(row["annualized_return_pct"], min_annualized_return_pct, None):
        return False
    if not _passes_min_max(row["abs_delta"], min_abs_delta, max_abs_delta):
        return False
    if not _passes_min_max(float(row["dte"]) if row["dte"] is not None else None, min_dte, max_dte):
        return False
    if not _passes_min_max(row["open_interest"], min_open_interest, None):
        return False
    # Gap filter: compute gap_pct from strike and underlying_price, fail if either filter is set and gap is None
    if min_gap_pct is not None or max_gap_pct is not None:
        gap_pct = _compute_gap_pct(row.get("strike"), row.get("underlying_price"))
        if not _passes_min_max(gap_pct, min_gap_pct, max_gap_pct):
            return False
    return True


def _delta_midpoint(thresholds_side: Optional[Mapping[str, Any]]) -> Optional[float]:
    if not isinstance(thresholds_side, Mapping):
        return None
    lo, hi = thresholds_side.get("delta_lo"), thresholds_side.get("delta_hi")
    if not isinstance(lo, (int, float)) or not isinstance(hi, (int, float)):
        return None
    return (lo + hi) / 2.0


def _sort_key(row: Dict[str, Any], midpoint_by_symbol: Dict[str, Optional[float]]):
    """Total order: score desc, DTE asc, category-relative delta fit asc,
    then a fully deterministic tie-breaker (symbol, expiration, strike)
    so two rows are never left ordered only by input/sort-stability
    accident. Mirrors best_options._row_sort_key's None-sorts-last
    convention on every numeric axis."""
    score = row["score"]
    score_key = -score if score is not None else float("inf")
    dte = row["dte"]
    dte_key = float(dte) if dte is not None else float("inf")
    abs_delta = row["abs_delta"]
    midpoint = midpoint_by_symbol.get(row["symbol"])
    delta_key = abs(abs_delta - midpoint) if (abs_delta is not None and midpoint is not None) else float("inf")
    return (
        score_key,
        dte_key,
        delta_key,
        row["symbol"],
        row["expiration"] or "",
        row["strike"] if row["strike"] is not None else float("inf"),
    )


def _memo_key(symbol: str, side: str, entry: Mapping[str, Any]) -> _MemoKey:
    """Build a hashable memo key from symbol and input fields.

    Extracts only the hashable primitives from entry: strings, ints, floats,
    None. If category/calendar dates are dicts (malformed Cosmos data), extract
    the string value or convert to None to prevent 'unhashable type: dict' errors.
    """
    chain = entry.get("chain")
    chain_timestamp = chain.get("timestamp") if isinstance(chain, Mapping) else None

    # Normalize category: if it's a dict, try to extract a string value or use None
    category = entry.get("category")
    if isinstance(category, Mapping):
        # Malformed: category is a dict instead of string
        # Try to extract a string field (e.g. {"type": "balanced"})
        category = category.get("type") or category.get("category") or category.get("name") or None

    # Normalize calendar dates: if dicts, extract date field or use None
    earnings_date = entry.get("next_earnings_date")
    if isinstance(earnings_date, Mapping):
        earnings_date = earnings_date.get("date") or None

    ex_div_date = entry.get("ex_dividend_date")
    if isinstance(ex_div_date, Mapping):
        ex_div_date = ex_div_date.get("date") or None

    # Support level should always be None or a number, but guard anyway
    support = entry.get("support_level")
    if isinstance(support, Mapping):
        support = None

    return (
        symbol,
        side,
        chain_timestamp,
        category,
        entry.get("total_shares", 0),
        earnings_date,
        ex_div_date,
        support,
    )


def _evaluate_symbol(
    symbol: str,
    entry: Mapping[str, Any],
    *,
    side: str,
    now: datetime,
    memo: Dict[_MemoKey, Dict[str, Any]],
    precomputed: Optional[Mapping[str, dict]] = None,
) -> Dict[str, Any]:
    """Runs (or reuses a memoized/precomputed) `evaluate_best_options` call for
    one ready symbol.

    When `precomputed` is supplied and contains an envelope for this symbol,
    returns it directly and never calls `evaluate_best_options`. Otherwise,
    uses this module's own default DTE window -- the screener's `min_dte`/
    `max_dte` are post-filters, never a wider or narrower window passed into
    the per-symbol evaluation itself (see module docstring)."""
    # Precomputed path: return the envelope directly if present
    if precomputed is not None:
        normalized = _normalize_symbol(symbol)
        envelope = precomputed.get(normalized)
        if envelope is not None:
            return envelope

    # Memo path: check if we've already computed this exact key
    key = _memo_key(symbol, side, entry)
    cached = memo.get(key)
    if cached is not None:
        return cached

    # Compute path: call evaluate_best_options
    chain = entry.get("chain") or {}
    result = evaluate_best_options(
        chain,
        side=side,
        category=entry.get("category"),
        total_shares=entry.get("total_shares", 0) or 0,
        next_earnings_date=entry.get("next_earnings_date"),
        ex_dividend_date=entry.get("ex_dividend_date"),
        support_level=entry.get("support_level"),
        dte_min=DEFAULT_DTE_MIN,
        dte_max=DEFAULT_DTE_MAX,
        now=now,
    )
    memo[key] = result
    return result


def _empty_side_section(offset: int, limit: int) -> Dict[str, Any]:
    return {
        "rows": [],
        "nearest_miss": [],
        "pagination": {"offset": offset, "limit": limit, "total_matching": 0, "returned": 0, "has_more": False},
    }


def evaluate_options_screener(
    symbol_inputs: List[Dict[str, Any]],
    *,
    now: datetime,
    side: str = "both",
    preferences: Optional[Iterable[str]] = None,
    symbols: Optional[Iterable[str]] = None,
    min_annualized_return_pct: Optional[float] = None,
    min_abs_delta: Optional[float] = None,
    max_abs_delta: Optional[float] = None,
    min_dte: Optional[int] = None,
    max_dte: Optional[int] = None,
    min_open_interest: Optional[float] = None,
    min_gap_pct: Optional[float] = None,
    max_gap_pct: Optional[float] = None,
    offset: int = 0,
    limit: int = 50,
    memo: Optional[Dict[_MemoKey, Dict[str, Any]]] = None,
    precomputed: Optional[Mapping[str, dict]] = None,
) -> Dict[str, Any]:
    """Aggregate `evaluate_best_options` across every ready symbol in
    `symbol_inputs` into one filtered, sorted, paginated view per side.

    `symbol_inputs`: one dict per symbol --
      `symbol` (str, required), `status` ("ready"|"warming"|"error",
      required), `chain` (dict, optional — required when status == "ready"
      and `precomputed` does not provide an envelope for that symbol),
      `category` (Optional[str]), `total_shares` (int, default 0),
      `next_earnings_date`/`ex_dividend_date` (Optional[str]),
      `support_level` (Optional[float]), `error` (Optional[str], carried
      through verbatim for "error"-status symbols).

    `precomputed`: Optional mapping of normalized symbol → precomputed
      `evaluate_best_options` envelope. When present, the envelope is used
      directly and `evaluate_best_options` is never called. A `status="ready"`
      entry with neither a `precomputed` envelope nor a `chain` is downgraded
      to `"error"` with a synthesised message.

    Total by construction: an entry with an unrecognised `status`, or a
    "ready" entry missing both a precomputed envelope and a usable `chain`,
    is downgraded to an `"error"` contribution (with a synthesised message)
    rather than raising or silently vanishing -- every input symbol is
    accounted for in the returned `symbols` summary.

    Returns a dict with `schema_version`, `generated_at`, `filters`
    (echoing the resolved/effective filter values), `symbols` (a status
    summary: ready/warming/error counts and the error list), and
    `calls`/`puts` sections (each `{"rows", "nearest_miss",
    "pagination"}`) -- the unrequested side (when `side` is `"call"` or
    `"put"`) is a cheap empty placeholder, mirroring
    `evaluate_best_options`'s own side-skipping convention.
    """
    normalized_side = _normalize_side(side)
    sides_to_eval = ("call", "put") if normalized_side == "both" else (normalized_side,)
    resolved_preferences = _normalize_preferences(preferences)
    symbol_filter = _normalize_symbol_filter(symbols)
    offset = max(0, int(offset))
    limit = max(0, int(limit))
    memo_store: Dict[_MemoKey, Dict[str, Any]] = memo if memo is not None else {}

    summary = {"total": 0, "ready": 0, "warming": [], "error": []}
    sections: Dict[str, Dict[str, Any]] = {
        s: {"rows": [], "nearest_miss": []} for s in _SIDES
    }
    midpoint_by_symbol: Dict[str, Dict[str, Optional[float]]] = {s: {} for s in _SIDES}

    for entry in symbol_inputs:
        symbol = _normalize_symbol(entry.get("symbol"))
        if not symbol:
            continue

        if symbol_filter is not None and symbol not in symbol_filter:
            # Excluded from consideration entirely -- not counted into
            # ready/warming/error at all, since the caller asked to look
            # only at a specific symbol subset.
            continue
        summary["total"] += 1

        status = entry.get("status")
        if status not in _VALID_STATUSES:
            status = "error"
            entry = {**entry, "error": entry.get("error") or f"unrecognised status: {entry.get('status')!r}"}

        if status == "warming":
            summary["warming"].append(symbol)
            continue

        # A "ready" entry must have either a precomputed envelope or a usable chain
        normalized = _normalize_symbol(symbol)
        has_precomputed = precomputed is not None and normalized in precomputed
        has_chain = isinstance(entry.get("chain"), Mapping)

        if status == "ready" and not has_precomputed and not has_chain:
            status = "error"
            entry = {**entry, "error": entry.get("error") or "status is 'ready' but no precomputed envelope or usable chain was provided"}

        if status == "error":
            summary["error"].append({"symbol": symbol, "error": entry.get("error")})
            continue

        summary["ready"] += 1
        for s in sides_to_eval:
            result = _evaluate_symbol(symbol, entry, side=s, now=now, memo=memo_store, precomputed=precomputed)
            section = result.get(_SIDE_BUCKET_KEY[s]) or {}
            category_key = ((result.get("parameters") or {}).get("category") or {}).get("value")
            thresholds_side = ((result.get("parameters") or {}).get("thresholds") or {}).get(s)
            midpoint_by_symbol[s][symbol] = _delta_midpoint(thresholds_side)

            rows = section.get("rows") or []
            # Extract underlying price from parameters to include in each row
            underlying_price = ((result.get("parameters") or {}).get("underlying") or {}).get("price")
            for row in rows:
                tagged = dict(row)
                tagged["symbol"] = symbol
                tagged["category"] = category_key
                tagged["underlying_price"] = underlying_price
                sections[s]["rows"].append(tagged)

            if not rows and int(section.get("total") or 0) == 0:
                nearest_miss = section.get("nearest_miss") or {}
                if nearest_miss.get("available"):
                    tagged_miss = dict(nearest_miss)
                    tagged_miss["symbol"] = symbol
                    tagged_miss["category"] = category_key
                    sections[s]["nearest_miss"].append(tagged_miss)

    output_sections: Dict[str, Dict[str, Any]] = {}
    for s in _SIDES:
        if s not in sides_to_eval:
            output_sections[s] = _empty_side_section(offset, limit)
            continue

        filtered = [
            row for row in sections[s]["rows"]
            if _row_passes_filters(
                row,
                preferences=resolved_preferences,
                min_annualized_return_pct=min_annualized_return_pct,
                min_abs_delta=min_abs_delta,
                max_abs_delta=max_abs_delta,
                min_dte=min_dte,
                max_dte=max_dte,
                min_open_interest=min_open_interest,
                min_gap_pct=min_gap_pct,
                max_gap_pct=max_gap_pct,
            )
        ]
        ordered = sorted(filtered, key=lambda r: _sort_key(r, midpoint_by_symbol[s]))
        total_matching = len(ordered)
        page = ordered[offset: offset + limit] if limit > 0 else []
        output_sections[s] = {
            "rows": page,
            "nearest_miss": sections[s]["nearest_miss"],
            "pagination": {
                "offset": offset,
                "limit": limit,
                "total_matching": total_matching,
                "returned": len(page),
                "has_more": (offset + len(page)) < total_matching,
            },
        }

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now.isoformat() if isinstance(now, datetime) else None,
        "filters": {
            "side": normalized_side,
            "preferences": sorted(resolved_preferences),
            "symbols": sorted(symbol_filter) if symbol_filter is not None else None,
            "min_annualized_return_pct": min_annualized_return_pct,
            "min_abs_delta": min_abs_delta,
            "max_abs_delta": max_abs_delta,
            "min_dte": min_dte,
            "max_dte": max_dte,
            "min_open_interest": min_open_interest,
            "min_gap_pct": min_gap_pct,
            "max_gap_pct": max_gap_pct,
            "offset": offset,
            "limit": limit,
        },
        "symbols": summary,
        "calls": output_sections["call"],
        "puts": output_sections["put"],
    }
