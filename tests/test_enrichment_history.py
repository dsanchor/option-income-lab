"""Tests for enrichment history (tech-timing / momentum time-series).

Covers ``CosmosDBService.record_enrichment_snapshot`` and
``get_enrichment_history``: daily dedupe, 90-day rolling prune, chronological
ordering, and no-op on missing score.
"""

from unittest.mock import MagicMock

from src.cosmos_db import CosmosDBService, CosmosResourceNotFoundError


def _service_with_store():
    """CosmosDBService backed by an in-memory dict container."""
    service = CosmosDBService.__new__(CosmosDBService)
    store: dict = {}

    def _read_item(item, partition_key):
        if item not in store:
            raise CosmosResourceNotFoundError()
        return store[item]

    def _upsert(doc):
        store[doc["id"]] = doc
        return doc

    service.container = MagicMock()
    service.container.read_item.side_effect = _read_item
    service.container.upsert_item.side_effect = _upsert
    return service, store


def test_snapshot_creates_history_doc():
    service, store = _service_with_store()

    service.record_enrichment_snapshot("AAPL", 62.5, "Bullish", today="2026-05-01")

    doc = store["enrichhist_AAPL"]
    assert doc["doc_type"] == "enrichment_history"
    assert doc["symbol"] == "AAPL"
    assert doc["points"] == [
        {"date": "2026-05-01", "tech_timing": 62.5, "momentum": "Bullish"}
    ]


def test_same_day_overwrites_point():
    service, store = _service_with_store()

    service.record_enrichment_snapshot("AAPL", 40, "Neutral", today="2026-05-01")
    service.record_enrichment_snapshot("AAPL", 71, "Bullish", today="2026-05-01")

    points = store["enrichhist_AAPL"]["points"]
    assert len(points) == 1
    assert points[0]["tech_timing"] == 71
    assert points[0]["momentum"] == "Bullish"


def test_points_kept_in_chronological_order():
    service, store = _service_with_store()

    service.record_enrichment_snapshot("AAPL", 30, "Bearish", today="2026-05-03")
    service.record_enrichment_snapshot("AAPL", 55, "Neutral", today="2026-05-01")
    service.record_enrichment_snapshot("AAPL", 80, "Bullish", today="2026-05-02")

    dates = [p["date"] for p in store["enrichhist_AAPL"]["points"]]
    assert dates == ["2026-05-01", "2026-05-02", "2026-05-03"]


def test_prunes_points_older_than_retention_window():
    service, store = _service_with_store()

    # Seed an old point, then add a recent one 100 days later.
    service.record_enrichment_snapshot("AAPL", 50, "Neutral", today="2026-01-01")
    service.record_enrichment_snapshot("AAPL", 60, "Bullish", today="2026-04-11")

    dates = [p["date"] for p in store["enrichhist_AAPL"]["points"]]
    # 2026-01-01 is >90 days before 2026-04-11 → pruned.
    assert dates == ["2026-04-11"]


def test_none_score_is_noop():
    service, store = _service_with_store()

    result = service.record_enrichment_snapshot("AAPL", None, "Bullish")

    assert result is None
    assert "enrichhist_AAPL" not in store


def test_get_enrichment_history_empty_when_missing():
    service, _ = _service_with_store()

    assert service.get_enrichment_history("MSFT") == []


def test_get_enrichment_history_returns_sorted_points():
    service, _ = _service_with_store()
    service.record_enrichment_snapshot("AAPL", 30, "Bearish", today="2026-05-03")
    service.record_enrichment_snapshot("AAPL", 55, "Neutral", today="2026-05-01")

    history = service.get_enrichment_history("AAPL")
    assert [p["date"] for p in history] == ["2026-05-01", "2026-05-03"]
