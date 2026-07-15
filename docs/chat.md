# Dual-Mode Chat Experience

[← Back to README](../README.md)

The `/chat` page now offers two distinct modes for analysis:

### Portfolio Chat

Analyze tracked symbols using your CosmosDB watchlist and position data. The chat context includes:
- Recent activities and alerts for the selected symbol
- Open positions (strike, expiration, status)
- Historical decisions and risk flags

Perfect for in-depth analysis of symbols you're actively tracking.

**How to use:**
1. Visit `/chat`
2. Click "Portfolio Chat"
3. Select a tracked symbol or ask general questions about your portfolio
4. Get insights based on your historical data and positions

### Quick Analysis

Analyze any symbol (tracked or not) using live Yahoo Finance data, without saving to your database. Quick Analysis fetches:
- Real-time overview (market cap, P/E, dividend yield, etc.)
- Technical indicators (RSI, MACD, moving averages, etc.)
- Analyst forecasts (price targets, ratings)
- Options chain (if available)
- Dividend history

Perfect for researching new symbols before committing to tracking.

**How to use:**
1. Visit `/chat`
2. Click "Quick Analysis"
3. Enter a symbol and select its market (NASDAQ, NYSE, AMEX, OTC)
4. Click "Fetch & Analyze"
5. Chat about the symbol with live data context
6. Use "Change Mode" to switch back to Portfolio Chat or select a different symbol

**Configuration:** Quick Analysis is read-only — data is fetched but never saved to CosmosDB. Rate limiting is handled gracefully with clear error messages.

### Per-Activity Chat

Accessible from individual activity detail pages via a **"Chat"** button next to the Delete button, this feature provides a read-only LLM advisory conversation about a specific agent decision. Designed for "why did the agent decide this?" questions and "what does this mean given today's market?" exploration.

**How it works:**
1. Click the "Chat" button on any activity detail page to open an ephemeral chat panel
2. Ask questions about the activity — the LLM's decision rationale, position context, or implications
3. The LLM receives two-tier context with strict separation:
   - **AGENT DECISION block (historical):** The persisted activity record and position snapshot at decision time — exact, immutable data the agent used
   - **CURRENT MARKET DATA block (live):** Re-fetched options chain and technical analysis labeled as "current, not what the agent used" — for comparing then vs. now
4. The chat maintains conversation history within the session but never persists it — close the panel and it's gone
5. **Read-only enforcement:** Zero database writes; the LLM cannot execute trades, modify positions, or alter activities

**Key principles:**
- **Historical vs. live separation:** Clearly distinguishes what the agent saw (historical snapshot) from what's happening now (live re-fetch)
- **Ephemeral by design:** No chat persistence; lightweight advisory layer without bloating activity records
- **Graceful degradation:** If live options chain or technicals are unavailable, the chat still functions with historical data only

**Configuration:** Model configurable via `activity_chat.model` in `config.yaml` (default: `gpt-5.4-mini`).