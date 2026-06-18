"""CosmosDB service layer for Option Income Lab.

Provides all database operations: symbol config CRUD, watchlist queries,
position management, activity/alert write and read, and dashboard queries.

Uses a single container ("symbols") with partition key /symbol and a hybrid
document model (symbol_config, activity, alert doc types).
"""

from azure.cosmos import CosmosClient, PartitionKey
from azure.cosmos.exceptions import CosmosResourceNotFoundError
from typing import Optional
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import logging

logger = logging.getLogger(__name__)


class CosmosDBService:
    """Service layer for CosmosDB operations."""

    def __init__(self, endpoint: str, key: str,
                 database_name: str = "stock-options-manager") -> None:
        self.client = CosmosClient(endpoint, credential=key)
        self.database = self.client.get_database_client(database_name)
        self.container = self.database.get_container_client("symbols")

        # Telemetry container — best-effort; never blocks if missing
        try:
            self.telemetry_container = self.database.get_container_client(
                "telemetry"
            )
            # Probe to confirm the container exists
            self.telemetry_container.read()
        except Exception:
            logger.warning(
                "Telemetry container not found — telemetry writes disabled. "
                "Run scripts/provision_cosmosdb.sh to create it."
            )
            self.telemetry_container = None

        # Settings container — best-effort; never blocks if missing
        try:
            self.settings_container = self.database.get_container_client(
                "settings"
            )
            # Probe to confirm the container exists
            self.settings_container.read()
        except Exception:
            logger.warning(
                "Settings container not found — settings persistence disabled. "
                "Run scripts/provision_cosmosdb.sh to create it."
            )
            self.settings_container = None

        # DGI Screener container — best-effort; never blocks if missing
        try:
            self.dgi_screener_container = self.database.get_container_client(
                "dgi_screener"
            )
            self.dgi_screener_container.read()
        except Exception:
            logger.warning(
                "DGI Screener container not found — DGI screener disabled. "
                "Run scripts/provision_cosmosdb.sh to create it."
            )
            self.dgi_screener_container = None

        # Calendar container — best-effort; never blocks if missing
        self._init_calendar_container()

    def _init_calendar_container(self):
        """Try to connect to the calendar container. Safe to call multiple times."""
        try:
            self.calendar_container = self.database.get_container_client(
                "calendar"
            )
            self.calendar_container.read()
        except Exception:
            logger.warning(
                "Calendar container not found — calendar events disabled. "
                "Run scripts/provision_cosmosdb.sh to create it."
            )
            self.calendar_container = None

    # ── Symbol Config CRUD ─────────────────────────────────────────────

    def create_symbol(self, symbol: str, exchange: str,
                      display_name: str = "",
                      covered_call: bool = False,
                      cash_secured_put: bool = False,
                      buy_tracker: bool = False) -> dict:
        """Create a new symbol config document."""
        now = datetime.utcnow().isoformat() + "Z"
        doc = {
            "id": f"config_{symbol}",
            "symbol": symbol,
            "doc_type": "symbol_config",
            "exchange": exchange,
            "display_name": display_name or symbol,
            "watchlist": {
                "covered_call": covered_call,
                "cash_secured_put": cash_secured_put,
                "buy_tracker": buy_tracker,
            },
            "telegram_notifications_enabled": True,
            "positions": [],
            "created_at": now,
            "updated_at": now,
        }
        return self.container.create_item(doc)

    def get_symbol(self, symbol: str) -> Optional[dict]:
        """Get symbol config by ticker."""
        try:
            return self.container.read_item(
                item=f"config_{symbol}",
                partition_key=symbol,
            )
        except CosmosResourceNotFoundError:
            return None

    def list_symbols(self) -> list[dict]:
        """List all symbol configs (cross-partition)."""
        query = "SELECT * FROM c WHERE c.doc_type = 'symbol_config'"
        return list(self.container.query_items(
            query=query,
            enable_cross_partition_query=True,
        ))

    def update_watchlist(self, symbol: str,
                         covered_call: Optional[bool] = None,
                         cash_secured_put: Optional[bool] = None,
                         buy_tracker: Optional[bool] = None) -> dict:
        """Update watchlist flags for a symbol."""
        doc = self.get_symbol(symbol)
        if doc is None:
            raise ValueError(f"Symbol {symbol} not found")
        if covered_call is not None:
            doc["watchlist"]["covered_call"] = covered_call
        if cash_secured_put is not None:
            doc["watchlist"]["cash_secured_put"] = cash_secured_put
        if buy_tracker is not None:
            doc["watchlist"]["buy_tracker"] = buy_tracker
        doc["updated_at"] = datetime.utcnow().isoformat() + "Z"
        return self.container.replace_item(item=doc["id"], body=doc)

    def update_symbol_enrichment(self, symbol: str, enrichment: dict) -> dict:
        """Update the enrichment data on a symbol config document."""
        doc = self.get_symbol(symbol)
        if doc is None:
            raise ValueError(f"Symbol {symbol} not found")
        doc["enrichment"] = enrichment
        doc["updated_at"] = datetime.utcnow().isoformat() + "Z"
        return self.container.replace_item(item=doc["id"], body=doc)

    def delete_symbol(self, symbol: str) -> None:
        """Delete a symbol config and ALL associated activities/alerts."""
        try:
            self.container.delete_item(
                item=f"config_{symbol}",
                partition_key=symbol,
            )
        except CosmosResourceNotFoundError:
            pass
        # Delete all activities and alerts in this partition
        query = (
            "SELECT c.id FROM c "
            "WHERE c.symbol = @symbol AND c.doc_type != 'symbol_config'"
        )
        items = list(self.container.query_items(
            query=query,
            parameters=[{"name": "@symbol", "value": symbol}],
            partition_key=symbol,
        ))
        for item in items:
            self.container.delete_item(item=item["id"], partition_key=symbol)

    # ── Watchlist Queries (used by scheduler) ──────────────────────────

    def get_covered_call_symbols(self) -> list[dict]:
        """Get all symbols enabled for covered call watching."""
        query = (
            "SELECT * FROM c WHERE c.doc_type = 'symbol_config' "
            "AND c.watchlist.covered_call = true"
        )
        return list(self.container.query_items(
            query=query,
            enable_cross_partition_query=True,
        ))

    def get_cash_secured_put_symbols(self) -> list[dict]:
        """Get all symbols enabled for cash-secured put watching."""
        query = (
            "SELECT * FROM c WHERE c.doc_type = 'symbol_config' "
            "AND c.watchlist.cash_secured_put = true"
        )
        return list(self.container.query_items(
            query=query,
            enable_cross_partition_query=True,
        ))

    def get_buy_tracker_symbols(self) -> list[dict]:
        """Get all symbols enabled for buy tracker watching."""
        query = (
            "SELECT * FROM c WHERE c.doc_type = 'symbol_config' "
            "AND c.watchlist.buy_tracker = true"
        )
        return list(self.container.query_items(
            query=query,
            enable_cross_partition_query=True,
        ))

    def get_symbols_with_active_positions(self,
                                          position_type: str) -> list[dict]:
        """Get symbol configs that have active positions of a given type.

        Args:
            position_type: "call" or "put"

        Returns:
            List of symbol_config documents with at least one active position
            matching the type. Adds ``_active_positions`` key with the filtered
            list for caller convenience.
        """
        query = (
            "SELECT * FROM c WHERE c.doc_type = 'symbol_config' "
            "AND ARRAY_LENGTH(c.positions) > 0"
        )
        results = list(self.container.query_items(
            query=query,
            enable_cross_partition_query=True,
        ))
        filtered: list[dict] = []
        for doc in results:
            active = [
                p for p in doc.get("positions", [])
                if p["type"] == position_type and p["status"] == "active"
            ]
            if active:
                doc["_active_positions"] = active
                filtered.append(doc)
        return filtered

    # ── Position Management ────────────────────────────────────────────

    @staticmethod
    def _generate_position_id(symbol: str, position_type: str,
                              strike: float, expiration: str) -> str:
        """Generate unique position ID with timestamp to prevent collisions."""
        exp_compact = expiration.replace("-", "")
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        return f"pos_{symbol}_{position_type}_{strike}_{exp_compact}_{timestamp}"

    def add_position(self, symbol: str, position_type: str,
                     strike: float, expiration: str,
                     notes: str = "",
                     source: dict | None = None) -> dict:
        """Add an open position to a symbol."""
        doc = self.get_symbol(symbol)
        if doc is None:
            raise ValueError(f"Symbol {symbol} not found")

        position_id = self._generate_position_id(symbol, position_type, strike, expiration)

        position = {
            "position_id": position_id,
            "type": position_type,
            "strike": strike,
            "expiration": expiration,
            "opened_at": datetime.utcnow().isoformat() + "Z",
            "status": "active",
            "notes": notes,
        }
        if source is not None:
            position["source"] = source
        doc["positions"].append(position)
        doc["updated_at"] = datetime.utcnow().isoformat() + "Z"
        return self.container.replace_item(item=doc["id"], body=doc)

    def roll_position(self, symbol: str, old_position_id: str,
                      new_type: str, new_strike: float, new_expiration: str,
                      source: dict | None = None,
                      closing_source: dict | None = None,
                      notes: str = "") -> dict:
        """Roll a position: close old + create new with full traceability."""
        doc = self.get_symbol(symbol)
        if doc is None:
            raise ValueError(f"Symbol {symbol} not found")

        # Find and validate old position
        old_pos = None
        for pos in doc.get("positions", []):
            if pos["position_id"] == old_position_id:
                old_pos = pos
                break
        if old_pos is None:
            raise ValueError(f"Position {old_position_id} not found")
        if old_pos.get("status") != "active":
            raise ValueError(f"Position {old_position_id} is not active")

        # Generate new position ID with timestamp for uniqueness
        new_position_id = self._generate_position_id(symbol, new_type, new_strike, new_expiration)

        # Close old position (mark as rolled)
        now = datetime.utcnow().isoformat() + "Z"
        old_pos["status"] = "rolled"
        old_pos["closed_at"] = now
        if closing_source is not None:
            old_pos["closing_source"] = closing_source
        old_pos["rolled_to"] = new_position_id

        # Create new position
        new_pos = {
            "position_id": new_position_id,
            "type": new_type,
            "strike": new_strike,
            "expiration": new_expiration,
            "opened_at": now,
            "status": "active",
            "notes": notes,
            "rolled_from": old_position_id,
        }
        if source is not None:
            new_pos["source"] = source
        doc["positions"].append(new_pos)

        doc["updated_at"] = now
        return self.container.replace_item(item=doc["id"], body=doc)

    def close_position(self, symbol: str, position_id: str) -> dict:
        """Mark a position as closed."""
        doc = self.get_symbol(symbol)
        if doc is None:
            raise ValueError(f"Symbol {symbol} not found")

        found = False
        for pos in doc.get("positions", []):
            if pos["position_id"] == position_id:
                if pos.get("status") == "closed":
                    # Position is already closed, just return success
                    logger.warning("Position %s is already closed", position_id)
                    return doc
                pos["status"] = "closed"
                pos["closed_at"] = datetime.utcnow().isoformat() + "Z"
                found = True
                # Don't break - handle any duplicate IDs if they exist
        
        if not found:
            raise ValueError(f"Position {position_id} not found")

        doc["updated_at"] = datetime.utcnow().isoformat() + "Z"
        return self.container.replace_item(item=doc["id"], body=doc)

    def update_position_notes(self, symbol: str, position_id: str,
                              notes: str) -> dict:
        """Update the notes field on a position."""
        doc = self.get_symbol(symbol)
        if doc is None:
            raise ValueError(f"Symbol {symbol} not found")

        found = False
        for pos in doc.get("positions", []):
            if pos["position_id"] == position_id:
                pos["notes"] = notes
                found = True
                break

        if not found:
            raise ValueError(f"Position {position_id} not found")

        doc["updated_at"] = datetime.utcnow().isoformat() + "Z"
        return self.container.replace_item(item=doc["id"], body=doc)

    def update_position_premium(self, symbol: str, position_id: str,
                                premium: float) -> dict:
        """Update the premium on a position's source."""
        doc = self.get_symbol(symbol)
        if doc is None:
            raise ValueError(f"Symbol {symbol} not found")

        found = False
        for pos in doc.get("positions", []):
            if pos["position_id"] == position_id:
                if "source" not in pos:
                    pos["source"] = {}
                pos["source"]["premium"] = premium
                found = True
                break

        if not found:
            raise ValueError(f"Position {position_id} not found")

        doc["updated_at"] = datetime.utcnow().isoformat() + "Z"
        return self.container.replace_item(item=doc["id"], body=doc)

    def update_position_buyback_cost(self, symbol: str, position_id: str,
                                      buyback_cost: float) -> dict:
        """Update the buyback cost on a rolled position."""
        doc = self.get_symbol(symbol)
        if doc is None:
            raise ValueError(f"Symbol {symbol} not found")

        found = False
        for pos in doc.get("positions", []):
            if pos["position_id"] == position_id:
                if not pos.get("rolled_to"):
                    raise ValueError(f"Position {position_id} was not rolled")
                pos["buyback_cost"] = buyback_cost
                found = True
                break

        if not found:
            raise ValueError(f"Position {position_id} not found")

        doc["updated_at"] = datetime.utcnow().isoformat() + "Z"
        return self.container.replace_item(item=doc["id"], body=doc)

    def delete_position(self, symbol: str, position_id: str) -> dict:
        """Remove a position and all linked activities/alerts from a symbol."""
        doc = self.get_symbol(symbol)
        if doc is None:
            raise ValueError(f"Symbol {symbol} not found")

        doc["positions"] = [
            p for p in doc.get("positions", [])
            if p["position_id"] != position_id
        ]
        doc["updated_at"] = datetime.utcnow().isoformat() + "Z"
        result = self.container.replace_item(item=doc["id"], body=doc)

        # Cascade: delete all activities linked to this position
        dec_query = (
            "SELECT c.id FROM c "
            "WHERE c.doc_type = 'activity' AND c.position_id = @position_id"
        )
        activities = list(self.container.query_items(
            query=dec_query,
            parameters=[{"name": "@position_id", "value": position_id}],
            partition_key=symbol,
        ))
        activity_ids = {d["id"] for d in activities}

        # TODO: Remove after migration - cascade delete of alerts
        # In unified schema, alerts are activities with is_alert=true,
        # so this cascade step becomes unnecessary
        if activity_ids:
            id_list = ", ".join(f"'{did}'" for did in activity_ids)
            sig_query = (
                f"SELECT c.id FROM c "
                f"WHERE c.doc_type = 'alert' "
                f"AND c.activity_id IN ({id_list})"
            )
            alerts = list(self.container.query_items(
                query=sig_query,
                parameters=[],
                partition_key=symbol,
            ))
            for alt in alerts:
                self.container.delete_item(
                    item=alt["id"], partition_key=symbol)

        for act in activities:
            self.container.delete_item(item=act["id"], partition_key=symbol)

        logger.info(
            "Cascade-deleted position %s: %d activities, %d alerts removed",
            position_id, len(activities),
            len(alerts) if activity_ids else 0,
        )
        return result

    def delete_activities_by_agent_type(
        self, symbol: str, agent_type: str
    ) -> tuple[int, int]:
        """Cascade-delete all activities (and their alerts) for a given agent type on a symbol."""
        dec_query = (
            "SELECT c.id FROM c "
            "WHERE c.doc_type = 'activity' AND c.agent_type = @agent_type"
        )
        activities = list(self.container.query_items(
            query=dec_query,
            parameters=[{"name": "@agent_type", "value": agent_type}],
            partition_key=symbol,
        ))
        activity_ids = {d["id"] for d in activities}

        sig_count = 0
        # TODO: Remove after migration - cascade delete of alerts
        # In unified schema, alerts are activities with is_alert=true
        if activity_ids:
            id_list = ", ".join(f"'{did}'" for did in activity_ids)
            sig_query = (
                f"SELECT c.id FROM c "
                f"WHERE c.doc_type = 'alert' "
                f"AND c.activity_id IN ({id_list})"
            )
            alerts = list(self.container.query_items(
                query=sig_query,
                parameters=[],
                partition_key=symbol,
            ))
            for alt in alerts:
                self.container.delete_item(
                    item=alt["id"], partition_key=symbol)
            sig_count = len(alerts)

        for act in activities:
            self.container.delete_item(item=act["id"], partition_key=symbol)

        logger.info(
            "Cascade-deleted agent_type '%s' for %s: %d activities, %d alerts removed",
            agent_type, symbol, len(activities), sig_count,
        )
        return len(activities), sig_count

    # ── Position Snapshots ────────────────────────────────────────────

    def write_position_snapshot(self, symbol: str, position_id: str,
                                snapshot_data: dict) -> None:
        """Write a position snapshot document (best-effort, never raises)."""
        if not position_id:
            logger.warning(
                "Skipping position snapshot for %s: missing position_id",
                symbol,
            )
            return

        ts = snapshot_data.get("timestamp") or datetime.utcnow().strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        ts_compact = ts.replace("-", "").replace(":", "").replace("T", "_")[:15]
        doc_id = f"{symbol}_snapshot_{position_id}_{ts_compact}"

        doc = {
            "id": doc_id,
            "symbol": symbol,
            "doc_type": "position_snapshot",
            "position_id": position_id,
            "timestamp": ts,
            **snapshot_data,
        }
        doc["id"] = doc_id
        doc["symbol"] = symbol
        doc["doc_type"] = "position_snapshot"
        doc["position_id"] = position_id
        doc["timestamp"] = ts

        try:
            self.container.create_item(doc)
        except Exception as exc:
            logger.warning(
                "Position snapshot write failed for %s/%s: %s",
                symbol, position_id, exc,
            )

    def get_position_snapshots(self, symbol: str, position_id: str,
                               limit: int = 100) -> list:
        """Get recent position snapshots for a single position, newest first."""
        query = (
            f"SELECT TOP {int(limit)} * FROM c "
            "WHERE c.doc_type = 'position_snapshot' AND c.position_id = @position_id "
            "ORDER BY c.timestamp DESC"
        )
        return list(self.container.query_items(
            query=query,
            parameters=[
                {"name": "@position_id", "value": position_id},
            ],
            partition_key=symbol,
        ))

    def update_snapshot_dps(self, symbol: str, position_id: str,
                            dps_score: int) -> None:
        """Update the most recent snapshot with the DPS score (best-effort)."""
        try:
            snaps = self.get_position_snapshots(symbol, position_id, limit=1)
            if not snaps:
                return
            doc = snaps[0]
            doc["dps_score"] = dps_score
            self.container.replace_item(item=doc["id"], body=doc)
        except Exception as exc:
            logger.warning(
                "DPS score update failed for %s/%s: %s",
                symbol, position_id, exc,
            )

    # ── Activity / Alert Write ─────────────────────────────────────────

    def write_activity(self, symbol: str, agent_type: str,
                       activity_data: dict,
                       timestamp: str | None = None,
                       ttl_seconds: int | None = None) -> dict:
        """Write a activity document.

        Args:
            symbol: Ticker symbol (partition key).
            agent_type: One of "covered_call", "cash_secured_put",
                "buy_tracker", "open_call_monitor", "open_put_monitor".
            activity_data: Full activity dict from agent output.
            timestamp: Override timestamp (ISO format). Defaults to now.
            ttl_seconds: Optional TTL in seconds for automatic expiry.

        Returns:
            The created CosmosDB document.
        """
        ts = timestamp or datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        ts_compact = ts.replace("-", "").replace(":", "").replace("T", "_")[:15]

        position_id = activity_data.get("position_id", "")
        id_suffix = f"_{position_id}" if position_id else ""

        doc_id = f"{symbol}_{agent_type}{id_suffix}_{ts_compact}"

        doc: dict = {
            "id": doc_id,
            "symbol": symbol,
            "doc_type": "activity",
            "agent_type": agent_type,
            "timestamp": ts,
            **activity_data,
        }
        # The **activity_data spread above can silently overwrite earlier keys.
        # Reassert all routing/identity fields so LLM-generated dicts never
        # corrupt id, doc_type, symbol, agent_type, or timestamp.
        # NOTE: is_alert is intentionally NOT reasserted — it's a dynamic field
        # computed by agent_runner based on the activity type.
        doc["id"] = doc_id
        doc["timestamp"] = ts
        doc["doc_type"] = "activity"
        doc["symbol"] = symbol
        doc["agent_type"] = agent_type

        if ttl_seconds is not None:
            doc["ttl"] = ttl_seconds

        return self.container.upsert_item(doc)

    def mark_as_alert(self, symbol: str, activity_id: str,
                      alert_data: dict) -> dict:
        """Mark an existing activity as an alert with enrichment data.
        
        Replaces write_alert() in the unified schema model where alerts
        are not separate documents but activities with is_alert=true.
        """
        act_doc = self.container.read_item(activity_id, partition_key=symbol)
        act_doc["is_alert"] = True
        for key in ("confidence",):
            if key in alert_data:
                act_doc[key] = alert_data[key]
        return self.container.replace_item(item=act_doc["id"], body=act_doc)

    def update_activity_field(self, doc_id: str, symbol: str,
                              field: str, value) -> bool:
        """Update a single field on an existing activity document.

        Reads the document, adds/updates the field, and writes it back.
        Returns True on success, False on failure.
        """
        try:
            doc = self.container.read_item(doc_id, partition_key=symbol)
            doc[field] = value
            self.container.replace_item(item=doc["id"], body=doc)
            return True
        except Exception:
            logger.warning(
                "Failed to update field '%s' on doc %s (symbol=%s)",
                field, doc_id, symbol, exc_info=True,
            )
            return False

    # TODO: Remove after migration - kept for backwards compatibility
    def write_alert(self, symbol: str, agent_type: str,
                     alert_data: dict, activity_id: str,
                     timestamp: str | None = None) -> dict:
        """DEPRECATED: Use mark_as_alert() instead.
        
        Write a alert document linked to a activity.
        This method is kept temporarily for backwards compatibility during
        the migration to unified schema. Will be removed after migration.
        """
        ts = timestamp or datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        ts_compact = ts.replace("-", "").replace(":", "").replace("T", "_")[:15]

        doc_id = f"sig_{symbol}_{agent_type}_{ts_compact}"

        doc: dict = {
            "id": doc_id,
            "symbol": symbol,
            "doc_type": "alert",
            "agent_type": agent_type,
            "timestamp": ts,
            "activity_id": activity_id,
            **alert_data,
        }
        doc["id"] = doc_id
        doc["timestamp"] = ts
        doc["doc_type"] = "alert"
        doc["symbol"] = symbol
        doc["agent_type"] = agent_type
        doc["activity_id"] = activity_id

        alert_doc = self.container.create_item(doc)

        try:
            act_doc = self.container.read_item(activity_id,
                                               partition_key=symbol)
            act_doc["is_alert"] = True
            self.container.replace_item(item=act_doc["id"], body=act_doc)
        except Exception:
            pass

        return alert_doc

    # ── Activity / Alert Read (context injection) ──────────────────────

    def get_recent_activities(self, symbol: str, agent_type: str,
                             max_entries: int = 20,
                             position_id: str | None = None,
                             include_alerts: bool = False) -> list[dict]:
        """Get recent activities for a symbol+agent, newest first.

        For position monitors, optionally filter by position_id.
        When include_alerts is False (default), excludes alert entries.
        When True, returns all activities regardless of is_alert flag.
        """
        conditions = [
            "c.doc_type = 'activity'",
            "c.agent_type = @agent_type",
        ]
        if not include_alerts:
            conditions.append(
                "(c.is_alert = false OR NOT IS_DEFINED(c.is_alert))")

        params: list[dict] = [
            {"name": "@agent_type", "value": agent_type},
        ]
        if position_id:
            conditions.append("c.position_id = @position_id")
            params.append({"name": "@position_id", "value": position_id})

        query = (
            f"SELECT TOP @limit * FROM c WHERE {' AND '.join(conditions)} "
            "ORDER BY c.timestamp DESC"
        )
        params.append({"name": "@limit", "value": max_entries})

        return list(self.container.query_items(
            query=query,
            parameters=params,
            partition_key=symbol,
        ))

    def get_recent_alerts(self, symbol: str, agent_type: str,
                           max_entries: int = 10) -> list[dict]:
        """Get recent alerts for a symbol+agent, newest first."""
        query = (
            "SELECT TOP @limit * FROM c "
            "WHERE c.doc_type = 'activity' AND c.is_alert = true "
            "AND c.agent_type = @agent_type "
            "ORDER BY c.timestamp DESC"
        )
        return list(self.container.query_items(
            query=query,
            parameters=[
                {"name": "@agent_type", "value": agent_type},
                {"name": "@limit", "value": max_entries},
            ],
            partition_key=symbol,
        ))

    def delete_activity(self, activity_id: str, symbol: str) -> None:
        """Delete a single activity document by ID."""
        self.container.delete_item(item=activity_id, partition_key=symbol)

    # ── Single-Document Lookups ────────────────────────────────────────

    def get_activity_by_id(self, activity_id: str) -> dict | None:
        """Get a single activity by its document ID (cross-partition)."""
        query = "SELECT * FROM c WHERE c.id = @id AND c.doc_type = 'activity'"
        results = list(self.container.query_items(
            query=query,
            parameters=[{"name": "@id", "value": activity_id}],
            enable_cross_partition_query=True,
        ))
        return results[0] if results else None

    def get_banner(self) -> dict | None:
        """Get the current dashboard banner document."""
        try:
            return self.container.read_item(
                item="dashboard_banner",
                partition_key="_system",
            )
        except CosmosResourceNotFoundError:
            return None

    def save_banner(self, items: list[dict], model: str | None = None) -> dict:
        """Upsert the current dashboard banner document."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        doc = {
            "id": "dashboard_banner",
            "symbol": "_system",
            "doc_type": "banner",
            "timestamp": now,
            "updated_at": now,
            "items": items,
        }
        if model:
            doc["model"] = model
        return self.container.upsert_item(doc)

    # ── Dashboard Queries ──────────────────────────────────────────────

    def get_all_alerts(self, agent_type: str | None = None,
                        since: str | None = None,
                        limit: int = 100) -> list[dict]:
        """Get alerts across all symbols (cross-partition query)."""
        conditions = ["c.doc_type = 'activity'", "c.is_alert = true"]
        params: list[dict] = []
        if agent_type:
            conditions.append("c.agent_type = @agent_type")
            params.append({"name": "@agent_type", "value": agent_type})
        if since:
            conditions.append("c.timestamp >= @since")
            params.append({"name": "@since", "value": since})

        query = (
            f"SELECT TOP @limit * FROM c WHERE {' AND '.join(conditions)} "
            "ORDER BY c.timestamp DESC"
        )
        params.append({"name": "@limit", "value": limit})

        return list(self.container.query_items(
            query=query,
            parameters=params,
            enable_cross_partition_query=True,
        ))

    def get_all_activities(self, agent_type: str | None = None,
                          since: str | None = None,
                          limit: int = 100) -> list[dict]:
        """Get activities across all symbols (cross-partition query).
        
        Returns all activities, including both alerts and non-alerts.
        """
        conditions = ["c.doc_type = 'activity'"]
        params: list[dict] = []
        if agent_type:
            conditions.append("c.agent_type = @agent_type")
            params.append({"name": "@agent_type", "value": agent_type})
        if since:
            conditions.append("c.timestamp >= @since")
            params.append({"name": "@since", "value": since})

        query = (
            f"SELECT TOP @limit * FROM c WHERE {' AND '.join(conditions)} "
            "ORDER BY c.timestamp DESC"
        )
        params.append({"name": "@limit", "value": limit})

        return list(self.container.query_items(
            query=query,
            parameters=params,
            enable_cross_partition_query=True,
        ))

    def count_alerts_by_symbol(self, agent_type: str,
                                since: str | None = None) -> dict[str, int]:
        """Count alerts per symbol for dashboard aggregation."""
        conditions = ["c.doc_type = 'activity'", "c.is_alert = true",
                      "c.agent_type = @agent_type"]
        params: list[dict] = [{"name": "@agent_type", "value": agent_type}]
        if since:
            conditions.append("c.timestamp >= @since")
            params.append({"name": "@since", "value": since})

        query = (
            f"SELECT c.symbol, COUNT(1) as count FROM c "
            f"WHERE {' AND '.join(conditions)} GROUP BY c.symbol"
        )
        results = list(self.container.query_items(
            query=query,
            parameters=params,
            enable_cross_partition_query=True,
        ))
        return {r["symbol"]: r["count"] for r in results}

    def get_recent_activities_by_symbol(self, limit_per_symbol: int = 3) -> dict[str, list[dict]]:
        """Get N most recent activities per agent_type per symbol (cross-partition query).
        
        Fetches up to `limit_per_symbol` activities for EACH active agent_type within
        each symbol. This ensures all agent types (covered_call, cash_secured_put,
        buy_tracker, open_call_monitor, open_put_monitor) are represented in the
        summary data,
        even when one type has more recent activity than others.
        
        Args:
            limit_per_symbol: Number of activities to retrieve per agent_type per symbol (default: 3)
        
        Returns:
            Dictionary mapping symbol -> list of activity documents (newest first).
            Each symbol may have up to (limit_per_symbol × number_of_active_agent_types) activities.
            Excludes alerts (is_alert=true).
        """
        symbols_query = "SELECT DISTINCT c.symbol FROM c WHERE c.doc_type = 'symbol_config'"
        symbols = list(self.container.query_items(
            query=symbols_query,
            enable_cross_partition_query=True,
        ))
        
        # Known agent types to query
        agent_types = ["covered_call", "cash_secured_put", "buy_tracker", "open_call_monitor", "open_put_monitor"]
        
        result = {}
        for sym_doc in symbols:
            symbol = sym_doc["symbol"]
            all_activities = []
            
            # Query each agent_type separately to ensure representation
            for agent_type in agent_types:
                query = (
                    "SELECT TOP @limit * FROM c "
                    "WHERE c.doc_type = 'activity' "
                    "AND c.agent_type = @agent_type "
                    "AND (c.is_alert = false OR NOT IS_DEFINED(c.is_alert)) "
                    "ORDER BY c.timestamp DESC"
                )
                activities = list(self.container.query_items(
                    query=query,
                    parameters=[
                        {"name": "@limit", "value": limit_per_symbol},
                        {"name": "@agent_type", "value": agent_type}
                    ],
                    partition_key=symbol,
                ))
                all_activities.extend(activities)
            
            # Sort merged results by timestamp (newest first) and store if non-empty
            if all_activities:
                all_activities.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
                result[symbol] = all_activities
        
        return result

    # ── Reports ──────────────────────────────────────────────────────

    def write_report(self, symbol: str, report_markdown: str,
                     cached_resources: list | None = None,
                     timestamp: str | None = None) -> dict:
        """Write a generated report document.

        Args:
            symbol: Ticker symbol (partition key).
            report_markdown: Full markdown report from the agent.
            cached_resources: List of data provider resources served from cache.
            timestamp: Override timestamp (ISO format). Defaults to now.

        Returns:
            The created CosmosDB document.
        """
        ts = timestamp or datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        ts_compact = ts.replace("-", "").replace(":", "").replace("T", "_")[:15]

        doc = {
            "id": f"{symbol}_report_{ts_compact}",
            "symbol": symbol,
            "doc_type": "report",
            "timestamp": ts,
            "report": report_markdown,
            "cached_resources": cached_resources or [],
        }
        return self.container.create_item(doc)

    def get_latest_report(self, symbol: str) -> dict | None:
        """Get the most recent report for a symbol.

        Returns:
            The report document, or None if no report exists.
        """
        query = (
            "SELECT TOP 1 * FROM c "
            "WHERE c.doc_type = 'report' "
            "ORDER BY c.timestamp DESC"
        )
        results = list(self.container.query_items(
            query=query,
            parameters=[],
            partition_key=symbol,
        ))
        return results[0] if results else None

    # ── Technical Analysis ────────────────────────────────────────────

    def write_technical_analysis(self, symbol: str, analysis_markdown: str,
                                 cached_resources: list | None = None,
                                 timestamp: str | None = None) -> dict:
        """Write a generated technical analysis document.

        Args:
            symbol: Ticker symbol (partition key).
            analysis_markdown: Full markdown analysis from the agent.
            cached_resources: List of data provider resources served from cache.
            timestamp: Override timestamp (ISO format). Defaults to now.

        Returns:
            The created CosmosDB document.
        """
        ts = timestamp or datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        ts_compact = ts.replace("-", "").replace(":", "").replace("T", "_")[:15]

        doc = {
            "id": f"{symbol}_technical_analysis_{ts_compact}",
            "symbol": symbol,
            "doc_type": "technical_analysis",
            "timestamp": ts,
            "analysis": analysis_markdown,
            "cached_resources": cached_resources or [],
        }
        return self.container.create_item(doc)

    # ── Telemetry ──────────────────────────────────────────────────────

    def write_telemetry(self, metric_type: str, data: dict) -> None:
        """Write a telemetry document (best-effort, never raises)."""
        if self.telemetry_container is None:
            return
        try:
            doc = {
                "id": str(uuid4()),
                "metric_type": metric_type,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "ttl": 2592000,  # 30 days
                **data,
            }
            self.telemetry_container.create_item(doc)
        except Exception as exc:
            logger.warning("Telemetry write failed (%s): %s", metric_type, exc)

    def get_telemetry_stats(self) -> dict:
        """Aggregate telemetry stats bucketed by today / 7 days / 30 days.

        Returns:
            {
              "tv_fetch": {resource: {"today": {...}, "7d": {...}, "30d": {...}}},
              "agent_run": {agent_type: {"today": {...}, "7d": {...}, "30d": {...}}},
            }
        """
        if self.telemetry_container is None:
            return {}

        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        cutoffs = {
            "today": today_start.isoformat(),
            "7d": (now - timedelta(days=7)).isoformat(),
            "30d": (now - timedelta(days=30)).isoformat(),
        }
        since = cutoffs["30d"]

        def _empty_tv_buckets() -> dict:
            return {k: {"total_duration": 0.0, "total_size": 0, "count": 0, "error_count": 0}
                    for k in cutoffs}

        def _empty_ar_buckets() -> dict:
            return {k: {"total_duration": 0.0, "count": 0} for k in cutoffs}

        try:
            # ── TV fetch stats ────────────────────────────────────────
            tv_query = (
                "SELECT * FROM c "
                "WHERE c.metric_type = 'tv_fetch' AND c.timestamp >= @since"
            )
            tv_docs = list(self.telemetry_container.query_items(
                query=tv_query,
                parameters=[{"name": "@since", "value": since}],
                enable_cross_partition_query=True,
            ))

            tv_agg: dict[str, dict] = {}
            for doc in tv_docs:
                res = doc.get("resource", "unknown")
                agg = tv_agg.setdefault(res, _empty_tv_buckets())
                ts = doc.get("timestamp", "")
                dur = doc.get("duration_seconds", 0)
                size = doc.get("response_size_chars", 0)
                error = doc.get("error", False)
                for period, cutoff in cutoffs.items():
                    if ts >= cutoff:
                        b = agg[period]
                        b["total_duration"] += dur
                        b["total_size"] += size
                        b["count"] += 1
                        if error:
                            b["error_count"] += 1

            tv_stats: dict[str, dict] = {}
            for res, periods in tv_agg.items():
                tv_stats[res] = {}
                for period, b in periods.items():
                    c = b["count"] or 1
                    tv_stats[res][period] = {
                        "avg_duration": round(b["total_duration"] / c, 1),
                        "avg_size": round(b["total_size"] / c),
                        "count": b["count"],
                        "error_count": b["error_count"],
                    }

            # ── Agent run stats ───────────────────────────────────────
            ar_query = (
                "SELECT * FROM c "
                "WHERE c.metric_type = 'agent_run' AND c.timestamp >= @since"
            )
            ar_docs = list(self.telemetry_container.query_items(
                query=ar_query,
                parameters=[{"name": "@since", "value": since}],
                enable_cross_partition_query=True,
            ))

            ar_agg: dict[str, dict] = {}
            for doc in ar_docs:
                at = doc.get("agent_type", "unknown")
                agg = ar_agg.setdefault(at, _empty_ar_buckets())
                ts = doc.get("timestamp", "")
                dur = doc.get("duration_seconds", 0)
                for period, cutoff in cutoffs.items():
                    if ts >= cutoff:
                        b = agg[period]
                        b["total_duration"] += dur
                        b["count"] += 1

            ar_stats: dict[str, dict] = {}
            for at, periods in ar_agg.items():
                ar_stats[at] = {}
                for period, b in periods.items():
                    c = b["count"] or 1
                    ar_stats[at][period] = {
                        "avg_duration": round(b["total_duration"] / c, 1),
                        "count": b["count"],
                    }

            return {"tv_fetch": tv_stats, "agent_run": ar_stats}

        except Exception as exc:
            logger.warning("Telemetry stats query failed: %s", exc)
            return {}

    def get_recent_fetch_errors(self, limit: int = 10) -> list:
        """Get most recent data fetch errors for display."""
        if self.telemetry_container is None:
            return []
        try:
            query = (
                "SELECT c.symbol, c.resource, c.timestamp, c.duration_seconds "
                "FROM c WHERE c.metric_type = 'tv_fetch' AND c.error = true "
                "ORDER BY c.timestamp DESC OFFSET 0 LIMIT @limit"
            )
            docs = list(self.telemetry_container.query_items(
                query=query,
                parameters=[{"name": "@limit", "value": limit}],
                enable_cross_partition_query=True,
            ))
            return docs
        except Exception:
            return []

    # ── Settings Management ────────────────────────────────────────────

    def get_settings(self) -> dict:
        """Read the app settings document from CosmosDB.
        
        Returns empty dict if not found or if settings container is unavailable.
        """
        if self.settings_container is None:
            return {}
        try:
            doc = self.settings_container.read_item(
                item="app-config",
                partition_key="app-config",
            )
            # Return copy without internal fields
            result = {k: v for k, v in doc.items() if k not in ("id", "_rid", "_self", "_etag", "_attachments", "_ts")}
            return result
        except CosmosResourceNotFoundError:
            return {}
        except Exception as exc:
            logger.warning("Failed to read settings from CosmosDB: %s", exc)
            return {}

    def save_settings(self, settings: dict) -> dict:
        """Write the full settings document to CosmosDB (upsert).
        
        Args:
            settings: The settings dict to persist (should not contain 'id' key)
        
        Returns:
            The saved document
        """
        if self.settings_container is None:
            raise RuntimeError("Settings container not available")
        
        doc = {"id": "app-config", **settings}
        return self.settings_container.upsert_item(doc)

    def merge_defaults(self, defaults: dict) -> dict:
        """Deep-merge: read current settings from CosmosDB.
        
        For any key in `defaults` that doesn't exist in the stored doc, add it.
        Never overwrite existing keys. This is called at startup with the
        config.yaml contents (excluding credentials) as defaults.
        
        Args:
            defaults: Default settings from config.yaml (excluding azure/cosmosdb)
        
        Returns:
            The merged settings document
        """
        if self.settings_container is None:
            logger.warning("Settings container unavailable — skipping merge_defaults")
            return {}
        
        stored = self.get_settings()
        
        def deep_merge(base: dict, new_vals: dict) -> dict:
            """Recursively merge new_vals into base.
            
            Rules:
            - If key exists in new_vals but not in base → add it
            - If key exists in both and both are dicts → recurse
            - If key exists in both and base value is NOT a dict → keep base (never overwrite)
            - If key exists in base but not in new_vals → keep it
            """
            result = base.copy()
            for key, val in new_vals.items():
                if key not in result:
                    # Key doesn't exist in stored → add it
                    result[key] = val
                elif isinstance(result[key], dict) and isinstance(val, dict):
                    # Both are dicts → recurse
                    result[key] = deep_merge(result[key], val)
                # else: key exists in stored and is not a dict → keep stored value
            return result
        
        merged = deep_merge(stored, defaults)
        
        # Save the merged result back to CosmosDB
        try:
            self.save_settings(merged)
            logger.info("Settings merged and saved to CosmosDB")
        except Exception as exc:
            logger.warning("Failed to save merged settings to CosmosDB: %s", exc)
        
        return merged

    # ── Data Provider Health Status ───────────────────────────────────

    def get_tv_health(self) -> dict:
        """Read the data provider health status document.

        Returns dict with keys: is_healthy, last_check, last_error, last_error_time.
        Returns a default "healthy" dict if no status doc exists.
        """
        if self.settings_container is None:
            return {"is_healthy": True, "last_check": None}
        try:
            doc = self.settings_container.read_item(
                item="tv-health", partition_key="tv-health",
            )
            return {k: v for k, v in doc.items()
                    if k not in ("id", "_rid", "_self", "_etag", "_attachments", "_ts")}
        except CosmosResourceNotFoundError:
            return {"is_healthy": True, "last_check": None}
        except Exception as exc:
            logger.warning("Failed to read TV health status: %s", exc)
            return {"is_healthy": True, "last_check": None}

    def update_tv_health(self, *, is_healthy: bool,
                         error: str | None = None) -> None:
        """Upsert the data provider health status document (best-effort)."""
        if self.settings_container is None:
            return
        now = datetime.now(timezone.utc).isoformat()
        try:
            current = self.get_tv_health()
            doc = {
                "id": "tv-health",
                "is_healthy": is_healthy,
                "last_check": now,
            }
            if is_healthy:
                doc["last_success"] = now
                doc["last_error"] = current.get("last_error")
                doc["last_error_time"] = current.get("last_error_time")
            else:
                doc["last_error"] = error or "403 Forbidden"
                doc["last_error_time"] = now
                doc["last_success"] = current.get("last_success")
            self.settings_container.upsert_item(doc)
        except Exception as exc:
            logger.warning("Failed to update TV health status: %s", exc)

    # ── DGI Screener ──────────────────────────────────────────────────

    def get_dgi_top(self) -> list[dict]:
        """Get current DGI top entries from the dgi_screener container."""
        if self.dgi_screener_container is None:
            return []
        try:
            query = (
                "SELECT * FROM c WHERE c.doc_type = 'dgi_top' "
                "ORDER BY c.rank ASC"
            )
            return list(self.dgi_screener_container.query_items(
                query=query,
                enable_cross_partition_query=True,
            ))
        except Exception as exc:
            logger.warning("Failed to read DGI top entries: %s", exc)
            return []

    def upsert_dgi_top(self, entries: list) -> None:
        """Upsert each DGI top entry (id: top_{symbol})."""
        if self.dgi_screener_container is None:
            logger.warning("DGI Screener container unavailable — skipping upsert")
            return
        for entry in entries:
            try:
                self.dgi_screener_container.upsert_item(entry)
            except Exception as exc:
                logger.warning(
                    "Failed to upsert DGI entry %s: %s",
                    entry.get("symbol", "?"), exc,
                )

    def delete_dgi_dropped(self, symbols: list) -> None:
        """Delete entries no longer in the top 20."""
        if self.dgi_screener_container is None:
            return
        for symbol in symbols:
            try:
                self.dgi_screener_container.delete_item(
                    item=f"top_{symbol}",
                    partition_key=symbol,
                )
            except CosmosResourceNotFoundError:
                pass
            except Exception as exc:
                logger.warning("Failed to delete DGI entry %s: %s", symbol, exc)

    def write_dgi_snapshot(self, snapshot: dict) -> None:
        """Write a daily snapshot document to the dgi_screener container."""
        if self.dgi_screener_container is None:
            logger.warning("DGI Screener container unavailable — skipping snapshot")
            return
        try:
            self.dgi_screener_container.upsert_item(snapshot)
        except Exception as exc:
            logger.warning("Failed to write DGI snapshot: %s", exc)

    def is_symbol_watched(self, symbol: str) -> bool:
        """Check if a symbol exists in the symbols container."""
        try:
            doc = self.get_symbol(symbol)
            return doc is not None
        except Exception:
            return False

    # ── Calendar Events ───────────────────────────────────────────────

    def upsert_calendar_event(self, symbol: str, event_type: str,
                              date: str, has_active_position: bool = False):
        """Upsert a calendar event (earnings or ex_dividend) for a symbol."""
        if not self.calendar_container:
            self._init_calendar_container()
        if not self.calendar_container:
            return None
        doc = {
            "id": f"{symbol}_{event_type}",
            "symbol": symbol,
            "type": event_type,
            "date": date,
            "has_active_position": has_active_position,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            return self.calendar_container.upsert_item(doc)
        except Exception as exc:
            logger.warning("Failed to upsert calendar event for %s: %s", symbol, exc)
            return None

    def get_calendar_events(self) -> list:
        """Return all calendar events."""
        if not self.calendar_container:
            self._init_calendar_container()
        if not self.calendar_container:
            return []
        try:
            return list(self.calendar_container.query_items(
                query="SELECT c.symbol, c.type, c.date, c.has_active_position, c.updated_at FROM c",
                enable_cross_partition_query=True,
            ))
        except Exception as exc:
            logger.warning("Failed to read calendar events: %s", exc)
            return []

    def delete_calendar_events_for_symbol(self, symbol: str):
        """Delete all calendar events for a symbol."""
        if not self.calendar_container:
            return
        try:
            items = list(self.calendar_container.query_items(
                query="SELECT c.id FROM c WHERE c.symbol = @symbol",
                parameters=[{"name": "@symbol", "value": symbol}],
                partition_key=symbol,
            ))
            for item in items:
                self.calendar_container.delete_item(
                    item=item["id"], partition_key=symbol
                )
        except Exception as exc:
            logger.warning("Failed to delete calendar events for %s: %s", symbol, exc)
