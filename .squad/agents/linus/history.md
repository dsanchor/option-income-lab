# Linus — History

## Project Context
- **Project:** options-agent
- **User:** dsanchor
- **Role:** Quantitative strategy, prompt, provider, and financial-contract owner
- **Stack:** Python, Microsoft Agent Framework, Azure/Gemini providers, yfinance,
  TradingView, Alpha Vantage, React

## Core Context

- Maintains strategy instruction parity across Massive, TradingView, Alpha
  Vantage, and yfinance while adapting only provider-specific data gathering.
- Prompt contracts must use deterministic evidence paths, explicit missing-data
  semantics, stable JSON output, and strategy-valid decisions.
- Major strategy work includes earnings gates, 21–35 DTE roll targets with a
  45 DTE cap, premium-first roll policy, near-ATM hysteresis, contrarian quality
  auditing, DGI screening, and Buy Tracker DGI alignment.
- Major data work includes provider migration, options-chain schema/filtering,
  last-known-good quote preservation, market-hours probing, dividend evidence,
  and position snapshots.
- Major UI work includes roll tables, activity/DPS chat prompts, timeline
  charts, settings, suitability display, and options-chain context.

## Durable Decisions and Patterns

- Earnings gates are mandatory and symmetric where applicable. Post-earnings
  0–7 days is blocked; 8–13 days is cautionary.
- Roll candidates prioritize annualized return while respecting target DTE,
  expiration, held-contract exclusion, and premium/quote verification.
- Position monitors use hysteresis near ATM to avoid flip-flopping on marginal
  price crossings.
- Alert/activity lookups must identify the event by a field unique to that
  event; generic fields create cooldown and history bugs.
- Third-party endpoint interception needs broad matching, field aliases,
  diagnostics, and graceful fallback because provider schemas drift.
- Background failures must retain tracebacks; silent container or persistence
  skips are data-loss risks.
- Percentage storage/display contracts must be explicit. Apply field-specific
  formatting before generic string conversion.

## Recent Learnings

### 2026-08-17 — Buy Tracker Prompt and Provider Contract
- Centralized the five-dimension DGI rules so Buy Tracker prompt surfaces cannot
  drift: 0–2 WAIT, 3–4 BUY, and gated 5/5 promotion.
- RSI is excluded from Value, permissive MA-summary scoring was removed from
  Trend, and earnings belongs only to Calendar.
- Production evidence uses provider `Buy` signals for `MACD.macd` and `Stoch.K`,
  plus positive annual DPS, latest DPS, and dividend-growth years.
- Payout eligibility is the exact finite `<=75%` rule. Missing required proxy
  evidence fails promotion closed; missing explicit cut state alone does not.

### 2026-08-17 — Open Call Executable Ask Safety
- Buyback P&L, profit CLOSE, and roll economics require a numeric, finite,
  positive current ask.
- Bid, midpoint, last/model price, and ask=0 are not executable substitutes.
  Bid=0 with ask>0 remains valid and P&L is ask-based.
- Incomplete quotes degrade to WAIT/incomplete data unless an independent risk
  path supports CLOSE; unavailable economics stay null and are disclosed.

### 2026-08-09 — Options Chain Last-Known-Good Cache
- Refresh merges fresh fields with prior valid contract fields instead of
  replacing the entire cache with provider zeros.
- Quote/Greek zeros may fall back to prior valid values; naturally changing
  fields such as volume and open interest remain fresh.
- Cache TTL controls staleness, not availability. Stale data is served while a
  deduplicated refresh runs; truly expired contract buckets are pruned.

### 2026-08-08 — Suitability Semantics
- Symbols-page suitability is deterministic Entry + Momentum classification,
  independent of watchlist membership and option-chain delta filters.
- Oversold and overextended modifiers route to Ideal Puts/Calls; No Puts/Calls
  require unmodified bearish/bullish momentum.

## Provider and Prompt Guardrails
- Keep strategy logic and output schemas provider-independent.
- Never infer positive evidence from prose or missing fields.
- Use canonical raw paths and validate finite numeric values.
- Preserve explicit risk precedence over favorable scoring.
- Document provider limitations instead of fabricating unavailable metrics.
