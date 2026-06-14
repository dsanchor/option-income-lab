---
name: data-source
description: Reference for the pre-fetched Yahoo Finance payload structure used by the agents.
---

## Purpose

Load this skill when you need to interpret the structure of the pre-fetched market data included in the prompt.

All market data is already **pre-fetched from Yahoo Finance** and embedded directly in the message. Do **not** fetch more data. Work only with the provided payload.

## Shared Characteristics

- Values may show `—` outside market hours; note this and continue with available data.
- Technical indicators are already pre-calculated.
- Pivot points are already calculated.
- If a section contains `[ERROR: ...]`, acknowledge it and continue with the remaining sections.

## Common Sections

### 1. OVERVIEW PAGE

Typical uses:
- current price
- market cap / valuation context when available
- sector / industry
- dividend yield
- 52-week range
- volume
- exchange / ticker metadata
- next earnings date

### 2. TECHNICALS PAGE

Typical uses:
- overall technical summary
- oscillator summary and individual values
- moving average summary and individual values
- pivot points (Classic, Fibonacci, Camarilla, Woodie, DM)
- support / resistance analysis
- momentum and trend assessment

Common indicators include:
- RSI
- MACD
- Stochastic
- CCI
- ADX
- SMA / EMA 10-200
- Ichimoku / VWMA / Hull MA when provided

### 3. FORECAST PAGE

Typical uses:
- analyst ratings / consensus
- price targets
- EPS history and surprises
- revenue context
- upcoming earnings date
- sentiment shifts that affect assignment or entry quality

### 4. DIVIDENDS PAGE

Typical uses:
- ex-dividend date
- pay date
- dividend amount
- yield and payout context
- assignment / calendar risk for income strategies

### 5. OPTIONS CHAIN

When present, the chain is structured JSON grouped by expiration and side.

Typical uses:
- strike selection
- premium selection (`bid` for sell decisions, `ask` for buyback checks)
- delta / IV / theta / gamma / vega / rho review
- volume / open interest review
- roll candidate verification

If the prompt only includes a **current contract** block instead of the full chain, use that point-in-time contract data for monitoring decisions.

## Strategy Notes

- Covered calls focus on call-side resistance, assignment risk, ex-dividend risk, and upside capture.
- Cash-secured puts focus on put-side support, downside assignment quality, and oversold entries.
- Open position monitors may receive only the overview, technicals, forecast, and current-contract subset needed for the decision.
