---
name: earnings-gate-monitor
description: Mandatory earnings-first decision matrix for open covered call and open cash-secured put position monitoring.
---

## Purpose

Load this skill **before any other analysis** when assessing an open short option position. The earnings gate runs before moneyness, delta, technical, or volatility analysis.

**Core principle:** the key risk is not that earnings are nearby — the key risk is that the position is **still open during earnings** while near the money.

## Step 1: Extract Earnings Date

- Find **Next Earnings Date** from the overview or forecast data.
- If no earnings date is available:
  - set `next_earnings_date = "unknown"`
  - add `unknown_earnings`
  - downgrade confidence to `medium`

## Step 2: Calculate Timing

- `days_to_earnings` = calendar days from today to the next earnings date
- `expiration_to_earnings_gap` = `earnings_date - current_position_expiration`
  - **Positive** → position expires **before** earnings (safe)
  - **Negative** → position expires **after** earnings (the position spans earnings)

For puts, use **absolute delta** when applying the matrix.

## Step 3: Apply the Monitor Earnings Decision Matrix

| Days to Earnings | Expiration vs Earnings | Position Moneyness | Gate Result | Risk Flag(s) | Confidence Impact | Rationale |
|---|---|---|---|---|---|---|
| **>30 days** | Expiration BEFORE earnings | Any | **HOLD** — no concern | None | No impact | Position expires well before earnings. No action needed. |
| **>30 days** | Expiration ≥14 days AFTER earnings | Any | **FLAG** — awareness only | `earnings_within_dte` | No impact | Position spans earnings but expires well after IV crush settles. Revisit as earnings approach. |
| **>30 days** | Expiration 0-13 days AFTER earnings | **OTM (delta <0.30)** | **FLAG** — medium risk | `earnings_within_dte` | No impact | Spans earnings and expires in the post-earnings chaos zone, but remains OTM. |
| **>30 days** | Expiration 0-13 days AFTER earnings | **Near ATM/ITM (delta ≥0.30)** | **ROLL recommended** | `earnings_within_dte` | Downgrade one level | Near the money in the chaos zone after earnings. |
| **15-30 days** | Expiration ≥5 days BEFORE earnings | Any | **HOLD** — safe buffer | None | No impact | Position closes well before earnings. |
| **15-30 days** | Expiration 3-4 days BEFORE earnings | Any | **HOLD with caution** | `earnings_approaching` | No impact | Tight but still acceptable. Monitor for date shifts. |
| **15-30 days** | Expiration 0-2 days BEFORE earnings | Any | **FLAG** — tight buffer | `earnings_approaching` | No impact | Too little buffer for date changes. |
| **15-30 days** | Expiration ≥14 days AFTER earnings | **OTM (delta <0.30)** | **FLAG** — medium risk | `earnings_within_dte` | No impact | Spans earnings but expires after IV settles. |
| **15-30 days** | Expiration ≥14 days AFTER earnings | **Near ATM/ITM (delta ≥0.30)** | **ROLL recommended** | `earnings_approaching`, `earnings_within_dte` | Downgrade one level | Near-money position spanning earnings. |
| **15-30 days** | Expiration 0-13 days AFTER earnings | **OTM (delta <0.30)** | **FLAG** — medium-high risk | `earnings_within_dte` | Downgrade one level | Spans earnings and expires in the chaos zone. |
| **15-30 days** | Expiration 0-13 days AFTER earnings | **Near ATM/ITM (delta ≥0.30)** | **ROLL urgently** | `earnings_approaching`, `earnings_within_dte` | Downgrade one level | Near-money position spanning earnings and expiring in the chaos zone. |
| **7-14 days** | Expiration ≥3 days BEFORE earnings | Any | **HOLD** — expires before event | `earnings_soon` | No impact | No earnings gap risk for this position. |
| **7-14 days** | Expiration 0-2 days BEFORE earnings | Any | **FLAG** — very tight | `earnings_soon` | No impact | Watch carefully for earnings-date movement. |
| **7-14 days** | Expiration ≥14 days AFTER earnings | **OTM (delta <0.30)** | **FLAG** — medium-high risk | `earnings_soon`, `earnings_within_dte` | No impact | OTM but still spans earnings. |
| **7-14 days** | Expiration ≥14 days AFTER earnings | **Near ATM/ITM (delta ≥0.30)** | **ROLL urgently** | `earnings_soon`, `earnings_within_dte` | Downgrade one level | Near-money position spanning imminent earnings. |
| **7-14 days** | Expiration 0-13 days AFTER earnings | **OTM (delta <0.30)** | **FLAG** — high risk | `earnings_soon`, `earnings_within_dte` | Downgrade one level | Elevated risk even when still OTM. |
| **7-14 days** | Expiration 0-13 days AFTER earnings | **Near ATM/ITM (delta ≥0.30)** | **CLOSE or ROLL immediately** | `earnings_soon`, `earnings_within_dte` | Downgrade to `low` | Near-money, imminent earnings, chaos-zone expiration. |
| **<7 days** | Expiration BEFORE earnings | Any | **HOLD** — expires before event | `earnings_imminent` | No impact | Earnings are close, but the position is not open during the event. |
| **<7 days** | Expiration AFTER earnings | **OTM (delta <0.25)** | **FLAG** — high risk, trader decides | `earnings_imminent`, `earnings_within_dte` | Downgrade one level | Well OTM but still open through imminent earnings. |
| **<7 days** | Expiration AFTER earnings | **Near ATM/ITM (delta ≥0.25)** | **CLOSE or ROLL immediately** | `earnings_imminent`, `earnings_within_dte` | Downgrade to `low` | Critical: near-money position spanning imminent earnings. |
| **0-2 days (just passed)** | Any | Any | **HOLD** — earnings resolved | None | No impact | Uncertainty resolved and IV crush is favorable for short premium. |
| **Unknown** | N/A | Any | **CONSERVATIVE** | `unknown_earnings` | Downgrade to `medium` | Cannot assess earnings timing accurately. |

## Hard Override

If **all three** are true, recommend action immediately regardless of other signals:
1. expiration is **after** earnings
2. earnings are **<7 days away**
3. position is **near ATM/ITM** (`delta ≥ 0.25` for the immediate override)

## How to Interpret Results

### FLAG

For OTM positions spanning earnings:
- include the earnings risk flag(s)
- populate `earnings_gate_result`
- do **not** force action on FLAG alone
- combine the flag with delta trend, momentum, IV trend, and profit state

### ROLL recommended / ROLL urgently

- treat as a strong action signal
- if profit is already substantial, carry `close_for_profit_recommended` and `profit_level_pct`
- still consider the broader technical context unless the hard override applies

### CLOSE or ROLL immediately

- this is a hard override
- hand off immediately to roll/close handling
- if profit is very high, include close-for-profit metadata

## Roll Target Rules When the Gate Forces Action

1. **Preferred:** roll to an expiration **before earnings** with at least a 3-day buffer
2. **Acceptable:** roll to an expiration **≥14 days after earnings**
3. **Blocked:** expirations **0-13 days after earnings**
4. If the position is already at strong profit, carry close-for-profit guidance into the handoff

Priority order:
1. pre-earnings with ≥5-day buffer
2. pre-earnings with 3-4 day buffer
3. ≥14 days post-earnings
4. close-for-profit fallback when appropriate

## Required Output Object

Always populate:

```json
"earnings_analysis": {
  "next_earnings_date": "YYYY-MM-DD or unknown",
  "days_to_earnings": 15,
  "position_expiration": "YYYY-MM-DD",
  "expiration_to_earnings_gap": -9,
  "earnings_gate_result": "FLAG_MEDIUM",
  "earnings_risk_flag": "earnings_within_dte"
}
```

Allowed `earnings_gate_result` values:
- `HOLD`
- `HOLD_WITH_CAUTION`
- `FLAG`
- `FLAG_MEDIUM`
- `FLAG_HIGH`
- `ROLL_RECOMMENDED`
- `ROLL_URGENTLY`
- `CLOSE_OR_ROLL`
- `CONSERVATIVE`
