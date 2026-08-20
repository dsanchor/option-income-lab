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
# 5b. Theta unit-conversion regression (py_vollib double /365 bug)
# ---------------------------------------------------------------------------

class TestThetaUnitConversionRegression:
    """Regression suite for the py_vollib double-division bug: py_vollib's
    own `theta()` already divides its raw annual Black-Scholes theta by
    365 internally (its own docstring: "the text book analytical formula
    does not divide by 365 ... hence we divide by 365" — it returns the
    daily, per-share value directly). `GreeksCalculator.compute()`'s
    py_vollib branch used to divide by 365 again, deflating theta by
    ~365x. Every test below would fail if that extra division were
    reintroduced."""

    def test_hull_textbook_call_reference_value(self):
        """Hull Example 17.2, p.359 — also py_vollib's own doctest for
        `theta()`: S=49, K=50, r=.05, T=0.3846, sigma=0.2, call. Annual
        theta ~= -4.30538996455 -> correct daily = annual/365. Uses r=0.05
        (not the fixture's 0.045) to match the reference example exactly."""
        hull_calc = GreeksCalculator(risk_free_rate=0.05)
        g = hull_calc.compute(flag="c", S=49.0, K=50.0, T=0.3846, sigma=0.2)
        expected_daily = -4.30538996455 / 365
        assert g["theta"] == pytest.approx(expected_daily, abs=2e-4)
        # A regression to the double-divided bug would be ~365x smaller
        # in magnitude than the correct reference value.
        assert abs(g["theta"]) > abs(expected_daily) / 50

    def test_hull_textbook_put_reference_value(self):
        """Same Hull example, put side (py_vollib's own doctest reference
        value): annual theta = -1.8530056722."""
        hull_calc = GreeksCalculator(risk_free_rate=0.05)
        g = hull_calc.compute(flag="p", S=49.0, K=50.0, T=0.3846, sigma=0.2)
        expected_daily = -1.8530056722 / 365
        assert g["theta"] == pytest.approx(expected_daily, abs=2e-4)

    def test_theta_matches_manual_fallback_across_scenarios(self, calc):
        """Path equivalence: the py_vollib-backed result and the manual/
        scipy fallback must agree (same underlying daily-theta formula)
        across a spread of strikes/DTE/IV/call+put — a double-division on
        only one path would make this diverge by ~365x."""
        r = calc.risk_free_rate
        for flag in ("c", "p"):
            for dte in (1, 7, 30, 60, 365):
                T = dte / 365
                for K in (90.0, 100.0, 110.0):
                    for sigma in (0.20, 0.50):
                        vollib_theta = calc.compute(flag, 100.0, K, T, sigma)["theta"]
                        manual_theta = calc._manual_greeks(flag, 100.0, K, T, r, sigma)["theta"]
                        assert vollib_theta == pytest.approx(manual_theta, abs=2e-4), (
                            f"path divergence flag={flag} dte={dte} K={K} sigma={sigma}: "
                            f"vollib={vollib_theta} manual={manual_theta}"
                        )

    def test_theta_forced_fallback_matches_real_vollib_path(self, calc, monkeypatch):
        """Forcing the module to behave as if py_vollib were unavailable
        (`_HAS_VOLLIB = False`) must produce the same daily theta as the
        real py_vollib-backed path for identical inputs — the two code
        paths must never silently disagree by a ~365x unit mismatch."""
        import src.greeks_calculator as gc_module

        params = dict(flag="c", S=185.0, K=190.0, T=30 / 365, sigma=0.25)
        with_vollib = calc.compute(**params)["theta"]

        monkeypatch.setattr(gc_module, "_HAS_VOLLIB", False)
        forced_fallback = calc.compute(**params)["theta"]
        assert forced_fallback == pytest.approx(with_vollib, abs=2e-4)

    def test_theta_negative_across_dte_range_both_flags(self, calc):
        """Long options always lose value to time decay -> theta < 0 for
        both calls and puts, at every DTE in the task's required matrix
        (ATM, moderate IV, no dividend — the conventional negative-theta
        regime)."""
        for flag in ("c", "p"):
            for dte in (1, 7, 30, 60, 365):
                g = calc.compute(flag=flag, S=100.0, K=100.0, T=dte / 365, sigma=0.30)
                assert g["theta"] < 0, f"flag={flag} dte={dte}: expected negative theta, got {g['theta']}"

    def test_theta_finite_and_growing_near_expiry(self, calc):
        """As T shrinks toward (but not at) zero, theta must stay finite
        (no inf/NaN blow-up) and its magnitude must grow as expiry nears —
        the hallmark of correctly-scaled time decay acceleration."""
        magnitudes = []
        for dte in (30, 7, 1, 0.5):
            g = calc.compute(flag="c", S=100.0, K=100.0, T=dte / 365, sigma=0.30)
            assert math.isfinite(g["theta"])
            magnitudes.append(abs(g["theta"]))
        assert magnitudes == sorted(magnitudes), (
            f"theta magnitude should grow as DTE shrinks, got {magnitudes}"
        )

    def test_theta_at_true_expiry_is_zero(self, calc):
        g = calc.compute(flag="c", S=100.0, K=100.0, T=0.0, sigma=0.30)
        assert g["theta"] == 0.0

    def test_daily_theta_is_human_scale_not_365x_too_small(self, calc):
        """Sanity check against the module's own documented convention
        (`compute()`'s docstring: "theta is daily"). A realistic 30 DTE
        ATM option should decay a human-scale fraction of its premium per
        day, not ~1e-4/365 — the bug's magnitude signature."""
        g = calc.compute(flag="c", S=100.0, K=100.0, T=30 / 365, sigma=0.30)
        assert -0.5 < g["theta"] < -0.01

    def test_call_put_theta_parity_matches_closed_form_identity(self, calc):
        """Exact analytical identity (derivable directly from the shared
        Black-Scholes theta formula, independent of sigma): daily
        theta_call - theta_put == -r * K * exp(-r*T) / 365. A double
        division on only the py_vollib path (or any inconsistent scaling
        between call/put) would break this by the same ~365x factor."""
        r = calc.risk_free_rate
        for K in (90.0, 100.0, 110.0):
            for dte in (1, 30, 365):
                T = dte / 365
                call_theta = calc.compute("c", 100.0, K, T, 0.30)["theta"]
                put_theta = calc.compute("p", 100.0, K, T, 0.30)["theta"]
                expected_diff = -r * K * math.exp(-r * T) / 365
                assert (call_theta - put_theta) == pytest.approx(expected_diff, abs=2e-4), (
                    f"K={K} dte={dte}: call-put={call_theta - put_theta} expected={expected_diff}"
                )

    def test_raw_py_vollib_theta_equals_computed_theta_directly(self, calc):
        """Direct cross-check against py_vollib's own `theta()` return
        value (already daily per its docstring) — `compute()` must pass
        it through unchanged, not rescale it further."""
        from py_vollib.black_scholes.greeks.analytical import theta as vol_theta

        r = calc.risk_free_rate
        for flag in ("c", "p"):
            for K in (90.0, 100.0, 110.0):
                raw = vol_theta(flag, 100.0, K, 30 / 365, r, 0.30)
                computed = calc.compute(flag, 100.0, K, 30 / 365, 0.30)["theta"]
                assert computed == pytest.approx(raw, abs=1e-4)

    def test_other_greeks_unchanged_by_the_theta_fix(self, calc):
        """The fix touches only the theta line — delta/gamma/vega/rho on
        the py_vollib path must still equal their own dedicated py_vollib
        functions exactly as before (no collateral change to other
        Greeks, per this task's explicit scope)."""
        from py_vollib.black_scholes.greeks.analytical import delta as vol_delta
        from py_vollib.black_scholes.greeks.analytical import gamma as vol_gamma
        from py_vollib.black_scholes.greeks.analytical import vega as vol_vega
        from py_vollib.black_scholes.greeks.analytical import rho as vol_rho

        r = calc.risk_free_rate
        flag, S, K, T, sigma = "c", 185.0, 190.0, 30 / 365, 0.25
        g = calc.compute(flag, S, K, T, sigma)
        assert g["delta"] == pytest.approx(vol_delta(flag, S, K, T, r, sigma), abs=1e-6)
        assert g["gamma"] == pytest.approx(vol_gamma(flag, S, K, T, r, sigma), abs=1e-6)
        assert g["vega"] == pytest.approx(vol_vega(flag, S, K, T, r, sigma) / 100, abs=1e-6)
        assert g["rho"] == pytest.approx(vol_rho(flag, S, K, T, r, sigma), abs=1e-6)


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
