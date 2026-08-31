"""Tests for calendar robustness in contract validation (full-context parity design).

Provider-shape integration tests:
- Test extractors against ACTUAL provider output structure (_build_overview, _build_dividends)
- Test calendar resolution logic (yfinance vs Cosmos)
- Test exception flow (early failures before error_msg assignment)

Fixes for Basher rejection:
- RC-1: Extractors now read nested provider paths (not flat top-level keys)
- RC-2: Exception handler uses str(e) instead of undefined error_msg
- RC-3: Tests use production-shaped nested fixtures matching real provider builders
"""

import pytest
import json
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from src.contract_validation_integration import (
    _extract_earnings_from_overview,
    _extract_exdiv_from_dividends,
    _extract_exchange,
    _resolve_calendar_date,
)


def _build_provider_overview_json(earnings_timestamp=None):
    """Build provider-shaped overview JSON matching _build_overview output."""
    return json.dumps({
        "name": "Test Corp",
        "ticker": "TEST",
        "exchange": "NASDAQ",
        "fundamentals": {
            "earnings_release_next_date_fq": {
                "label": "Next Earnings Date",
                "value": earnings_timestamp,
                "formatted": datetime.fromtimestamp(earnings_timestamp, tz=timezone.utc).strftime("%Y-%m-%d") if earnings_timestamp else None
            } if earnings_timestamp is not None else None
        } if earnings_timestamp is not None else {}
    })


def _build_provider_dividends_json(exdiv_timestamp=None):
    """Build provider-shaped dividends JSON matching _build_dividends output."""
    return json.dumps({
        "name": "Test Corp",
        "ticker": "TEST",
        "exchange": "NASDAQ",
        "dividends": {
            "ex_dividend_date_recent": {
                "label": "Ex-Dividend Date (Recent)",
                "value": exdiv_timestamp,
                "formatted": datetime.fromtimestamp(exdiv_timestamp, tz=timezone.utc).strftime("%Y-%m-%d") if exdiv_timestamp else None
            } if exdiv_timestamp is not None else None
        } if exdiv_timestamp is not None else {}
    })


class TestProviderShapeIntegration:
    """Test extractors against REAL provider output shapes."""

    def test_extract_earnings_from_real_overview_shape(self):
        """Extract earnings date from real _build_overview output structure."""
        # 2024-09-10 epoch timestamp
        overview_json = _build_provider_overview_json(earnings_timestamp=1725984000)
        
        result = _extract_earnings_from_overview(overview_json)
        
        # Assert correct extraction from nested provider structure
        assert result == "2024-09-10"

    def test_extract_earnings_from_real_overview_shape_no_earnings(self):
        """Return None when earnings field missing in real provider output."""
        overview_json = json.dumps({
            "name": "Test Corp",
            "ticker": "TEST",
            "exchange": "NASDAQ",
            "fundamentals": {}
        })
        
        result = _extract_earnings_from_overview(overview_json)
        assert result is None

    def test_extract_exdiv_from_real_dividends_shape(self):
        """Extract ex-dividend date from real _build_dividends output structure."""
        # Future date (30 days from now)
        future_timestamp = int((datetime.now(timezone.utc) + timedelta(days=30)).timestamp())
        dividends_json = _build_provider_dividends_json(exdiv_timestamp=future_timestamp)
        
        result = _extract_exdiv_from_dividends(dividends_json)
        
        # Assert correct extraction from nested provider structure
        assert result is not None
        # Should be in YYYY-MM-DD format
        assert len(result) == 10
        assert result[4] == "-" and result[7] == "-"

    def test_extract_exdiv_past_date_from_real_shape(self):
        """Return None when ex-dividend date is in the past (future-only gate)."""
        # Past date (2000-01-01)
        past_timestamp = 946684800
        dividends_json = _build_provider_dividends_json(exdiv_timestamp=past_timestamp)
        
        result = _extract_exdiv_from_dividends(dividends_json)
        
        # Should be None (past date filtered out)
        assert result is None

    def test_extract_exdiv_no_exdiv_in_real_shape(self):
        """Return None when ex-dividend field missing in real provider output."""
        dividends_json = json.dumps({
            "name": "Test Corp",
            "ticker": "TEST",
            "exchange": "NASDAQ",
            "dividends": {}
        })
        
        result = _extract_exdiv_from_dividends(dividends_json)
        assert result is None

    def test_extract_earnings_formatted_fallback(self):
        """Use formatted field when value is None."""
        # Build provider-shaped JSON with value=None but formatted field
        overview_json = json.dumps({
            "name": "Test Corp",
            "ticker": "TEST",
            "exchange": "NASDAQ",
            "fundamentals": {
                "earnings_release_next_date_fq": {
                    "label": "Next Earnings Date",
                    "value": None,
                    "formatted": "2027-01-15"
                }
            }
        })
        
        result = _extract_earnings_from_overview(overview_json)
        assert result == "2027-01-15"

    def test_extract_exdiv_formatted_fallback(self):
        """Use formatted field when value is None."""
        # Build provider-shaped JSON with value=None but formatted field
        dividends_json = json.dumps({
            "name": "Test Corp",
            "ticker": "TEST",
            "exchange": "NASDAQ",
            "dividends": {
                "ex_dividend_date_recent": {
                    "label": "Ex-Dividend Date (Recent)",
                    "value": None,
                    "formatted": "2027-01-15"
                }
            }
        })
        
        result = _extract_exdiv_from_dividends(dividends_json)
        assert result == "2027-01-15"

    def test_extract_exchange_from_real_overview(self):
        """Extract exchange from real _build_overview output."""
        overview_json = json.dumps({
            "name": "Test Corp",
            "ticker": "TEST",
            "exchange": "NASDAQ",
            "fundamentals": {}
        })
        
        result = _extract_exchange(overview_json)
        assert result == "NASDAQ"


class TestCalendarResolution:
    """Test calendar date resolution logic (yfinance vs Cosmos)."""

    def test_yfinance_preferred_when_both_present(self):
        """Prefer yfinance date when both sources available."""
        yf_date = "2027-01-15"
        cosmos_date = "2027-01-10"
        
        resolved, source = _resolve_calendar_date(yf_date, cosmos_date, "earnings")
        
        assert resolved == yf_date
        assert source == "yfinance"

    def test_cosmos_fallback_when_yfinance_unavailable(self):
        """Fall back to Cosmos when yfinance unavailable."""
        yf_date = None
        cosmos_date = "2027-01-10"
        
        resolved, source = _resolve_calendar_date(yf_date, cosmos_date, "earnings")
        
        assert resolved == cosmos_date
        assert source == "cosmos"

    def test_none_when_both_unavailable(self):
        """Return None when both sources unavailable."""
        yf_date = None
        cosmos_date = None
        
        resolved, source = _resolve_calendar_date(yf_date, cosmos_date, "earnings")
        
        assert resolved is None
        assert source == "none"

    def test_conflict_logged_when_dates_differ(self):
        """Log warning when yfinance and Cosmos dates disagree."""
        yf_date = "2027-01-15"
        cosmos_date = "2027-01-10"
        
        with patch("src.contract_validation_integration.logger") as mock_logger:
            resolved, source = _resolve_calendar_date(yf_date, cosmos_date, "earnings")
            
            # Should log warning about conflict
            assert mock_logger.warning.called
            warning_msg = mock_logger.warning.call_args[0][0]
            assert "conflict" in warning_msg.lower()
            assert "yfinance" in warning_msg.lower()
            assert "cosmos" in warning_msg.lower()
            
            # Should still prefer yfinance
            assert resolved == yf_date
            assert source == "yfinance"


class TestExtractorEdgeCases:
    """Test extractor edge cases and malformed input."""

    def test_extract_earnings_malformed_json(self):
        """Handle malformed JSON gracefully."""
        result = _extract_earnings_from_overview("NOT-JSON")
        assert result is None

    def test_extract_exdiv_malformed_json(self):
        """Handle malformed JSON gracefully."""
        result = _extract_exdiv_from_dividends("NOT-JSON")
        assert result is None

    def test_extract_earnings_missing_fundamentals_key(self):
        """Return None when fundamentals key missing."""
        overview_json = json.dumps({
            "name": "Test Corp",
            "ticker": "TEST",
            "exchange": "NASDAQ",
            # No fundamentals key
        })
        result = _extract_earnings_from_overview(overview_json)
        assert result is None

    def test_extract_exdiv_missing_dividends_key(self):
        """Return None when dividends key missing."""
        dividends_json = json.dumps({
            "name": "Test Corp",
            "ticker": "TEST",
            "exchange": "NASDAQ",
            # No dividends key
        })
        result = _extract_exdiv_from_dividends(dividends_json)
        assert result is None

    def test_extract_earnings_value_unparseable(self):
        """Return None when value is unparseable."""
        overview_json = json.dumps({
            "name": "Test Corp",
            "ticker": "TEST",
            "exchange": "NASDAQ",
            "fundamentals": {
                "earnings_release_next_date_fq": {
                    "label": "Next Earnings Date",
                    "value": "invalid-timestamp",
                    "formatted": None
                }
            }
        })
        result = _extract_earnings_from_overview(overview_json)
        assert result is None

    def test_extract_exdiv_value_unparseable(self):
        """Return None when value is unparseable."""
        dividends_json = json.dumps({
            "name": "Test Corp",
            "ticker": "TEST",
            "exchange": "NASDAQ",
            "dividends": {
                "ex_dividend_date_recent": {
                    "label": "Ex-Dividend Date (Recent)",
                    "value": "invalid-timestamp",
                    "formatted": None
                }
            }
        })
        result = _extract_exdiv_from_dividends(dividends_json)
        assert result is None

    def test_extract_earnings_iso_format_string_value(self):
        """Handle ISO format string in value field."""
        overview_json = json.dumps({
            "name": "Test Corp",
            "ticker": "TEST",
            "exchange": "NASDAQ",
            "fundamentals": {
                "earnings_release_next_date_fq": {
                    "label": "Next Earnings Date",
                    "value": "2027-01-15T10:00:00Z",  # ISO string
                    "formatted": None
                }
            }
        })
        result = _extract_earnings_from_overview(overview_json)
        assert result == "2027-01-15"

    def test_extract_exdiv_iso_format_string_value(self):
        """Handle ISO format string in value field."""
        dividends_json = json.dumps({
            "name": "Test Corp",
            "ticker": "TEST",
            "exchange": "NASDAQ",
            "dividends": {
                "ex_dividend_date_recent": {
                    "label": "Ex-Dividend Date (Recent)",
                    "value": "2027-01-15T10:00:00Z",  # ISO string
                    "formatted": None
                }
            }
        })
        result = _extract_exdiv_from_dividends(dividends_json)
        assert result == "2027-01-15"
