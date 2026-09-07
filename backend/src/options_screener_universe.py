"""Options Screener universe predicate — single source of truth.

Contract: danny-options-screener-universe-contract.md §2.1

Business-logic layer, importable by both ``backend/web/app.py`` (manual
endpoint) and ``backend/src/main.py`` (scheduled job) without circular
imports — neither of those modules imports this one's dependents nor is
imported by them today.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Dict, Iterable, Set

from src.us_exchange_eligibility import is_us_options_eligible
from src.portfolio.watchlist_membership import is_watchlist_member

# Legacy free-text exchange labels pre-dating the security_master/MIC
# rollout that are unambiguously NYSE/NASDAQ-equivalent for US-options
# eligibility purposes (Amendment J / danny-unified-watchlist-contract.md
# §J.1). Deliberately excludes "AMEX" — Amendment J restricts eligibility
# to XNYS/XNAS only, so a legacy AMEX-labelled symbol must still fail
# closed, exactly like `is_us_options_eligible` does for a real AMEX MIC.
# This is a narrow, scoped-down normalization (2 entries), not a second
# suffix/mapping table — `is_us_options_eligible` itself is imported
# unchanged, per contract.
_LEGACY_US_EXCHANGE_TO_MIC = {"NYSE": "XNYS", "NASDAQ": "XNAS"}


def _resolve_eligibility_mic(raw_exchange: object) -> str | None:
    """Normalize a symbol_config's raw `exchange` value for the
    `is_us_options_eligible` check, folding in the two unambiguous legacy
    free-text aliases (see `_LEGACY_US_EXCHANGE_TO_MIC`)."""
    if not raw_exchange or not isinstance(raw_exchange, str):
        return None
    mic = raw_exchange.strip().upper()
    return _LEGACY_US_EXCHANGE_TO_MIC.get(mic, mic)


def compute_options_screener_universe(
    symbol_configs: Iterable[dict],
    portfolio_shares_by_ticker: Dict[str, Decimal],
) -> Set[str]:
    """Return the set of tickers eligible for the Options Screener universe.

    A symbol is included iff:
      1. ``is_us_options_eligible(doc-resolved MIC)`` is True, AND
      2. ``portfolio_shares_by_ticker.get(ticker, 0) > 0``
         OR ``is_watchlist_member(doc)`` is True

    MIC is resolved from ``doc["exchange"]`` only (no security_master
    lookup — avoids N+1; this is safe/correct for the canonical population,
    where ``ensure_symbol_config`` already writes the real MIC into
    ``exchange`` at config-creation time, and directive-compliant
    fail-closed for legacy stragglers whose ``exchange`` is not a MIC at
    all — ``is_us_options_eligible`` fails closed on those).

    Unknown/missing MIC → not eligible (fail-closed), matching
    ``is_us_options_eligible``'s own contract.

    Shares comparison is strict ``> 0`` (Decimal); zero and negative shares
    (data anomaly) never grant universe membership on their own — only
    explicit Watchlist membership does for a non-held/zero/negative symbol.

    Args:
        symbol_configs: iterable of ``symbol_config`` documents (as
            returned by ``cosmos.list_symbols()``).
        portfolio_shares_by_ticker: ticker (uppercase) -> current share
            count (``Decimal``), typically derived from one
            ``HoldingsService.compute_holdings()`` call.

    Returns:
        Set of uppercase tickers admitted to the Options Screener universe.
    """
    universe: Set[str] = set()
    for doc in symbol_configs:
        ticker = (doc.get("symbol") or "").strip().upper()
        if not ticker:
            continue

        mic = _resolve_eligibility_mic(doc.get("exchange"))
        if not is_us_options_eligible(mic):
            continue

        shares = portfolio_shares_by_ticker.get(ticker, Decimal("0"))
        if not isinstance(shares, Decimal):
            try:
                shares = Decimal(str(shares))
            except Exception:
                shares = Decimal("0")

        if shares > 0 or is_watchlist_member(doc):
            universe.add(ticker)

    return universe
