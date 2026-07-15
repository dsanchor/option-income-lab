# Output

[← Back to README](../README.md)

All activities and alerts are stored in Azure CosmosDB. The web dashboard provides a UI for browsing them, or query directly via the CosmosDB Data Explorer.

### Activity Documents (complete audit trail)

Every agent run creates an `activity` document per symbol in CosmosDB. Query by `doc_type = "activity"` and filter by `agent_type` or `symbol`.

### Alert Documents (actionable alerts only)

Actionable activities (SELL, ROLL, CLOSE) also create a `alert` document linked to the activity. Query by `doc_type = "alert"` for the dashboard's primary read path.

### Example Activity Object

Each activity document in CosmosDB:
```json
{
  "timestamp": "2026-03-27T00:00:00Z",
  "symbol": "MO",
  "exchange": "NYSE",
  "agent": "covered_call",
  "activity": "SELL",
  "strike": 60.0,
  "expiration": "2026-04-17",
  "iv": 32.5,
  "reason": "IV Rank elevated with strong technical support; selling 30-delta call",
  "confidence": "high",
  "risk_flags": [],
  "risk_rating": 3,
  "risk_rating_breakdown": {
    "volatility": 1,
    "assignment": 0,
    "technical": 1,
    "calendar": 0,
    "sentiment": 1
  }
}
```

For `SELL` activities, `strike`, `expiration`, premium, `risk_rating`, and `risk_rating_breakdown` fields are populated. A corresponding `alert` document is also created with the actionable subset of the activity data.

### Telegram Notifications

When a `SELL`, `ROLL`, or `CLOSE` alert is generated, a Telegram notification is sent if enabled (see [Configuration](#configuration)). The message includes the symbol, action, and key details (strike, expiration, risk flags). Sell alerts include the risk rating (`Risk: X/10`) and premium. Roll alerts include roll economics (buyback cost, new premium, net credit/debit) and assignment risk level. Close alerts show the buyback cost for the position exit. When a supervisor review produces a **MODERATE** or **STRONG** challenge, the supervisor one-liner is appended to the alert. When the Alpha Advisor finds a **MODERATE** or **STRONG** opportunity, its one-liner is also included. **WEAK/NONE** results are omitted from Telegram to reduce noise — they remain accessible in the web dashboard.