"""Context injection adapter for agent prompts.

Replaces the file-based logger.py read functions with CosmosDB-backed
equivalents.  Each activity is presented with its alert status so the
agent knows whether it was actionable.

Context format (oldest → newest, one block per activity):
    [2026-03-28 14:30:00] WAIT
    Premium marginal at 0.8%...

    [2026-03-28 16:30:00] SELL ⚡ ALERT
    IV Rank 72, premium 2.1%...
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.cosmos_db import CosmosDBService


class ContextProvider:
    """Provides per-symbol activity/alert context for agent prompts."""

    def __init__(self, cosmos: CosmosDBService) -> None:
        self.cosmos = cosmos

    def get_context(
        self,
        symbol: str,
        agent_type: str,
        max_entries: int = 2,
        position_id: str | None = None,
    ) -> str:
        """Return formatted context string for prompt injection.

        Fetches the last *max_entries* activities for the given symbol and
        agent type.  Each activity includes its alert status (actionable
        or not).  Returned oldest-first for natural chronological reading.

        Args:
            symbol: Ticker symbol.
            agent_type: Agent type key.
            max_entries: Number of recent activities (0–5, default 2).
            position_id: Optional position filter for monitor agents.

        Returns:
            Human-readable context string, or a "no history" message.
        """
        if max_entries <= 0:
            return "Context injection disabled."

        activities = self.cosmos.get_recent_activities(
            symbol, agent_type, max_entries, position_id
        )
        if not activities:
            return "No previous activities recorded."

        blocks: list[str] = []
        for d in reversed(activities):  # newest-first → oldest-first
            ts = d.get("timestamp", "?")
            activity = d.get("activity", "?")
            is_alert = d.get("is_alert", False)

            header = f"[{ts}] {activity}"
            if is_alert:
                header += " ⚡ ALERT"

            reason = d.get("reason", "")
            if not reason:
                reason = json.dumps(
                    {k: v for k, v in d.items()
                     if k not in ("id", "symbol", "doc_type", "_rid",
                                  "_self", "_etag", "_attachments", "_ts")},
                    indent=2,
                )

            blocks.append(f"{header}\n{reason}")

        return "\n\n".join(blocks)
