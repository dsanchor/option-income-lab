from __future__ import annotations

from typing import Any


def _query(container, query: str) -> list[dict[str, Any]]:
    if container is None:
        raise RuntimeError("Required Cosmos container is unavailable")
    return [
        dict(item) for item in container.query_items(
            query=query, enable_cross_partition_query=True
        )
    ]


class CosmosBackupCollector:
    """Read-only adapter over the repository's existing Cosmos service."""

    def __init__(self, cosmos) -> None:
        self.cosmos = cosmos

    def collect(self) -> dict[str, list[dict[str, Any]]]:
        symbols = getattr(self.cosmos, "container", None)
        portfolio = getattr(self.cosmos, "portfolio_container", None)
        settings = getattr(self.cosmos, "settings_container", None)
        symbol_configs = _query(
            symbols, "SELECT * FROM c WHERE c.doc_type = 'symbol_config'"
        )
        app_settings: dict[str, Any] = {}
        if settings is not None:
            try:
                doc = settings.read_item(item="app-config", partition_key="app-config")
                app_settings = {
                    key: value for key, value in doc.items()
                    if key not in {"id", "_rid", "_self", "_etag", "_attachments", "_ts", "ttl"}
                }
            except Exception as exc:
                if "not found" not in str(exc).lower() and "404" not in str(exc):
                    raise
        return {
            "accounts": _query(portfolio, "SELECT * FROM c WHERE c.doc_type = 'account'"),
            "securities": _query(symbols, "SELECT * FROM c WHERE c.doc_type = 'security_master'"),
            "symbol_configs": symbol_configs,
            "option_positions_source": symbol_configs,
            "ledger_movements": _query(
                portfolio, "SELECT * FROM c WHERE c.doc_type = 'ledger_txn'"
            ),
            "action_plans": _query(
                symbols, "SELECT * FROM c WHERE c.doc_type = 'action_plan'"
            ),
            "app_settings_source": [app_settings],
        }
