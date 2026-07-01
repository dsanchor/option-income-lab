from datetime import datetime, timezone

from web.app import _build_economics_report


def _sample_symbol_docs():
    return [
        {
            "symbol": "AAPL",
            "positions": [
                {
                    "position_id": "aapl-call-1",
                    "type": "call",
                    "strike": 1000,
                    "expiration": "2026-01-31",
                    "opened_at": "2026-01-01T00:00:00Z",
                    "closed_at": "2026-01-20T00:00:00Z",
                    "status": "closed",
                    "source": {"premium": 100},
                },
                {
                    "position_id": "aapl-put-1",
                    "type": "put",
                    "strike": 800,
                    "expiration": "2026-02-14",
                    "opened_at": "2026-01-15T00:00:00Z",
                    "closed_at": "2026-02-01T00:00:00Z",
                    "status": "rolled",
                    "buyback_cost": 30,
                    "source": {"premium": 80},
                },
                {
                    "position_id": "aapl-call-skip",
                    "type": "call",
                    "strike": 900,
                    "expiration": "2026-03-01",
                    "opened_at": "2026-02-01T00:00:00Z",
                    "status": "active",
                    "source": {"premium": None},
                },
            ],
        },
        {
            "symbol": "MSFT",
            "positions": [
                {
                    "position_id": "msft-put-1",
                    "type": "put",
                    "strike": 500,
                    "expiration": "2025-12-31",
                    "opened_at": "2025-12-01T00:00:00Z",
                    "status": "active",
                    "source": {"premium": "50"},
                },
                {
                    "position_id": "msft-call-skip",
                    "type": "call",
                    "strike": 600,
                    "expiration": "2026-02-28",
                    "opened_at": "2026-01-29T00:00:00Z",
                    "status": "closed",
                    "source": {"premium": "N/A"},
                },
                {
                    "position_id": "msft-put-2",
                    "type": "put",
                    "strike": 700,
                    "expiration": "2026-03-03",
                    "opened_at": "2026-02-01T00:00:00Z",
                    "closed_at": "2026-02-20T00:00:00Z",
                    "status": "closed",
                    "buyback_cost": "N/A",
                    "source": {"premium": 70},
                },
            ],
        },
    ]


def test_build_economics_report_aggregates_only_valid_numeric_premiums():
    report = _build_economics_report(
        _sample_symbol_docs(),
        now=datetime(2026, 3, 1, tzinfo=timezone.utc),
    )

    assert report["summary"] == {
        "total_premium": 30000.0,
        "total_buyback": 3000.0,
        "net_income": 27000.0,
        "avg_roc_pct": 9.0,
        "avg_roc_annualized": 109.5,
        "win_rate": 100.0,
        "total_positions": 4,
    }
    assert report["filters"] == {
        "years": [2026, 2025],
        "symbols": ["AAPL", "MSFT"],
    }
    assert [row["label"] for row in report["monthly"]] == [
        "Dec 2025",
        "Jan 2026",
        "Feb 2026",
    ]
    assert report["monthly"][1]["positions_count"] == 2
    assert report["monthly"][1]["calls_count"] == 1
    assert report["monthly"][1]["puts_count"] == 1
    assert report["by_symbol"] == [
        {
            "symbol": "AAPL",
            "premium": 18000.0,
            "buyback": 3000.0,
            "net": 15000.0,
            "positions_count": 2,
            "avg_roc_pct": 8.33,
            "avg_roc_annualized": 101.35,
        },
        {
            "symbol": "MSFT",
            "premium": 12000.0,
            "buyback": 0.0,
            "net": 12000.0,
            "positions_count": 2,
            "avg_roc_pct": 10.0,
            "avg_roc_annualized": 121.67,
        },
    ]
    assert report["by_type"]["calls"]["count"] == 1
    assert report["by_type"]["puts"]["count"] == 3
    assert [position["position_id"] for position in report["positions"]] == [
        "msft-put-2",
        "aapl-put-1",
        "aapl-call-1",
        "msft-put-1",
    ]


def test_build_economics_report_applies_filters():
    report = _build_economics_report(
        _sample_symbol_docs(),
        year=2026,
        symbol_filter="AAPL",
        option_type="put",
        status_filter="rolled",
        now=datetime(2026, 3, 1, tzinfo=timezone.utc),
    )

    assert report["summary"]["total_positions"] == 1
    assert report["summary"]["total_premium"] == 8000.0
    assert report["summary"]["total_buyback"] == 3000.0
    assert report["summary"]["net_income"] == 5000.0
    assert report["summary"]["win_rate"] == 100.0
    assert report["positions"] == [
        {
            "symbol": "AAPL",
            "position_id": "aapl-put-1",
            "type": "put",
            "strike": 800.0,
            "expiration": "2026-02-14",
            "premium": 8000.0,
            "premium_per_share": 80.0,
            "buyback_cost": 3000.0,
            "buyback_per_share": 30.0,
            "net": 5000.0,
            "roc_pct": 6.25,
            "roc_annualized": 76.04,
            "days_held": 17,
            "status": "rolled",
            "opened_at": "2026-01-15T00:00:00Z",
        }
    ]
