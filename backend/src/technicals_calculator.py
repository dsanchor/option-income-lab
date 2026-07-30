"""Technical indicators and signal generation from OHLCV data.

Computes oscillators and moving averages, then generates Buy/Sell/Neutral
signals using the same logic as tv_data_fetcher.py.  Uses pandas-ta when
available; falls back to manual pandas/numpy computation.
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

try:
    import pandas_ta as ta
    _HAS_PANDAS_TA = True
except ImportError:
    _HAS_PANDAS_TA = False
    logger.info("pandas-ta not installed — using manual indicator computation")


# ======================================================================
# Display layout (matches tv_data_fetcher.py exactly)
# ======================================================================

_OSCILLATOR_DISPLAY = [
    ("RSI", "RSI (14)"),
    ("Stoch.K", "Stochastic %K (14,3,3)"),
    ("CCI20", "CCI (20)"),
    ("ADX", "ADX (14)"),
    ("AO", "Awesome Oscillator"),
    ("Mom", "Momentum (10)"),
    ("MACD.macd", "MACD Level (12,26)"),
    ("W.R", "Williams %R (14)"),
    ("BBPower", "Bull Bear Power"),
    ("UO", "Ultimate Oscillator (7,14,28)"),
]

_MA_DISPLAY = [
    ("EMA10", "EMA (10)"), ("SMA10", "SMA (10)"),
    ("EMA20", "EMA (20)"), ("SMA20", "SMA (20)"),
    ("EMA30", "EMA (30)"), ("SMA30", "SMA (30)"),
    ("EMA50", "EMA (50)"), ("SMA50", "SMA (50)"),
    ("EMA100", "EMA (100)"), ("SMA100", "SMA (100)"),
    ("EMA200", "EMA (200)"), ("SMA200", "SMA (200)"),
    ("Ichimoku.BLine", "Ichimoku Base Line (9,26,52,26)"),
    ("VWMA", "VWMA (20)"), ("HullMA9", "Hull MA (9)"),
]


# ======================================================================
# Signal logic — ported exactly from tv_data_fetcher.py
# ======================================================================

def _oscillator_signal(key: str, sym: dict) -> str:
    v = sym.get(key)
    if v is None:
        return "Neutral"
    if key == "RSI":
        prev = sym.get("RSI[1]")
        if prev is not None:
            if v < 30 and v > prev:
                return "Buy"
            if v > 70 and v < prev:
                return "Sell"
        return "Neutral"
    if key == "Stoch.K":
        k, d = sym.get("Stoch.K"), sym.get("Stoch.D")
        k1, d1 = sym.get("Stoch.K[1]"), sym.get("Stoch.D[1]")
        if None not in (k, d, k1, d1):
            if k < 20 and d < 20 and k > d and k1 < d1:
                return "Buy"
            if k > 80 and d > 80 and k < d and k1 > d1:
                return "Sell"
        return "Neutral"
    if key == "CCI20":
        prev = sym.get("CCI20[1]")
        if prev is not None:
            if v < -100 and v > prev:
                return "Buy"
            if v > 100 and v < prev:
                return "Sell"
        return "Neutral"
    if key == "ADX":
        adx = sym.get("ADX")
        plus_di, minus_di = sym.get("ADX+DI"), sym.get("ADX-DI")
        plus_di1, minus_di1 = sym.get("ADX+DI[1]"), sym.get("ADX-DI[1]")
        if None not in (adx, plus_di, minus_di, plus_di1, minus_di1) and adx > 20:
            if plus_di > minus_di and plus_di1 < minus_di1:
                return "Buy"
            if plus_di < minus_di and plus_di1 > minus_di1:
                return "Sell"
        return "Neutral"
    if key == "AO":
        ao, ao1, ao2 = sym.get("AO"), sym.get("AO[1]"), sym.get("AO[2]")
        if None not in (ao, ao1):
            if (ao > 0 and ao1 < 0) or (ao2 is not None and ao > 0 and ao > ao1 and ao1 < ao2):
                return "Buy"
            if (ao < 0 and ao1 > 0) or (ao2 is not None and ao < 0 and ao < ao1 and ao1 > ao2):
                return "Sell"
        return "Neutral"
    if key == "Mom":
        prev = sym.get("Mom[1]")
        if prev is not None:
            return "Buy" if v > prev else ("Sell" if v < prev else "Neutral")
        return "Neutral"
    if key == "MACD.macd":
        signal = sym.get("MACD.signal")
        if signal is not None:
            return "Buy" if v > signal else ("Sell" if v < signal else "Neutral")
        return "Neutral"
    if key == "W.R":
        if v < -80:
            return "Buy"
        if v > -20:
            return "Sell"
        return "Neutral"
    if key == "BBPower":
        return "Buy" if v > 0 else ("Sell" if v < 0 else "Neutral")
    if key == "UO":
        if v < 30:
            return "Buy"
        if v > 70:
            return "Sell"
        return "Neutral"
    return "Neutral"


def _ma_signal(key: str, ma_val, close) -> str:
    if ma_val is None or close is None:
        return "Neutral"
    if close > ma_val:
        return "Buy"
    if close < ma_val:
        return "Sell"
    return "Neutral"


def _count_signals(signals: list) -> Tuple[int, int, int]:
    buy = sum(1 for s in signals if s == "Buy")
    sell = sum(1 for s in signals if s == "Sell")
    neutral = sum(1 for s in signals if s == "Neutral")
    return buy, sell, neutral


def _tech_recommendation_label(value) -> str:
    if value is None:
        return "N/A"
    if value >= 0.5:
        return "Strong Buy"
    if value > 0.1:
        return "Buy"
    if value >= -0.1:
        return "Neutral"
    if value > -0.5:
        return "Sell"
    return "Strong Sell"


def _format_tech_value(key: str, value):
    if value is None:
        return "N/A"
    if key in ("RSI", "Stoch.K", "CCI20", "ADX", "W.R", "UO"):
        return f"{value:.2f}"
    if key in ("AO", "Mom", "MACD.macd", "BBPower"):
        return f"{value:.4f}"
    if key.startswith(("EMA", "SMA", "VWMA", "HullMA", "Ichimoku")):
        return f"${value:,.2f}"
    return str(value)


# ======================================================================
# Manual indicator computation helpers
# ======================================================================

def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def _sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period, min_periods=period).mean()


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def _stochastic(high: pd.Series, low: pd.Series, close: pd.Series,
                k_period: int = 14, d_period: int = 3) -> Tuple[pd.Series, pd.Series]:
    lowest = low.rolling(window=k_period, min_periods=k_period).min()
    highest = high.rolling(window=k_period, min_periods=k_period).max()
    k = 100.0 * (close - lowest) / (highest - lowest)
    d = k.rolling(window=d_period, min_periods=d_period).mean()
    return k, d


def _cci(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 20) -> pd.Series:
    tp = (high + low + close) / 3.0
    sma_tp = tp.rolling(window=period, min_periods=period).mean()
    mad = tp.rolling(window=period, min_periods=period).apply(lambda x: np.mean(np.abs(x - np.mean(x))), raw=True)
    return (tp - sma_tp) / (0.015 * mad)


def _adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14):
    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    atr = tr.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    plus_di = 100.0 * plus_dm.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean() / atr
    minus_di = 100.0 * minus_dm.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean() / atr

    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    adx_val = dx.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    return adx_val, plus_di, minus_di


def _awesome_oscillator(high: pd.Series, low: pd.Series) -> pd.Series:
    mid = (high + low) / 2.0
    return _sma(mid, 5) - _sma(mid, 34)


def _momentum(close: pd.Series, period: int = 10) -> pd.Series:
    return close - close.shift(period)


def _macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = _ema(close, fast)
    ema_slow = _ema(close, slow)
    macd_line = ema_fast - ema_slow
    signal_line = _ema(macd_line, signal)
    return macd_line, signal_line


def _williams_r(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    highest = high.rolling(window=period, min_periods=period).max()
    lowest = low.rolling(window=period, min_periods=period).min()
    return -100.0 * (highest - close) / (highest - lowest)


def _ultimate_oscillator(high: pd.Series, low: pd.Series, close: pd.Series,
                         p1: int = 7, p2: int = 14, p3: int = 28) -> pd.Series:
    prev_close = close.shift(1)
    bp = close - pd.concat([low, prev_close], axis=1).min(axis=1)
    tr = pd.concat([high, prev_close], axis=1).max(axis=1) - pd.concat([low, prev_close], axis=1).min(axis=1)

    avg1 = bp.rolling(p1).sum() / tr.rolling(p1).sum()
    avg2 = bp.rolling(p2).sum() / tr.rolling(p2).sum()
    avg3 = bp.rolling(p3).sum() / tr.rolling(p3).sum()
    return 100.0 * (4 * avg1 + 2 * avg2 + avg3) / 7.0


def _vwma(close: pd.Series, volume: pd.Series, period: int = 20) -> pd.Series:
    return (close * volume).rolling(period).sum() / volume.rolling(period).sum()


def _hull_ma(close: pd.Series, period: int = 9) -> pd.Series:
    half_period = period // 2
    sqrt_period = int(np.sqrt(period))
    wma_half = close.ewm(span=half_period, adjust=False).mean()
    wma_full = close.ewm(span=period, adjust=False).mean()
    diff = 2 * wma_half - wma_full
    return diff.ewm(span=sqrt_period, adjust=False).mean()


def _ichimoku_base(high: pd.Series, low: pd.Series, period: int = 26) -> pd.Series:
    return (high.rolling(period).max() + low.rolling(period).min()) / 2.0


def _safe_val(series: pd.Series, offset: int = -1):
    """Return scalar from series at offset, or None if out of bounds / NaN."""
    try:
        v = series.iloc[offset]
        return float(v) if pd.notna(v) else None
    except (IndexError, KeyError):
        return None


# ======================================================================
# Main class
# ======================================================================

class TechnicalsCalculator:
    """Compute all technical indicators from OHLCV data and generate signals."""

    def compute_all(self, history: pd.DataFrame) -> dict:
        """Compute all technicals from a yfinance history DataFrame.

        Input: DataFrame with columns Open, High, Low, Close, Volume
        Returns dict matching the structure that _build_technicals_dict() produces.
        """
        if history is None or history.empty or len(history) < 30:
            logger.warning("Insufficient history data for technicals (%d bars)",
                           0 if history is None else len(history))
            return self._empty_technicals()

        close = history["Close"]
        high = history["High"]
        low = history["Low"]
        volume = history["Volume"]

        sym = self._compute_indicators(close, high, low, volume)
        return self._build_technicals_dict(sym)

    def _compute_indicators(self, close, high, low, volume) -> dict:
        """Compute all raw indicator values into a flat dict matching TV keys."""
        sym: Dict[str, Any] = {"close": _safe_val(close)}

        if _HAS_PANDAS_TA:
            sym.update(self._compute_with_pandas_ta(close, high, low, volume))
        else:
            sym.update(self._compute_manual(close, high, low, volume))

        return sym

    def _compute_with_pandas_ta(self, close, high, low, volume) -> dict:
        vals: Dict[str, Any] = {}
        df = pd.DataFrame({"open": close, "high": high, "low": low, "close": close, "volume": volume})

        # RSI
        rsi = ta.rsi(close, length=14)
        if rsi is not None:
            vals["RSI"] = _safe_val(rsi)
            vals["RSI[1]"] = _safe_val(rsi, -2)

        # Stochastic
        stoch = ta.stoch(high, low, close, k=14, d=3, smooth_k=3)
        if stoch is not None and len(stoch.columns) >= 2:
            k_col, d_col = stoch.columns[0], stoch.columns[1]
            vals["Stoch.K"] = _safe_val(stoch[k_col])
            vals["Stoch.D"] = _safe_val(stoch[d_col])
            vals["Stoch.K[1]"] = _safe_val(stoch[k_col], -2)
            vals["Stoch.D[1]"] = _safe_val(stoch[d_col], -2)

        # CCI
        cci_s = ta.cci(high, low, close, length=20)
        if cci_s is not None:
            vals["CCI20"] = _safe_val(cci_s)
            vals["CCI20[1]"] = _safe_val(cci_s, -2)

        # ADX
        adx_df = ta.adx(high, low, close, length=14)
        if adx_df is not None and len(adx_df.columns) >= 3:
            adx_col = [c for c in adx_df.columns if "ADX" in c and "DM" not in c][0]
            dmp_col = [c for c in adx_df.columns if "DMP" in c][0]
            dmn_col = [c for c in adx_df.columns if "DMN" in c][0]
            vals["ADX"] = _safe_val(adx_df[adx_col])
            vals["ADX+DI"] = _safe_val(adx_df[dmp_col])
            vals["ADX-DI"] = _safe_val(adx_df[dmn_col])
            vals["ADX+DI[1]"] = _safe_val(adx_df[dmp_col], -2)
            vals["ADX-DI[1]"] = _safe_val(adx_df[dmn_col], -2)

        # Awesome Oscillator
        ao_s = ta.ao(high, low, fast=5, slow=34)
        if ao_s is not None:
            vals["AO"] = _safe_val(ao_s)
            vals["AO[1]"] = _safe_val(ao_s, -2)
            vals["AO[2]"] = _safe_val(ao_s, -3)

        # Momentum
        mom = ta.mom(close, length=10)
        if mom is not None:
            vals["Mom"] = _safe_val(mom)
            vals["Mom[1]"] = _safe_val(mom, -2)

        # MACD
        macd_df = ta.macd(close, fast=12, slow=26, signal=9)
        if macd_df is not None and len(macd_df.columns) >= 2:
            macd_col = [c for c in macd_df.columns if "MACD_" in c and "h" not in c.lower() and "s" not in c.lower()][0]
            signal_col = [c for c in macd_df.columns if "MACDs" in c or "SIGNAL" in c.upper()][0]
            vals["MACD.macd"] = _safe_val(macd_df[macd_col])
            vals["MACD.signal"] = _safe_val(macd_df[signal_col])

        # Williams %R
        wr = ta.willr(high, low, close, length=14)
        if wr is not None:
            vals["W.R"] = _safe_val(wr)

        # Bull Bear Power
        ema13 = ta.ema(close, length=13)
        if ema13 is not None:
            bb_power = close - ema13
            vals["BBPower"] = _safe_val(bb_power)

        # Ultimate Oscillator
        uo = ta.uo(high, low, close, fast=7, medium=14, slow=28)
        if uo is not None:
            vals["UO"] = _safe_val(uo)

        # Moving Averages
        for period in [10, 20, 30, 50, 100, 200]:
            sma_s = ta.sma(close, length=period)
            ema_s = ta.ema(close, length=period)
            if sma_s is not None:
                vals[f"SMA{period}"] = _safe_val(sma_s)
            if ema_s is not None:
                vals[f"EMA{period}"] = _safe_val(ema_s)

        # Ichimoku Base Line
        ich = _ichimoku_base(high, low, 26)
        vals["Ichimoku.BLine"] = _safe_val(ich)

        # VWMA
        vwma_s = ta.vwma(close, volume, length=20)
        if vwma_s is not None:
            vals["VWMA"] = _safe_val(vwma_s)

        # Hull MA
        hma = ta.hma(close, length=9)
        if hma is not None:
            vals["HullMA9"] = _safe_val(hma)

        return vals

    def _compute_manual(self, close, high, low, volume) -> dict:
        vals: Dict[str, Any] = {}

        # RSI
        rsi = _rsi(close, 14)
        vals["RSI"] = _safe_val(rsi)
        vals["RSI[1]"] = _safe_val(rsi, -2)

        # Stochastic
        stoch_k, stoch_d = _stochastic(high, low, close, 14, 3)
        vals["Stoch.K"] = _safe_val(stoch_k)
        vals["Stoch.D"] = _safe_val(stoch_d)
        vals["Stoch.K[1]"] = _safe_val(stoch_k, -2)
        vals["Stoch.D[1]"] = _safe_val(stoch_d, -2)

        # CCI
        cci_s = _cci(high, low, close, 20)
        vals["CCI20"] = _safe_val(cci_s)
        vals["CCI20[1]"] = _safe_val(cci_s, -2)

        # ADX
        adx_s, plus_di, minus_di = _adx(high, low, close, 14)
        vals["ADX"] = _safe_val(adx_s)
        vals["ADX+DI"] = _safe_val(plus_di)
        vals["ADX-DI"] = _safe_val(minus_di)
        vals["ADX+DI[1]"] = _safe_val(plus_di, -2)
        vals["ADX-DI[1]"] = _safe_val(minus_di, -2)

        # Awesome Oscillator
        ao_s = _awesome_oscillator(high, low)
        vals["AO"] = _safe_val(ao_s)
        vals["AO[1]"] = _safe_val(ao_s, -2)
        vals["AO[2]"] = _safe_val(ao_s, -3)

        # Momentum
        mom = _momentum(close, 10)
        vals["Mom"] = _safe_val(mom)
        vals["Mom[1]"] = _safe_val(mom, -2)

        # MACD
        macd_line, signal_line = _macd(close, 12, 26, 9)
        vals["MACD.macd"] = _safe_val(macd_line)
        vals["MACD.signal"] = _safe_val(signal_line)

        # Williams %R
        wr = _williams_r(high, low, close, 14)
        vals["W.R"] = _safe_val(wr)

        # Bull Bear Power
        ema13 = _ema(close, 13)
        vals["BBPower"] = _safe_val(close - ema13)

        # Ultimate Oscillator
        uo = _ultimate_oscillator(high, low, close, 7, 14, 28)
        vals["UO"] = _safe_val(uo)

        # Moving Averages
        for period in [10, 20, 30, 50, 100, 200]:
            vals[f"SMA{period}"] = _safe_val(_sma(close, period))
            vals[f"EMA{period}"] = _safe_val(_ema(close, period))

        # Ichimoku Base Line
        vals["Ichimoku.BLine"] = _safe_val(_ichimoku_base(high, low, 26))

        # VWMA
        vals["VWMA"] = _safe_val(_vwma(close, volume, 20))

        # Hull MA
        vals["HullMA9"] = _safe_val(_hull_ma(close, 9))

        return vals

    def _build_technicals_dict(self, sym: dict) -> dict:
        """Build the output structure matching tv_data_fetcher._build_technicals_dict()."""
        close = sym.get("close")

        osc_signals = [_oscillator_signal(k, sym) for k, _ in _OSCILLATOR_DISPLAY]
        ma_signals = [_ma_signal(k, sym.get(k), close) for k, _ in _MA_DISPLAY]
        all_signals = osc_signals + ma_signals
        total_buy, total_sell, total_neutral = _count_signals(all_signals)

        # Compute recommendation values from signal counts
        osc_buy, osc_sell, osc_neutral = _count_signals(osc_signals)
        ma_buy, ma_sell, ma_neutral = _count_signals(ma_signals)

        # Recommendation as normalized value: (buy - sell) / total
        total = len(all_signals) or 1
        rec_all = (total_buy - total_sell) / total
        osc_total = len(osc_signals) or 1
        rec_osc = (osc_buy - osc_sell) / osc_total
        ma_total = len(ma_signals) or 1
        rec_ma = (ma_buy - ma_sell) / ma_total

        osc_indicators = {}
        for (fk, label), sig in zip(_OSCILLATOR_DISPLAY, osc_signals):
            val = sym.get(fk)
            if val is not None:
                osc_indicators[fk] = {
                    "label": label, "value": val,
                    "formatted": _format_tech_value(fk, val), "signal": sig,
                }

        ma_indicators = {}
        for (fk, label), sig in zip(_MA_DISPLAY, ma_signals):
            val = sym.get(fk)
            if val is not None:
                ma_indicators[fk] = {
                    "label": label, "value": val,
                    "formatted": _format_tech_value(fk, val), "signal": sig,
                }

        return {
            "price": close,
            "summary": {
                "recommendation": {"value": rec_all, "label": _tech_recommendation_label(rec_all)},
                "buy": total_buy, "sell": total_sell, "neutral": total_neutral,
            },
            "oscillators": {
                "recommendation": {"value": rec_osc, "label": _tech_recommendation_label(rec_osc)},
                "buy": osc_buy, "sell": osc_sell, "neutral": osc_neutral,
                "indicators": osc_indicators,
            },
            "moving_averages": {
                "recommendation": {"value": rec_ma, "label": _tech_recommendation_label(rec_ma)},
                "buy": ma_buy, "sell": ma_sell, "neutral": ma_neutral,
                "indicators": ma_indicators,
            },
        }

    @staticmethod
    def _empty_technicals() -> dict:
        return {
            "price": None,
            "summary": {"recommendation": None, "buy": 0, "sell": 0, "neutral": 0},
            "oscillators": {"recommendation": None, "buy": 0, "sell": 0, "neutral": 0, "indicators": {}},
            "moving_averages": {"recommendation": None, "buy": 0, "sell": 0, "neutral": 0, "indicators": {}},
        }
