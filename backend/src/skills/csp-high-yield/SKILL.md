---
name: csp-high-yield
description: Cash-secured put parameters for High Yield-category stocks (dividend yield ≥4%).
---

## Category Profile: High Yield

High Yield stocks pay generous dividends (≥4%) and are often mature, income-focused companies (e.g., MO, T, VZ, XOM).
Assignment means owning a high-income stock — attractive for income portfolios.
But high yield can signal distress — fundamental quality check is critical.

## Adjusted Parameters

**All existing rules remain in force** — earnings gate, DTE ≤ 45 hard cap, investment worthiness check, and all WAIT triggers. This skill ONLY adjusts thresholds within those rules.

### Delta Range
- **Target**: -0.25 to -0.35 (standard to aggressive)
- **After ex-dividend drop**: -0.30 to -0.40 (buy the dividend dip)
- **Rationale**: You WANT to own these for income. Assignment at strike = starting the dividend income stream at a discount.

### Premium Thresholds
- **Minimum premium**: ≥ 1.0% of strike price for 30–45 DTE
- **Minimum annualized return**: ≥ 12%
- **WAIT threshold**: premium < 0.6% of strike price
- **Rationale**: High Yield stocks have moderate-to-good IV. Reasonable premium thresholds keep opportunities flowing.

### IV Requirements
- **IV Rank**: ≥ 25 preferred (reduced from default 50)
- **WAIT if**: IV Rank < 15 AND premium below minimum
- **Rationale**: These stocks often have moderate IV. Lower bar ensures consistent put selling.

### Technical Adjustments
- **RSI < 45**: Good opportunity (relaxed from default RSI < 40)
- **RSI < 30**: Excellent if fundamentals intact — yield is very attractive at lower prices
- **At SMA 200**: Good support level for established income stocks
- **Oversold after ex-dividend**: IDEAL — stock dropped by dividend amount, likely to recover

### Market State Guidance
When evaluating the current market state for this High Yield stock:
- **Oversold (RSI < 35)**: Excellent if dividend is safe — sell aggressively
- **Neutral (RSI 35–55)**: Standard opportunity at support levels
- **Weak market / sector rotation out**: Verify dividend safety before selling puts
- **High IV**: Favorable — capture elevated premium on an income stock
- **After ex-dividend drop**: Special opportunity — stock mechanically drops, sell puts on the dip

### Fundamental Quality — CRITICAL
- **High yield can be a value trap** — MUST verify dividend sustainability
- **Payout ratio check**: 
  - < 75%: Safe — proceed normally
  - 75–85%: Caution — add `risk_flags: ["fundamental_deterioration"]`, use lower delta
  - > 85%: High risk of dividend cut — consider WAIT or very low delta (-0.15 to -0.20)
- **Debt/Equity check**:
  - < 1.5: Normal for income stocks
  - 1.5–2.5: Elevated — monitor closely
  - > 2.5: High risk — add `risk_flags: ["weak_fundamentals"]`
- **Earnings trend**: Flat or declining revenue + high payout = danger zone → WAIT
- **Ask yourself**: If the dividend is cut 50%, would you still want to own this stock at the strike price?

### Support Level Selection
- Dividend yield itself creates support: as price drops, yield rises, attracting income buyers
- SMA 200 is strong institutional support for established income names
- Round-number strikes work well (psychological + institutional buying)
- Target strikes where dividend yield at assignment would be > 5%

### Ex-Dividend Opportunity
- After ex-div: stock drops by dividend amount → sell puts at the lower level
- This is a recurring pattern with high-yield stocks — plan for it quarterly
- Put premium + future dividend income = excellent total return if assigned

### Risk Profile
- **Primary risk**: Dividend cut → stock drops 15–30% → assignment at loss
- **Mitigation**: Strict fundamental checks, payout ratio monitoring
- **Assignment outcome**: Excellent if dividend is sustainable — immediate high-yield income
- **Total return**: Premium + high dividend yield (4%+) = attractive income
