from textwrap import dedent


def get_monitor_earnings_gate() -> str:
    """Return the shared monitor earnings-gate decision framework."""
    return dedent("""## ⚠️ MANDATORY EARNINGS GATE — CHECK FIRST, BEFORE ALL OTHER ANALYSIS

**This gate runs BEFORE any moneyness, delta, or technical analysis. If the gate says CLOSE or ROLL immediately, that is the PRIMARY recommendation regardless of other signals.**

### Step 1: Extract Earnings Date
- Find "Next Earnings Date" from the OVERVIEW data (`"Next Earnings Date"`) or forecast data
- If no earnings date is found: set `earnings_date = "unknown"`, apply flag `unknown_earnings`, downgrade confidence to "medium"

### Step 2: Calculate Earnings Timing
- `days_to_earnings` = calendar days from today to next earnings date
- `expiration_to_earnings_gap` = earnings_date - position_expiration_date
  - **Positive value** = position expires BEFORE earnings → SAFE (no earnings risk for this position)
  - **Negative value** = position expires AFTER earnings → RISK (position spans earnings)

### Step 3: Apply the Monitor Earnings Decision Matrix

| Days to Earnings | Expiration vs Earnings | Position Moneyness | Gate Result | Risk Flag(s) | Confidence Impact | Rationale |
|---|---|---|---|---|---|---|
| **>30 days** | Expiration BEFORE earnings | Any | **HOLD** — no concern | None | No impact | Position expires well before earnings. No action needed. |
| **>30 days** | Expiration ≥14 days AFTER earnings | Any | **FLAG** — awareness only | `earnings_within_dte` | No impact | Position spans earnings but expires well after IV crush settles. Revisit as earnings approach. |
| **>30 days** | Expiration 0-13 days AFTER earnings | **OTM (delta <0.30)** | **FLAG** — medium risk | `earnings_within_dte` | No impact | Spans earnings and expires in post-earnings chaos zone, but OTM. Monitor moneyness closely. |
| **>30 days** | Expiration 0-13 days AFTER earnings | **Near ATM/ITM (delta ≥0.30)** | **ROLL recommended** | `earnings_within_dte` | Downgrade one level | Spans earnings AND expires in chaos zone while near the money. Roll to pre-earnings or ≥14 days post-earnings expiration. |
| **15-30 days** | Expiration ≥5 days BEFORE earnings | Any | **HOLD** — safe buffer | None | No impact | Position closes well before earnings. |
| **15-30 days** | Expiration 3-4 days BEFORE earnings | Any | **HOLD with caution** | `earnings_approaching` | No impact | Tight but safe — 3-day minimum buffer holds. Monitor for earnings date shifts. |
| **15-30 days** | Expiration 0-2 days BEFORE earnings | Any | **FLAG** — tight buffer | `earnings_approaching` | No impact | Very tight before earnings. Monitor for date shifts. If date shifts, may need to roll. |
| **15-30 days** | Expiration ≥14 days AFTER earnings | **OTM (delta <0.30)** | **FLAG** — medium risk | `earnings_within_dte` | No impact | Spans earnings but well OTM and expires after IV settles. Monitor delta trend. |
| **15-30 days** | Expiration ≥14 days AFTER earnings | **Near ATM/ITM (delta ≥0.30)** | **ROLL recommended** | `earnings_approaching`, `earnings_within_dte` | Downgrade one level | Near the money spanning earnings. Even though exp is far post-earnings, gap risk at ATM is real. Roll to pre-earnings expiration. |
| **15-30 days** | Expiration 0-13 days AFTER earnings | **OTM (delta <0.30)** | **FLAG** — medium-high risk | `earnings_within_dte` | Downgrade one level | Spans earnings AND expires in chaos zone. OTM helps but tighten monitoring. Consider rolling if delta increases toward 0.30+. |
| **15-30 days** | Expiration 0-13 days AFTER earnings | **Near ATM/ITM (delta ≥0.30)** | **ROLL urgently** | `earnings_approaching`, `earnings_within_dte` | Downgrade one level | Near-money position spanning earnings and expiring in post-earnings chaos zone. Roll to pre-earnings or ≥14 days post. |
| **7-14 days** | Expiration ≥3 days BEFORE earnings | Any | **HOLD** — expires before event | `earnings_soon` | No impact | Position expires before earnings. No gap risk. |
| **7-14 days** | Expiration 0-2 days BEFORE earnings | Any | **FLAG** — very tight | `earnings_soon` | No impact | Expires just before earnings. Watch for date shifts carefully. |
| **7-14 days** | Expiration ≥14 days AFTER earnings | **OTM (delta <0.30)** | **FLAG** — medium-high risk | `earnings_soon`, `earnings_within_dte` | No impact | Spans earnings but OTM and far post. If at 50%+ profit, recommend CLOSE or set `close_for_profit_recommended`. |
| **7-14 days** | Expiration ≥14 days AFTER earnings | **Near ATM/ITM (delta ≥0.30)** | **ROLL urgently** | `earnings_soon`, `earnings_within_dte` | Downgrade one level | Near-money spanning imminent earnings. Roll to pre-earnings expiration. |
| **7-14 days** | Expiration 0-13 days AFTER earnings | **OTM (delta <0.30)** | **FLAG** — high risk | `earnings_soon`, `earnings_within_dte` | Downgrade one level | Spans earnings and expires in chaos zone. Even OTM, this is elevated risk. If at 50%+ profit, recommend CLOSE or set `close_for_profit_recommended`. |
| **7-14 days** | Expiration 0-13 days AFTER earnings | **Near ATM/ITM (delta ≥0.30)** | **CLOSE or ROLL immediately** | `earnings_soon`, `earnings_within_dte` | Downgrade to "low" | Near-money, imminent earnings, expires in chaos zone. Act NOW. |
| **<7 days** | Expiration BEFORE earnings | Any | **HOLD** — expires before event | `earnings_imminent` | No impact | Position expires before imminent earnings. No gap risk. |
| **<7 days** | Expiration AFTER earnings | **OTM (delta <0.25)** | **FLAG** — high risk, trader decides | `earnings_imminent`, `earnings_within_dte` | Downgrade one level | Well OTM but spans imminent earnings. Flag as high risk — let trader decide. If at 50%+ profit, recommend CLOSE or set `close_for_profit_recommended`. |
| **<7 days** | Expiration AFTER earnings | **Near ATM/ITM (delta ≥0.25)** | **CLOSE or ROLL immediately** | `earnings_imminent`, `earnings_within_dte` | Downgrade to "low" | CRITICAL: near-money position spanning imminent earnings. Act now. |
| **0-2 days (just passed)** | Any | Any | **HOLD** — earnings resolved | None | No impact | Uncertainty resolved. IV crush favorable for short positions. |
| **Unknown** | N/A | Any | **CONSERVATIVE approach** | `unknown_earnings` | Downgrade to "medium" | Cannot assess earnings risk. If DTE >21, consider rolling to shorter DTE. |

### Step 4: HARD OVERRIDE RULE

⛔ **CRITICAL OVERRIDE — applies ONLY when ALL three conditions are met: (1) position expires AFTER earnings, (2) earnings are <7 days away, AND (3) position is near ATM/ITM (delta ≥0.25). When all three conditions are true: CLOSE or ROLL immediately regardless of other factors.**

**For positions that span earnings but are well OTM (delta <0.25-0.30), the earnings gate produces a FLAG with risk level, NOT a forced action.** The trader decides whether to roll or hold based on:
- Current profit level (TastyTrade rule: if at 50%+ profit, close for profit or hand off with `close_for_profit_recommended`)
- Delta trend (is moneyness deteriorating?)
- IV trend (is IV still expanding, making the position more expensive to close?)
- The specific earnings history of this company (serial beaters vs. volatile reporters)

If the gate result is **FLAG** (OTM position spanning earnings):
- Include the earnings risk flag(s) in `risk_flags`
- Set `earnings_gate_result` to indicate the risk level (FLAG, FLAG_MEDIUM, FLAG_HIGH)
- DO NOT force a ROLL or CLOSE — provide the risk assessment and let other technical factors contribute to the decision
- If other factors (delta approaching 0.30, price momentum toward strike, rising IV) ALSO suggest ROLL, then the combined signal is strong — recommend ROLL
- If other factors are favorable (stable delta, price moving away from strike, falling IV), HOLD is reasonable despite spanning earnings

If the gate result is **ROLL recommended** (near ATM/ITM, 15-30 days):
- This is a strong signal to ROLL but NOT an absolute override
- If the position is at 50%+ profit → recommend CLOSE for profit or hand off with `close_for_profit_recommended` and approximate `profit_level_pct` (TastyTrade winner management)
- Factor into overall WAIT/ROLL decision alongside other technical signals

If the gate result is **CLOSE or ROLL immediately** (<7 days, ATM/ITM):
- This IS a hard override — act regardless of other signals
- The only exception: if position is at 80%+ profit, CLOSE for profit or set `close_for_profit_recommended: true` with `profit_level_pct`

### Roll Target Rules (when ROLL is recommended)

When the earnings gate recommends ROLL, the roll target expiration MUST follow these rules:

1. **PREFERRED: Roll to pre-earnings expiration** — Select an expiration ≥3 days before earnings. This captures remaining pre-earnings IV premium and avoids the earnings event entirely.
2. **ACCEPTABLE: Roll to ≥14 days after earnings** — If no suitable pre-earnings expiration exists (e.g., earnings are <7 days away), roll to an expiration at least 14 days after earnings so IV crush has settled.
3. **NEVER: Roll to 0-13 days after earnings** — This is the post-earnings chaos zone. IV is crushed, price is volatile, and the position has no time advantage. This roll target is BLOCKED.
4. **TastyTrade profit rule**: If the position is at 50%+ profit, CLOSE for profit instead of rolling, or hand off with `close_for_profit_recommended: true` and `profit_level_pct`. Taking a winner off the table is better than rolling into earnings uncertainty.

The priority order for roll targets: (1) pre-earnings with ≥5 day buffer, (2) pre-earnings with 3-4 day buffer, (3) ≥14 days post-earnings, (4) CLOSE for profit or hand off with `close_for_profit_recommended` if 50%+ achieved.

### Step 5: Populate Mandatory `earnings_analysis` Object (REQUIRED IN EVERY RESPONSE)

```json
"earnings_analysis": {
    "next_earnings_date": "2026-04-15",
    "days_to_earnings": 15,
    "position_expiration": "2026-04-24",
    "expiration_to_earnings_gap": -9,
    "earnings_gate_result": "FLAG_MEDIUM",
    "earnings_risk_flag": "earnings_within_dte"
}
```
- `next_earnings_date`: The date from OVERVIEW/forecast data, or `"unknown"`
- `days_to_earnings`: Integer, or `null` if unknown
- `position_expiration`: The current position's expiration date
- `expiration_to_earnings_gap`: Positive = expires before earnings (safe), negative = expires after (risk). Null if unknown.
- `earnings_gate_result`: One of: `"HOLD"`, `"HOLD_WITH_CAUTION"`, `"FLAG"`, `"FLAG_MEDIUM"`, `"FLAG_HIGH"`, `"ROLL_RECOMMENDED"`, `"ROLL_URGENTLY"`, `"CLOSE_OR_ROLL"`, `"CONSERVATIVE"`
- `earnings_risk_flag`: The applicable flag(s), or `null` if none
""")


def get_sell_earnings_gate() -> str:
    """Return the shared sell-side earnings-gate decision framework."""
    return dedent("""## ⚠️ MANDATORY EARNINGS GATE — CHECK FIRST, BEFORE ALL OTHER ANALYSIS

**This gate runs BEFORE any technical, volatility, or fundamental analysis. If the gate says BLOCKED, STOP — output WAIT immediately. No other signal can override this gate.**

### Step 1: Extract Earnings Date
- Find "Next Earnings Date" from the OVERVIEW data or forecast data
- If no earnings date is found: set `earnings_date = "unknown"`, apply flag `unknown_earnings`, use conservative DTE (<21 days), downgrade confidence to "medium"

### Step 2: Calculate Earnings Timing
- `days_to_earnings` = calendar days from today to next earnings date
- `expiration_to_earnings_gap` = earnings_date - candidate_expiration_date
  - **Positive value** = expiration is BEFORE earnings → SAFE
  - **Negative value** = expiration is AFTER earnings → RISK

### Step 3: Apply the Watcher Earnings Decision Matrix

| Days to Earnings | Expiration vs Earnings | Gate Result | Risk Flag | Confidence Impact | Rationale |
|---|---|---|---|---|---|
| **>30 days** | Expiration before earnings | **OPEN NORMALLY** | None | No impact | Earnings far out. Capture elevated pre-earnings IV. |
| **>30 days** | Expiration AFTER earnings (any) AND DTE ≤ 45 AND ≥14 days after earnings | **ALLOWED WITH CAUTION** | `post_earnings_exp` | Downgrade one level | Far enough post-earnings for IV crush to settle. Only if DTE ≤ 45 AND technicals strongly support. |
| **>30 days** | Expiration AFTER earnings (any) AND (DTE > 45 OR <14 days after earnings) | **BLOCKED → WAIT** | `earnings_within_dte` | N/A — WAIT | Either exceeds 45 DTE hard cap, or position spans earnings without enough post-earnings buffer. WAIT for post-earnings entry instead. |
| **15-30 days** | Expiration ≥5 days BEFORE earnings | **OPEN NORMALLY** | None | No impact | Comfortable buffer. Pre-earnings IV premium is a seller's advantage. |
| **15-30 days** | Expiration 3-4 days BEFORE earnings | **ALLOWED** | `earnings_approaching` | No impact | Acceptable buffer. Earnings date announcements rarely shift by >2 days. |
| **15-30 days** | Expiration 0-2 days BEFORE earnings | **BLOCKED → WAIT** | `earnings_within_dte` | N/A — WAIT | Insufficient buffer. Earnings date could shift by 1-2 days. |
| **15-30 days** | Expiration AFTER earnings (any) | **BLOCKED → WAIT** | `earnings_within_dte` | N/A — WAIT | Position would span earnings. Select an earlier expiration. |
| **7-14 days** | Expiration ≥5 days BEFORE earnings | **ALLOWED** | `earnings_approaching` | No impact | Pre-earnings IV boost captured. Safe expiration. |
| **7-14 days** | Expiration 3-4 days BEFORE earnings | **ALLOWED WITH CAUTION** | `earnings_soon` | No impact | Tight but viable. TastyTrade approach: if technicals are strong, this is acceptable. |
| **7-14 days** | Expiration 0-2 days BEFORE earnings | **BLOCKED → WAIT** | `earnings_within_dte` | N/A — WAIT | Insufficient buffer. Earnings date could shift. |
| **7-14 days** | Expiration AFTER earnings (any) | **BLOCKED → WAIT** | `earnings_within_dte` | N/A — WAIT | Position would span earnings. Select an earlier expiration. |
| **<7 days** | Expiration ≥3 days BEFORE earnings | **ALLOWED WITH CAUTION** | `earnings_imminent` | No impact | Earnings very close but option expires safely before. Pre-earnings IV at peak — excellent premium. |
| **<7 days** | Expiration 0-2 days BEFORE earnings | **BLOCKED → WAIT** | `earnings_imminent`, `earnings_within_dte` | N/A — WAIT | Too close to earnings date. Risk of date shift. |
| **<7 days** | Expiration AFTER earnings (any) | **BLOCKED → WAIT** | `earnings_imminent`, `earnings_within_dte` | N/A — WAIT | Position would span imminent earnings. |
| **0-2 days (just passed)** | Any | **IDEAL — OPEN** | None | No impact | Post-earnings IV crush still elevated, uncertainty resolved. Best entry point. |
| **Unknown** | N/A | **CONSERVATIVE DTE** | `unknown_earnings` | Downgrade to "medium" | Use expiration <21 DTE to minimize gap risk. |

### Step 4: HARD OVERRIDE RULE

⛔ **CRITICAL: No combination of bullish technicals, strong fundamentals, or favorable IV can override an earnings BLOCK. The BLOCK applies ONLY when the option's expiration would be AFTER earnings or within 0-2 days before earnings (insufficient buffer for potential date shifts). If the option expires ≥3 days before earnings, it is eligible regardless of earnings proximity — pre-earnings IV is an advantage for sellers.**

If the gate result is **BLOCKED → WAIT**:
- Set `activity = "WAIT"` — this is FINAL. Do NOT proceed to evaluate technicals, Greeks, or premiums.
- Set `reason` to explain the earnings block (include dates and gap calculation)
- Set `waiting_for` to describe what would unblock (e.g., "post-earnings setup" or "expiration that clears earnings date")
- You MUST still complete the `earnings_analysis` object in your output

If the gate result is **ALLOWED** or **ALLOWED WITH CAUTION**:
- Proceed with full technical/volatility/fundamental analysis below
- Apply any confidence downgrade noted in the matrix
- Include the earnings risk flag in `risk_flags`

### Step 5: Populate Mandatory `earnings_analysis` Object (REQUIRED IN EVERY RESPONSE)

```json
"earnings_analysis": {
    "next_earnings_date": "2026-04-15",
    "days_to_earnings": 15,
    "expiration_date": "2026-04-10",
    "expiration_to_earnings_gap": 5,
    "earnings_gate_result": "ALLOWED",
    "earnings_risk_flag": "earnings_approaching"
}
```
- `next_earnings_date`: The date from OVERVIEW/forecast data, or `"unknown"`
- `days_to_earnings`: Integer, or `null` if unknown
- `expiration_date`: The candidate or recommended expiration date
- `expiration_to_earnings_gap`: Positive = before earnings (safe), negative = after (risk). Null if unknown.
- `earnings_gate_result`: One of: `"OPEN_NORMALLY"`, `"ALLOWED"`, `"ALLOWED_WITH_CAUTION"`, `"ALLOWED_POST_EARNINGS"`, `"BLOCKED"`, `"IDEAL"`, `"CONSERVATIVE_DTE"`
- `earnings_risk_flag`: The applicable flag from the matrix, or `null` if none
""")
