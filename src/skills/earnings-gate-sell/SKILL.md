---
name: earnings-gate-sell
description: Mandatory earnings-first gate for new covered call and cash-secured put sell decisions.
---

## Purpose

Load this skill **before** evaluating technicals, IV, fundamentals, or premiums for a new short-premium entry.

**Core principle:** the key risk is not that earnings are nearby — the key risk is opening a position that remains **open during earnings**.

## Step 1: Extract Earnings Date

- Find **Next Earnings Date** in the overview or forecast data.
- If the date is missing:
  - set `earnings_date = "unknown"`
  - add `unknown_earnings`
  - use conservative short DTE
  - downgrade confidence to `medium`

## Step 2: Calculate Timing

- `days_to_earnings` = calendar days until earnings
- `expiration_to_earnings_gap` = `earnings_date - candidate_expiration`
  - **Positive** → expiration is before earnings (safe)
  - **Negative** → expiration is after earnings (the position spans earnings)

## Step 3: Apply the Watcher Earnings Decision Matrix

| Days to Earnings | Expiration vs Earnings | Gate Result | Risk Flag | Confidence Impact | Rationale |
|---|---|---|---|---|---|
| **>30 days** | Expiration before earnings | **OPEN NORMALLY** | None | No impact | Earnings are far enough away. |
| **>30 days** | Expiration AFTER earnings AND DTE ≤ 45 AND ≥14 days after earnings | **ALLOWED WITH CAUTION** | `post_earnings_exp` | Downgrade one level | Only acceptable when IV-crush effects should be settled. |
| **>30 days** | Expiration AFTER earnings AND (DTE > 45 OR <14 days after earnings) | **BLOCKED → WAIT** | `earnings_within_dte` | N/A | Position would span earnings without a safe buffer. |
| **15-30 days** | Expiration ≥5 days BEFORE earnings | **OPEN NORMALLY** | None | No impact | Comfortable pre-earnings buffer. |
| **15-30 days** | Expiration 3-4 days BEFORE earnings | **ALLOWED** | `earnings_approaching` | No impact | Tight but acceptable. |
| **15-30 days** | Expiration 0-2 days BEFORE earnings | **BLOCKED → WAIT** | `earnings_within_dte` | N/A | Insufficient buffer for earnings-date movement. |
| **15-30 days** | Expiration AFTER earnings (any) | **BLOCKED → WAIT** | `earnings_within_dte` | N/A | Position would remain open during earnings. |
| **7-14 days** | Expiration ≥5 days BEFORE earnings | **ALLOWED** | `earnings_approaching` | No impact | Pre-earnings IV can still be harvested safely. |
| **7-14 days** | Expiration 3-4 days BEFORE earnings | **ALLOWED WITH CAUTION** | `earnings_soon` | No impact | Tight but still viable if the rest of the setup is strong. |
| **7-14 days** | Expiration 0-2 days BEFORE earnings | **BLOCKED → WAIT** | `earnings_within_dte` | N/A | Too close to the event. |
| **7-14 days** | Expiration AFTER earnings (any) | **BLOCKED → WAIT** | `earnings_within_dte` | N/A | Do not span the earnings event. |
| **<7 days** | Expiration ≥3 days BEFORE earnings | **ALLOWED WITH CAUTION** | `earnings_imminent` | No impact | Earnings are close, but the option still expires safely before them. |
| **<7 days** | Expiration 0-2 days BEFORE earnings | **BLOCKED → WAIT** | `earnings_imminent`, `earnings_within_dte` | N/A | Too close to a possible date shift. |
| **<7 days** | Expiration AFTER earnings (any) | **BLOCKED → WAIT** | `earnings_imminent`, `earnings_within_dte` | N/A | Opening through imminent earnings is not allowed. |
| **0-2 days (just passed)** | Any | **IDEAL — OPEN** | None | No impact | Post-earnings uncertainty is resolved. |
| **Unknown** | N/A | **CONSERVATIVE DTE** | `unknown_earnings` | Downgrade to `medium` | Keep DTE short because the event timing is unknown. |

## Hard Override

If the gate result is **BLOCKED → WAIT**:
- output `WAIT` immediately
- do **not** continue into technical, IV, or premium analysis
- explain what would unblock the setup
- still populate `earnings_analysis`

No combination of bullish technicals, strong fundamentals, or attractive IV overrides an earnings block.

## Key Expiration Rules

- **Hard maximum:** `DTE ≤ 45`
- Prefer expirations **before earnings**
- Target a **5+ day** buffer when possible, **3+ day minimum**
- **Never** choose 0-2 days before earnings
- Post-earnings expirations are only acceptable when they are **≥14 days after earnings**, **DTE ≤ 45**, and earnings are still **>30 days away**
- If no safe expiration exists inside the 45 DTE cap, output `WAIT`

## Strategy-Specific Note

- Covered calls: pre-earnings IV can improve premiums, but only if the position expires safely before earnings.
- Cash-secured puts: post-earnings is often ideal because uncertainty is resolved, but do not force a post-earnings trade that violates the DTE cap.

## Required Output Object

Always populate:

```json
"earnings_analysis": {
  "next_earnings_date": "YYYY-MM-DD or unknown",
  "days_to_earnings": 15,
  "expiration_date": "YYYY-MM-DD",
  "expiration_to_earnings_gap": 5,
  "earnings_gate_result": "ALLOWED",
  "earnings_risk_flag": "earnings_approaching"
}
```

Allowed `earnings_gate_result` values:
- `OPEN_NORMALLY`
- `ALLOWED`
- `ALLOWED_WITH_CAUTION`
- `ALLOWED_POST_EARNINGS`
- `BLOCKED`
- `IDEAL`
- `CONSERVATIVE_DTE`
