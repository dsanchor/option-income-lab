"""Tests for Black-Scholes Greeks computation module.

Validates greeks_calculator.py — the Phase 1 foundation module that computes
option Greeks (delta, gamma, theta, vega, rho) using the Black-Scholes model.

All tests use deterministic inputs with known mathematical properties.
No external API calls — yfinance is mocked where needed (risk-free rate).
"""

import math
from unittest.mock import MagicMock, patch

import pytest

from src.greeks_calculator import GreeksCalculator, _fetch_risk_free_rate


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def calc():
    """Calculator with fixed risk-free rate (no network calls)."""
    return GreeksCalculator(risk_free_rate=0.045)


@pytest.fixture
def aapl_otm_call_params():
    """AAPL slightly OTM call — S=185, K=190, ~30 DTE."""
    return dict(flag="c", S=185.0, K=190.0, T=30 / 365, sigma=0.25)


@pytest.fixture
def aapl_otm_put_params():
    """Same strikes as call fixture, but put."""
    return dict(flag="p", S=185.0, K=190.0, T=30 / 365, sigma=0.25)


@pytest.fixture
def deep_itm_call():
    return dict(flag="c", S=200.0, K=150.0, T=30 / 365, sigma=0.25)


@pytest.fixture
def deep_otm_call():
    return dict(flag="c", S=150.0, K=200.0, T=30 / 365, sigma=0.25)


@pytest.fixture
def atm_call():
    return dict(flag="c", S=185.0, K=185.0, T=30 / 365, sigma=0.25)


@pytest.fixture
def batch_options():
    """Multiple options for batch computation."""
    return [
        dict(flag="c", S=185.0, K=190.0, T=30 / 365, sigma=0.25),
        dict(flag="p", S=185.0, K=180.0, T=60 / 365, sigma=0.30),
        dict(flag="c", S=300.0, K=310.0, T=45 / 365, sigma=0.20),
    ]


# ---------------------------------------------------------------------------
# 1. Known value ranges — OTM call
# ---------------------------------------------------------------------------

class TestKnownValueRangesCall:
    """Verify Greeks for a slightly OTM AAPL call are in expected ranges."""

    def test_call_delta_range(self, calc, aapl_otm_call_params):
        g = calc.compute(**aapl_otm_call_params)
        assert 0 < g["delta"] < 1, "Call delta must be between 0 and 1"
        # Slightly OTM, 30 DTE → delta roughly 0.3-0.45
        assert 0.2 < g["delta"] < 0.5, f"OTM call delta should be ~0.3-0.4, got {g['delta']}"

    def test_gamma_positive(self, calc, aapl_otm_call_params):
        g = calc.compute(**aapl_otm_call_params)
        assert g["gamma"] > 0, "Gamma must be positive"

    def test_theta_negative(self, calc, aapl_otm_call_params):
        g = calc.compute(**aapl_otm_call_params)
        assert g["theta"] < 0, "Theta (daily decay) must be negative"

    def test_vega_positive(self, calc, aapl_otm_call_params):
        g = calc.compute(**aapl_otm_call_params)
        assert g["vega"] > 0, "Vega must be positive"

    def test_rho_positive_for_call(self, calc, aapl_otm_call_params):
        g = calc.compute(**aapl_otm_call_params)
        assert g["rho"] > 0, "Rho must be positive for calls"


# ---------------------------------------------------------------------------
# 2. Known value ranges — OTM put
# ---------------------------------------------------------------------------

class TestKnownValueRangesPut:
    """Verify Greeks for a slightly OTM put (same strikes)."""

    def test_put_delta_range(self, calc, aapl_otm_put_params):
        g = calc.compute(**aapl_otm_put_params)
        assert -1 < g["delta"] < 0, "Put delta must be between -1 and 0"

    def test_put_gamma_positive(self, calc, aapl_otm_put_params):
        g = calc.compute(**aapl_otm_put_params)
        assert g["gamma"] > 0, "Put gamma must be positive"

    def test_put_theta_negative(self, calc, aapl_otm_put_params):
        g = calc.compute(**aapl_otm_put_params)
        assert g["theta"] < 0, "Put theta must be negative"

    def test_put_vega_positive(self, calc, aapl_otm_put_params):
        g = calc.compute(**aapl_otm_put_params)
        assert g["vega"] > 0, "Put vega must be positive"

    def test_rho_negative_for_put(self, calc, aapl_otm_put_params):
        g = calc.compute(**aapl_otm_put_params)
        assert g["rho"] < 0, "Rho must be negative for puts"


# ---------------------------------------------------------------------------
# 3. Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_near_zero_time_no_crash(self, calc):
        """T ≈ 0 (expiring today) — must not crash, uses _expired_greeks."""
        g = calc.compute(flag="c", S=185.0, K=190.0, T=1e-11, sigma=0.25)
        assert isinstance(g["delta"], float)
        assert isinstance(g["theta"], float)
        # OTM at expiry → delta should be 0
        assert g["delta"] == 0.0

    def test_near_zero_sigma_no_crash(self, calc):
        """sigma ≈ 0 — should handle gracefully via _expired_greeks."""
        g = calc.compute(flag="c", S=185.0, K=190.0, T=30 / 365, sigma=1e-11)
        assert isinstance(g["delta"], float)
        assert not math.isnan(g["delta"]), "Delta must not be NaN"

    def test_deep_itm_call_delta_near_one(self, calc, deep_itm_call):
        """Deep ITM call (S=200, K=150) → delta ≈ 1.0."""
        g = calc.compute(**deep_itm_call)
        assert g["delta"] > 0.95, f"Deep ITM call delta should be near 1.0, got {g['delta']}"

    def test_deep_otm_call_delta_near_zero(self, calc, deep_otm_call):
        """Deep OTM call (S=150, K=200) → delta ≈ 0.0."""
        g = calc.compute(**deep_otm_call)
        assert g["delta"] < 0.05, f"Deep OTM call delta should be near 0.0, got {g['delta']}"

    def test_atm_call_delta_near_half(self, calc, atm_call):
        """ATM option (S=K=185) → delta ≈ 0.5."""
        g = calc.compute(**atm_call)
        assert 0.45 < g["delta"] < 0.60, f"ATM call delta should be near 0.5, got {g['delta']}"

    def test_expired_itm_call_delta_one(self, calc):
        """Expired ITM call → delta = 1.0."""
        g = calc.compute(flag="c", S=200.0, K=150.0, T=0.0, sigma=0.25)
        assert g["delta"] == 1.0

    def test_expired_otm_put_delta_zero(self, calc):
        """Expired OTM put → delta = 0.0."""
        g = calc.compute(flag="p", S=200.0, K=150.0, T=0.0, sigma=0.25)
        assert g["delta"] == 0.0

    def test_expired_atm_call_delta_half(self, calc):
        """Expired ATM call (S==K) → delta = 0.5."""
        g = calc.compute(flag="c", S=185.0, K=185.0, T=0.0, sigma=0.25)
        assert g["delta"] == 0.5


# ---------------------------------------------------------------------------
# 4. Put-call delta parity
# ---------------------------------------------------------------------------

class TestPutCallParity:
    def test_call_delta_minus_put_delta_approx_one(self, calc):
        """For same params: call_delta - put_delta ≈ 1.0 (discounted)."""
        params = dict(S=185.0, K=190.0, T=30 / 365, sigma=0.25)
        call_g = calc.compute(flag="c", **params)
        put_g = calc.compute(flag="p", **params)
        diff = call_g["delta"] - put_g["delta"]
        assert abs(diff - 1.0) < 0.05, f"call_delta - put_delta should ≈ 1.0, got {diff}"

    def test_parity_holds_at_multiple_strikes(self, calc):
        """Verify parity across several strikes."""
        for K in [170, 185, 190, 200, 220]:
            params = dict(S=185.0, K=float(K), T=60 / 365, sigma=0.25)
            c = calc.compute(flag="c", **params)
            p = calc.compute(flag="p", **params)
            diff = c["delta"] - p["delta"]
            assert abs(diff - 1.0) < 0.05, f"Parity failed at K={K}: diff={diff}"


# ---------------------------------------------------------------------------
# 5. Batch computation
# ---------------------------------------------------------------------------

class TestBatchComputation:
    def test_batch_returns_list_of_dicts(self, calc, batch_options):
        results = calc.compute_batch(batch_options)
        assert isinstance(results, list)
        assert len(results) == 3

    def test_batch_each_has_required_keys(self, calc, batch_options):
        results = calc.compute_batch(batch_options)
        required_keys = {"delta", "gamma", "theta", "vega", "rho"}
        for r in results:
            assert required_keys.issubset(r.keys()), f"Missing keys in {r.keys()}"

    def test_batch_values_are_finite(self, calc, batch_options):
        results = calc.compute_batch(batch_options)
        for r in results:
            for key in ("delta", "gamma", "theta", "vega", "rho"):
                assert math.isfinite(r[key]), f"{key} is not finite: {r[key]}"


# ---------------------------------------------------------------------------
# 6. Risk-free rate fallback
# ---------------------------------------------------------------------------

class TestRiskFreeRate:
    @patch("yfinance.Ticker")
    def test_fetches_tnx_yield(self, mock_ticker_cls):
        """When ^TNX is available, use its value (converted from %)."""
        mock_ticker = MagicMock()
        mock_ticker.info = {"regularMarketPrice": 4.35}
        mock_ticker_cls.return_value = mock_ticker

        rate = _fetch_risk_free_rate()
        assert rate == pytest.approx(0.0435, abs=0.001)
        mock_ticker_cls.assert_called_once_with("^TNX")

    @patch("yfinance.Ticker")
    def test_fallback_when_tnx_unavailable(self, mock_ticker_cls):
        """When ^TNX fails, fall back to default 4.5%."""
        mock_ticker_cls.side_effect = Exception("Network error")

        rate = _fetch_risk_free_rate()
        assert rate == pytest.approx(0.045, abs=0.001)

    @patch("yfinance.Ticker")
    def test_fallback_when_tnx_returns_none(self, mock_ticker_cls):
        """When ^TNX returns None info, fall back to default."""
        mock_ticker = MagicMock()
        mock_ticker.info = {"regularMarketPrice": None}
        mock_ticker_cls.return_value = mock_ticker

        rate = _fetch_risk_free_rate()
        assert rate == pytest.approx(0.045, abs=0.001)

    def test_calculator_uses_provided_rate(self):
        """When rate is provided at init, no fetch occurs."""
        calc = GreeksCalculator(risk_free_rate=0.05)
        assert calc.risk_free_rate == 0.05


# ---------------------------------------------------------------------------
# 7. Output structure
# ---------------------------------------------------------------------------

class TestOutputStructure:
    def test_returns_dict_with_greek_keys(self, calc, aapl_otm_call_params):
        g = calc.compute(**aapl_otm_call_params)
        assert isinstance(g, dict)
        for key in ("delta", "gamma", "theta", "vega", "rho"):
            assert key in g, f"Missing key: {key}"
            assert isinstance(g[key], float), f"{key} should be float, got {type(g[key])}"
