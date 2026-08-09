---
name: risk-flags
description: Unified risk-flag taxonomy for sell, monitor, and roll agents.
---

## Purpose

Load this skill when you need to assign or validate `risk_flags`.

Export only the flags that truly apply, as a **flat array**.

## Open-Position Monitor Flags

### Position
- `approaching_itm`
- `high_delta`
- `low_extrinsic`
- `near_atm_stability`
- `profit_optimization`

### Earnings / Calendar
- `earnings_before_expiry` *(legacy alias of `earnings_within_dte`)*
- `earnings_within_dte`
- `earnings_approaching`
- `earnings_soon`
- `earnings_imminent`
- `unknown_earnings`
- `ex_dividend_risk`
- `catalyst_pending`

### Technical
- `breakout_momentum`
- `breakdown_momentum`
- `resistance_level`
- `support_break`

### Fundamental
- `fundamental_deterioration`
- `analyst_downgrade`

## Roll-Management Flags

Carry all relevant monitor flags forward, and add only when applicable:
- `ultra_defensive_roll`
- `no_viable_roll`
- `profit_optimization`
- `close_for_profit`

## New Sell / Watcher Flags

### Timing / Calendar
- `earnings_within_dte`
- `earnings_approaching`
- `earnings_soon`
- `earnings_imminent`
- `post_earnings_exp`
- `unknown_earnings`
- `earnings_uncertainty` *(legacy / generic timing concern)*
- `catalyst_pending`
- `dte_exceeded`

### Technical
- `breakout_momentum`
- `breakdown_momentum`
- `support_breaking`
- `resistance_level`
- `approaching_strike`

### Volatility
- `low_iv`
- `iv_too_low`
- `iv_crush_pending`

### Fundamental
- `weak_fundamentals`
- `analyst_downgrade`
- `earnings_miss`
- `fundamental_deterioration`

### Position / Pricing
- `high_delta`
- `low_extrinsic`
- `profit_optimization`

### Data Quality
- `incomplete_data`
- `unknown_earnings`
- `no_options_data`
- `incomplete_analyst_data`

## Earnings Flag Definitions

- `earnings_within_dte`: expiration is after earnings; the position spans the event
- `earnings_approaching`: earnings are 15-30 days away and require planning / caution
- `earnings_soon`: earnings are 7-14 days away and urgency is rising
- `earnings_imminent`: earnings are <7 days away and risk is acute
- `unknown_earnings`: no earnings date is available, so use conservative assumptions
- `post_earnings_exp`: expiration is far enough after earnings to be considered only with caution

## Usage Rules

- Prefer the newer canonical names over legacy ones.
- Do not add flags merely because a concept was discussed.
- If no meaningful risk flags apply, return `[]`.
