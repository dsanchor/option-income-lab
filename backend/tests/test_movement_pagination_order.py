from src.portfolio.cosmos_portfolio import CosmosPortfolioService


class MovementContainer:
    def __init__(self, rows):
        self.rows = list(rows)

    def read(self):
        return {}

    def query_items(self, query="", parameters=None, **kwargs):
        if "COUNT(1)" in query:
            return iter([len(self.rows)])
        return iter(dict(row) for row in self.rows)


def _movement(movement_id, trade_date):
    return {
        "id": movement_id,
        "doc_type": "ledger_txn",
        "account_id": "account-1",
        "txn_type": "BUY",
        "trade_date": trade_date,
    }


def test_equal_date_offset_pages_use_id_as_stable_tie_breaker():
    rows = [
        _movement("movement-b", "2026-09-24"),
        _movement("movement-d", "2026-09-24"),
        _movement("movement-a", "2026-09-24"),
        _movement("movement-c", "2026-09-24"),
        _movement("older", "2026-09-23"),
    ]
    service = CosmosPortfolioService(MovementContainer(rows), None)

    page_1, total_1 = service.get_movements(limit=2, offset=0)
    page_2, total_2 = service.get_movements(limit=2, offset=2)
    page_3, total_3 = service.get_movements(limit=2, offset=4)

    ids = [row["id"] for row in page_1 + page_2 + page_3]
    assert ids == [
        "movement-d",
        "movement-c",
        "movement-b",
        "movement-a",
        "older",
    ]
    assert len(ids) == len(set(ids)) == 5
    assert (total_1, total_2, total_3) == (5, 5, 5)


def test_pagination_order_is_independent_of_container_iteration_order():
    ascending = [
        _movement("movement-a", "2026-09-24"),
        _movement("movement-b", "2026-09-24"),
        _movement("movement-c", "2026-09-24"),
    ]
    descending = list(reversed(ascending))

    first = CosmosPortfolioService(MovementContainer(ascending), None)
    second = CosmosPortfolioService(MovementContainer(descending), None)

    first_page, _ = first.get_movements(limit=2, offset=1)
    second_page, _ = second.get_movements(limit=2, offset=1)

    assert [row["id"] for row in first_page] == ["movement-b", "movement-a"]
    assert first_page == second_page
