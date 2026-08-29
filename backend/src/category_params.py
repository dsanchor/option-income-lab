"""Single category normaliser and threshold accessor.

Design: `.squad/decisions/inbox/danny-best-options-design.md` §7 ("NEW
backend/src/category_params.py — single normaliser and threshold
accessor"). This module exists to close finding **F9**: `agent_runner.py`
keys its own category tables on space-form strings ("high yield") while
`rule_evaluator.py` keys on underscore form plus aliases, and stored
enrichment is Title+space ("High Yield"). `normalize_category` is the one
place both `best_options.py` (this task) and `agent_runner.py` (Rusty's
adoption, same design doc) are meant to call, so that divergence has
nowhere left to reappear.

Thresholds are never redefined here. `thresholds_for` reads verbatim from
`rule_evaluator.CATEGORY_THRESHOLDS_CC` / `CATEGORY_THRESHOLDS_CSP` — the
single source of truth sourced from `src/skills/*/SKILL.md` — and only
reshapes the lookup by (strategy, normalized category). Per the design's
own instruction, `rule_evaluator.py` itself is left untouched (its public
names/tests are unaffected); this module does not import its private
`_normalize_category`/`_CATEGORY_ALIASES`, since those are private to that
module, but mirrors the same alias table so both normalisers agree on
every input `rule_evaluator` already agrees with itself on.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from src.rule_evaluator import CATEGORY_THRESHOLDS_CC, CATEGORY_THRESHOLDS_CSP

DEFAULT_CATEGORY = "balanced"

# Strategy key -> verbatim threshold table. "covered_call"/"cash_secured_put"
# match rule_evaluator.CATEGORY_THRESHOLDS's own top-level strategy keys.
_STRATEGY_THRESHOLDS: Dict[str, Dict[str, Dict[str, Any]]] = {
    "covered_call": CATEGORY_THRESHOLDS_CC,
    "cash_secured_put": CATEGORY_THRESHOLDS_CSP,
}

# Mirrors rule_evaluator._CATEGORY_ALIASES verbatim (that name is private to
# rule_evaluator's own module, so it is reproduced here rather than imported
# — see module docstring).
_CATEGORY_ALIASES = {
    "high-yield": "high_yield",
    "highyield": "high_yield",
    "rising-star": "rising_star",
    "risingstar": "rising_star",
}


def resolve_category(raw: Optional[str]) -> Tuple[str, bool]:
    """Normalise `raw` to a canonical `CATEGORY_THRESHOLDS_*` key and report
    whether the result is a *guess*.

    Returns `(category_key, defaulted)`. `defaulted` is `True` only when
    `raw` was missing/blank/unrecognised and we had to fall back to
    `"balanced"` — an explicit `raw == "balanced"` (in any of underscore,
    space, or Title-case form) is not a guess and reports `defaulted=False`.
    This is the flag the Best Options parameters panel must surface
    (design §6: "the user must see when we are guessing about the stock's
    profile"). Total: never raises regardless of `raw`'s shape.
    """
    if not raw or not str(raw).strip():
        return DEFAULT_CATEGORY, True
    key = str(raw).strip().lower().replace(" ", "_")
    key = _CATEGORY_ALIASES.get(key, key)
    if key not in CATEGORY_THRESHOLDS_CC:
        return DEFAULT_CATEGORY, True
    return key, False


def normalize_category(raw: Optional[str]) -> str:
    """Normalise `raw` to a canonical `CATEGORY_THRESHOLDS_*` key, defaulting
    to `"balanced"` for `None`/blank/unrecognised input. Convenience wrapper
    over `resolve_category` for callers (e.g. `agent_runner`) that don't
    need the `defaulted` provenance flag. Total: never raises."""
    return resolve_category(raw)[0]


def category_label(category_key: str) -> str:
    """Human-readable label for an already-normalized key, e.g.
    `"high_yield"` -> `"High Yield"`. Mirrors
    `rule_evaluator._category_label` (private to that module)."""
    return str(category_key).replace("_", " ").title()


def thresholds_for(strategy: str, category: Optional[str]) -> Dict[str, Any]:
    """Return the `{delta_lo, delta_hi, premium_min_pct, premium_wait_pct,
    iv_rank_min}` threshold dict for `strategy` (`"covered_call"` or
    `"cash_secured_put"`) and `category` (any raw form; normalised here),
    verbatim from `rule_evaluator.CATEGORY_THRESHOLDS_CC`/`_CSP` — never
    redefined in this module.

    Raises `ValueError` for an unknown `strategy` (a programmer error, not
    a data-quality question `resolve_category`'s `defaulted` flag already
    covers) — every other input is normalised, never raises.
    """
    table = _STRATEGY_THRESHOLDS.get(strategy)
    if table is None:
        raise ValueError(
            f"unknown strategy {strategy!r}; expected one of {sorted(_STRATEGY_THRESHOLDS)}"
        )
    category_key = normalize_category(category)
    return dict(table[category_key])