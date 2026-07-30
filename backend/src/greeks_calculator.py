"""Black-Scholes Greeks computation module.

Computes delta, gamma, theta, vega, and rho for European options using
py_vollib when available, falling back to a manual scipy implementation.
"""

import logging
import math
from typing import Optional

logger = logging.getLogger(__name__)

# Try py_vollib first; fall back to manual scipy implementation
try:
    from py_vollib.black_scholes.greeks.analytical import delta as _vol_delta
    from py_vollib.black_scholes.greeks.analytical import gamma as _vol_gamma
    from py_vollib.black_scholes.greeks.analytical import theta as _vol_theta
    from py_vollib.black_scholes.greeks.analytical import vega as _vol_vega
    from py_vollib.black_scholes.greeks.analytical import rho as _vol_rho
    _HAS_VOLLIB = True
except ImportError:
    _HAS_VOLLIB = False

try:
    from scipy.stats import norm
    _HAS_SCIPY = True
except ImportError:
    _HAS_SCIPY = False


def _fetch_risk_free_rate() -> float:
    """Fetch 10-Year Treasury yield from ^TNX via yfinance, fallback 4.5%."""
    try:
        import yfinance as yf
        rate = yf.Ticker("^TNX").info.get("regularMarketPrice")
        if rate is not None and rate > 0:
            return rate / 100.0
    except Exception:
        pass
    return 0.045


# Manual Black-Scholes Greeks (scipy fallback)
def _d1(S, K, T, r, sigma):
    return (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))


def _d2(S, K, T, r, sigma):
    return _d1(S, K, T, r, sigma) - sigma * math.sqrt(T)


class GreeksCalculator:
    """Compute Black-Scholes Greeks for European options."""

    def __init__(self, risk_free_rate: Optional[float] = None):
        self._risk_free_rate = risk_free_rate
        self._rate_fetched = risk_free_rate is not None

    @property
    def risk_free_rate(self) -> float:
        if not self._rate_fetched:
            self._risk_free_rate = _fetch_risk_free_rate()
            self._rate_fetched = True
            logger.info("Risk-free rate: %.4f", self._risk_free_rate)
        return self._risk_free_rate

    def compute(self, flag: str, S: float, K: float, T: float, sigma: float) -> dict:
        """Compute all 5 Greeks for a single option.

        Args:
            flag: 'c' for call, 'p' for put
            S: current stock price
            K: strike price
            T: time to expiry in years (DTE/365)
            sigma: implied volatility (decimal, e.g. 0.35 for 35%)

        Returns:
            {"delta", "gamma", "theta", "vega", "rho"} — theta is daily,
            vega is per 1% IV change.
        """
        # Edge cases
        if T <= 1e-10 or sigma <= 1e-10 or S <= 0 or K <= 0:
            return self._expired_greeks(flag, S, K)

        r = self.risk_free_rate

        if _HAS_VOLLIB:
            try:
                return {
                    "delta": round(_vol_delta(flag, S, K, T, r, sigma), 6),
                    "gamma": round(_vol_gamma(flag, S, K, T, r, sigma), 6),
                    "theta": round(_vol_theta(flag, S, K, T, r, sigma) / 365, 6),
                    "vega": round(_vol_vega(flag, S, K, T, r, sigma) / 100, 6),
                    "rho": round(_vol_rho(flag, S, K, T, r, sigma), 6),
                }
            except Exception:
                pass  # fall through to manual

        if not _HAS_SCIPY:
            logger.error("Neither py_vollib nor scipy available for Greeks")
            return {"delta": 0, "gamma": 0, "theta": 0, "vega": 0, "rho": 0}

        return self._manual_greeks(flag, S, K, T, r, sigma)

    def _manual_greeks(self, flag, S, K, T, r, sigma):
        d1 = _d1(S, K, T, r, sigma)
        d2 = _d2(S, K, T, r, sigma)
        sqrt_T = math.sqrt(T)
        disc = math.exp(-r * T)

        if flag == "c":
            delta = norm.cdf(d1)
            rho_val = K * T * disc * norm.cdf(d2)
        else:
            delta = norm.cdf(d1) - 1
            rho_val = -K * T * disc * norm.cdf(-d2)

        gamma = norm.pdf(d1) / (S * sigma * sqrt_T)
        theta_annual = (
            -(S * norm.pdf(d1) * sigma) / (2 * sqrt_T)
            - r * K * disc * norm.cdf(d2 if flag == "c" else -d2) * (1 if flag == "c" else -1)
        )
        vega_full = S * norm.pdf(d1) * sqrt_T

        return {
            "delta": round(delta, 6),
            "gamma": round(gamma, 6),
            "theta": round(theta_annual / 365, 6),
            "vega": round(vega_full / 100, 6),
            "rho": round(rho_val, 6),
        }

    @staticmethod
    def _expired_greeks(flag, S, K):
        """Greeks for expired / near-zero vol options."""
        if flag == "c":
            delta = 1.0 if S > K else (0.5 if S == K else 0.0)
        else:
            delta = -1.0 if S < K else (-0.5 if S == K else 0.0)
        return {"delta": delta, "gamma": 0.0, "theta": 0.0, "vega": 0.0, "rho": 0.0}

    def compute_batch(self, options: list[dict]) -> list[dict]:
        """Compute Greeks for multiple options.

        Each dict in *options* must have keys: flag, S, K, T, sigma.
        Returns list of Greeks dicts in same order.
        """
        return [
            self.compute(
                flag=opt["flag"], S=opt["S"], K=opt["K"],
                T=opt["T"], sigma=opt["sigma"],
            )
            for opt in options
        ]
