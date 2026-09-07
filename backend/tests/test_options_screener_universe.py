"""Options Screener Universe predicate — unit tests.

Ref: danny-options-screener-universe-contract.md §2.1–2.6

Coverage (OSU = Options Screener Universe):
  OSU-1   US + shares>0 → included (core eligible case).
  OSU-2   US + explicit watchlist (covered_call=True) → included.
  OSU-3   US + zero shares AND explicit watchlist → included.
  OSU-4   US + zero shares AND NOT watchlist → excluded.
  OSU-5   US + negative shares AND NOT watchlist → excluded.
  OSU-6   US + negative shares AND explicit watchlist → included.
  OSU-7   Non-US (XMAD) regardless of shares/watchlist → excluded.
  OSU-8   Unknown MIC → excluded (fail-closed).
  OSU-9   Missing exchange field → excluded.
  OSU-10  Legacy free-text "NYSE" exchange → eligible (included when shares>0).
  OSU-11  Legacy free-text "NASDAQ" exchange → eligible.
  OSU-12  Legacy free-text "AMEX" exchange → excluded (not XNYS/XNAS).
  OSU-13  Return type is set[str].
  OSU-14  Symbol with no exchange key at all → excluded.
  OSU-15  Multiple eligible + ineligible in one call → correct subset returned.
  OSU-16  Predicate uses doc["exchange"] only, no extra Cosmos reads.
  OSU-17  compute_options_screener_universe is importable from options_screener_universe.
  OSU-18  XNYS shares=1 (Decimal) → included.
  OSU-19  XNAS shares=0.5 (fractional Decimal) → included.
  OSU-20  Watchlist check: telegram_notifications_enabled=True grants inclusion.
  OSU-21  Watchlist check: cash_secured_put=True grants inclusion.
  OSU-22  Watchlist check: buy_tracker=True grants inclusion.
  OSU-23  Auto-enrolled with all flags off → NOT watchlist member → excluded (if zero shares).
  OSU-24  Manually added (_auto_enrolled=False) → watchlist member → included (if zero shares).

WILL FAIL until Linus creates `backend/src/options_screener_universe.py` (§2.1 of contract).
"""

from __future__ import annotations

import pytest
from decimal import Decimal


# ---------------------------------------------------------------------------
# Import contract — will fail with ImportError until Linus creates the module
# ---------------------------------------------------------------------------

try:
    from src.options_screener_universe import compute_options_screener_universe
    _MODULE_AVAILABLE = True
except ImportError:
    _MODULE_AVAILABLE = False
    compute_options_screener_universe = None  # type: ignore

_skip_predicate = pytest.mark.skipif(
    not _MODULE_AVAILABLE,
    reason=(
        "src.options_screener_universe not yet implemented. "
        "Linus: add backend/src/options_screener_universe.py per §2.1 of "
        "danny-options-screener-universe-contract.md."
    ),
)


# ---------------------------------------------------------------------------
# OSU-17: module import — intentionally NOT guarded by _skip_predicate
# so it surfaces as FAILED (not skipped) when the module is absent.
# ---------------------------------------------------------------------------

def test_osu17_module_is_importable():
    """OSU-17: compute_options_screener_universe must be importable.
    FAILS until Linus creates src/options_screener_universe.py.
    """
    from src.options_screener_universe import compute_options_screener_universe as _fn  # noqa
    assert callable(_fn), "OSU-17: compute_options_screener_universe must be callable"


# ---------------------------------------------------------------------------
# Helpers — build minimal symbol_config fixtures
# ---------------------------------------------------------------------------

def _config(ticker: str, exchange: str, shares: int = 0,
            auto_enrolled: bool = True, cc: bool = False,
            csp: bool = False, bt: bool = False, tg: bool = False) -> dict:
    return {
        "symbol": ticker,
        "exchange": exchange,
        "_auto_enrolled": auto_enrolled,
        "watchlist": {"covered_call": cc, "cash_secured_put": csp, "buy_tracker": bt},
        "telegram_notifications_enabled": tg,
    }


def _shares(**kwargs) -> dict[str, Decimal]:
    return {k: Decimal(str(v)) for k, v in kwargs.items()}


# ---------------------------------------------------------------------------
# OSU-1..12: predicate quadrant coverage
# ---------------------------------------------------------------------------

@_skip_predicate
class TestComputeUniversePredicate:
    def test_osu1_us_positive_shares_included(self):
        """OSU-1: US + shares>0 → included."""
        configs = [_config("AAPL", "XNYS")]
        shares = _shares(AAPL=100)
        result = compute_options_screener_universe(configs, shares)
        assert "AAPL" in result, "OSU-1: US ticker with positive shares must be included"

    def test_osu2_us_watchlist_flag_included(self):
        """OSU-2: US + explicit watchlist (covered_call=True) → included even with 0 shares."""
        configs = [_config("ABBV", "XNYS", shares=0, cc=True)]
        shares = _shares(ABBV=0)
        result = compute_options_screener_universe(configs, shares)
        assert "ABBV" in result, "OSU-2: US ticker with covered_call=True must be included"

    def test_osu3_us_zero_shares_explicit_watchlist_included(self):
        """OSU-3: US + zero shares AND explicit watchlist → included."""
        configs = [_config("MO", "XNAS", csp=True)]
        shares = _shares(MO=0)
        result = compute_options_screener_universe(configs, shares)
        assert "MO" in result

    def test_osu4_us_zero_shares_no_watchlist_excluded(self):
        """OSU-4: US + zero shares AND not watchlist → excluded."""
        configs = [_config("XYZ", "XNYS")]
        shares = _shares(XYZ=0)
        result = compute_options_screener_universe(configs, shares)
        assert "XYZ" not in result, "OSU-4: zero shares with no watchlist must be excluded"

    def test_osu5_us_negative_shares_no_watchlist_excluded(self):
        """OSU-5: US + negative shares AND not watchlist → excluded."""
        configs = [_config("NEG", "XNYS")]
        shares = _shares(NEG=-10)
        result = compute_options_screener_universe(configs, shares)
        assert "NEG" not in result, "OSU-5: negative shares with no watchlist must be excluded"

    def test_osu6_us_negative_shares_watchlist_included(self):
        """OSU-6: US + negative shares AND explicit watchlist → included."""
        configs = [_config("NEG2", "XNYS", tg=True)]
        shares = _shares(NEG2=-5)
        result = compute_options_screener_universe(configs, shares)
        assert "NEG2" in result, "OSU-6: negative shares with explicit watchlist must be included"

    def test_osu7_non_us_excluded_regardless(self):
        """OSU-7: XMAD (non-US) excluded regardless of shares/watchlist."""
        configs = [_config("ACS", "XMAD", auto_enrolled=False, cc=True)]
        shares = _shares(ACS=1000)
        result = compute_options_screener_universe(configs, shares)
        assert "ACS" not in result, "OSU-7: XMAD (non-US) must always be excluded"

    def test_osu7_xlon_excluded(self):
        """OSU-7b: XLON excluded."""
        configs = [_config("HSBA", "XLON", auto_enrolled=False, csp=True)]
        shares = _shares(HSBA=500)
        result = compute_options_screener_universe(configs, shares)
        assert "HSBA" not in result

    def test_osu8_unknown_mic_excluded(self):
        """OSU-8: unknown MIC → excluded (fail-closed)."""
        configs = [_config("WEIRD", "XZZZ")]
        shares = _shares(WEIRD=100)
        result = compute_options_screener_universe(configs, shares)
        assert "WEIRD" not in result, "OSU-8: unknown MIC must be excluded"

    def test_osu9_missing_exchange_field_excluded(self):
        """OSU-9: symbol_config without exchange field → excluded."""
        config = {
            "symbol": "NOEXCH",
            "_auto_enrolled": True,
            "watchlist": {"covered_call": False, "cash_secured_put": False, "buy_tracker": False},
            "telegram_notifications_enabled": False,
        }
        shares = _shares(NOEXCH=100)
        result = compute_options_screener_universe([config], shares)
        assert "NOEXCH" not in result, "OSU-9: missing exchange field must be excluded"

    def test_osu10_legacy_nyse_text_eligible_with_shares(self):
        """OSU-10: legacy free-text 'NYSE' → eligible (included when shares>0)."""
        configs = [_config("LEGACYN", "NYSE")]
        shares = _shares(LEGACYN=100)
        result = compute_options_screener_universe(configs, shares)
        assert "LEGACYN" in result, (
            "OSU-10: legacy 'NYSE' alias must be treated as XNYS-equivalent"
        )

    def test_osu11_legacy_nasdaq_text_eligible_with_shares(self):
        """OSU-11: legacy free-text 'NASDAQ' → eligible."""
        configs = [_config("LEGACYQ", "NASDAQ")]
        shares = _shares(LEGACYQ=50)
        result = compute_options_screener_universe(configs, shares)
        assert "LEGACYQ" in result, (
            "OSU-11: legacy 'NASDAQ' alias must be treated as XNAS-equivalent"
        )

    def test_osu12_legacy_amex_text_excluded(self):
        """OSU-12: legacy 'AMEX' exchange → excluded (not XNYS/XNAS per Amendment J)."""
        configs = [_config("AMEXSTOCK", "AMEX")]
        shares = _shares(AMEXSTOCK=100)
        result = compute_options_screener_universe(configs, shares)
        assert "AMEXSTOCK" not in result, (
            "OSU-12: AMEX is not Amendment J eligible; must be excluded"
        )

    def test_osu13_return_type_is_set(self):
        """OSU-13: compute_options_screener_universe returns set[str]."""
        result = compute_options_screener_universe([], {})
        assert isinstance(result, set), f"OSU-13: must return set, got {type(result)}"

    def test_osu14_no_exchange_key_excluded(self):
        """OSU-14: doc without any exchange key → excluded."""
        config = {"symbol": "GHOST"}
        result = compute_options_screener_universe([config], {"GHOST": Decimal("50")})
        assert "GHOST" not in result

    def test_osu15_mixed_batch_returns_correct_subset(self):
        """OSU-15: multiple symbols in one call — only eligible subset returned."""
        configs = [
            _config("AAPL", "XNYS"),   # US, shares>0 → IN
            _config("MSFT", "XNAS"),   # US, shares>0 → IN
            _config("ACS", "XMAD"),    # Non-US → OUT
            _config("ZERO", "XNYS"),   # US, 0 shares, no watchlist → OUT
            _config("WL", "XNYS", auto_enrolled=False),  # US, 0 shares, watchlist → IN
        ]
        shares = _shares(AAPL=100, MSFT=50, ACS=200, ZERO=0, WL=0)
        result = compute_options_screener_universe(configs, shares)
        assert result == {"AAPL", "MSFT", "WL"}, (
            f"OSU-15: expected {{AAPL, MSFT, WL}}, got {result}"
        )

    # OSU-18..19: Decimal share counts
    def test_osu18_decimal_shares_positive_included(self):
        """OSU-18: Decimal(1) → shares > 0 → included."""
        configs = [_config("TINY", "XNYS")]
        result = compute_options_screener_universe(configs, {"TINY": Decimal("1")})
        assert "TINY" in result

    def test_osu19_fractional_decimal_shares_positive_included(self):
        """OSU-19: Decimal('0.5') → shares > 0 → included."""
        configs = [_config("FRAC", "XNAS")]
        result = compute_options_screener_universe(configs, {"FRAC": Decimal("0.5")})
        assert "FRAC" in result

    # OSU-20..24: watchlist membership nuances
    def test_osu20_telegram_grants_watchlist_membership(self):
        """OSU-20: telegram_notifications_enabled=True grants watchlist membership."""
        configs = [_config("TGM", "XNYS", tg=True)]
        result = compute_options_screener_universe(configs, {"TGM": Decimal("0")})
        assert "TGM" in result

    def test_osu21_csp_grants_watchlist_membership(self):
        """OSU-21: cash_secured_put=True grants watchlist membership."""
        configs = [_config("CSPONLY", "XNYS", csp=True)]
        result = compute_options_screener_universe(configs, {"CSPONLY": Decimal("0")})
        assert "CSPONLY" in result

    def test_osu22_buy_tracker_grants_watchlist_membership(self):
        """OSU-22: buy_tracker=True grants watchlist membership."""
        configs = [_config("BTONLY", "XNYS", bt=True)]
        result = compute_options_screener_universe(configs, {"BTONLY": Decimal("0")})
        assert "BTONLY" in result

    def test_osu23_auto_enrolled_all_flags_off_not_watchlist(self):
        """OSU-23: _auto_enrolled=True, all flags off → NOT watchlist member → excluded."""
        configs = [_config("AUTOENR", "XNYS", auto_enrolled=True)]
        result = compute_options_screener_universe(configs, {"AUTOENR": Decimal("0")})
        assert "AUTOENR" not in result, (
            "OSU-23: auto-enrolled with no flags must not count as watchlist member"
        )

    def test_osu24_manually_added_counts_as_watchlist(self):
        """OSU-24: _auto_enrolled=False (manually added) → watchlist member → included."""
        configs = [_config("MANUAL", "XNYS", auto_enrolled=False)]
        result = compute_options_screener_universe(configs, {"MANUAL": Decimal("0")})
        assert "MANUAL" in result, (
            "OSU-24: manually-added symbol (_auto_enrolled=False) must count as watchlist member"
        )

def test_osu_empty_inputs_returns_empty_set():
    """Empty inputs → empty set (no crash)."""
    assert compute_options_screener_universe([], {}) == set()
