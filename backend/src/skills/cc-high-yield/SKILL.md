---
name: cc-high-yield
description: Covered call parameters for High Yield-category stocks (dividend yield ≥4%).
---

## Category Profile: High Yield

High Yield stocks pay generous dividends (≥4%) and are often mature, slower-growing companies (e.g., MO, T, VZ, XOM).
They have moderate-to-high IV, limited price appreciation, and investors hold them primarily for income.
Covered calls are an excellent fit — maximize total income (dividend + premium).

## Adjusted Parameters

**All existing rules remain in force** — earnings gate, DTE ≤ 45 hard cap, fundamental quality checks, and all WAIT triggers. This skill ONLY adjusts thresholds within those rules.

### Delta Range
- **Target**: 0.25–0.35 (aggressive — at or above default)
- **In sideways/down market**: 0.30–0.40 (maximize income)
- **Rationale**: High Yield stocks have limited upside. You own them for income. Higher delta = more premium = more income. Assignment at a good price is acceptable.

### Premium Thresholds
- **Minimum premium**: ≥ 0.8% of stock price for 30–45 DTE
- **Minimum annualized return**: ≥ 10% (premium only, not counting dividends)
- **WAIT threshold**: premium < 0.5% of stock price
- **Rationale**: These stocks often have decent IV. Target meaningful premium to stack with the dividend.

### IV Requirements
- **IV Rank**: ≥ 30 preferred
- **WAIT if**: IV Rank < 15 AND premium below minimum
- **Rationale**: High Yield stocks often have moderate IV. Lower bar ensures consistent coverage.

### Technical Adjustments
- **Range-bound**: IDEAL — sell aggressively at resistance. Delta 0.30–0.35.
- **Overbought (RSI > 65)**: Excellent — mean reversion is common in these stocks. Delta 0.30–0.35.
- **Downtrend**: Covered calls help offset losses. Use standard delta but check fundamentals closely (dividend cut risk?).
- **Uptrend**: Rare for High Yield but possible — use standard delta, assignment at profit is fine.

### Market State Guidance
When evaluating the current market state for this High Yield stock:
- **Momentum (RSI > 65)**: Favorable — sell at higher delta. These stocks rarely sustain strong runs.
- **Neutral (RSI 40–65)**: Standard opportunity — delta 0.25–0.35
- **Weak (RSI < 40)**: Still sell calls but check dividend safety. If fundamentals intact, delta 0.25–0.30.
- **High IV**: Excellent — these stocks respond well to IV-based call selling. Use full delta range.

### Ex-Dividend Consideration — CRITICAL
- High dividends (≥4%) create **significant early assignment risk**
- **ALWAYS check ex-dividend date** before selling calls
- If ex-div within DTE AND strike <10% OTM (delta > 0.20): HIGH assignment risk
- Preferred timing: sell calls AFTER ex-dividend date when possible
- If selling before ex-div: widen strike to >10% OTM or ensure expiration is before ex-div date
- Factor into total return: premium yield + dividend yield = total income yield

### Fundamental Quality — Extra Scrutiny
- High yield can signal distress — verify dividend is sustainable
- Check payout ratio: > 90% is a warning sign (dividend cut risk)
- Check debt levels: high debt + high yield = potential trap
- If payout ratio > 85% OR debt/equity > 2.0: add `risk_flags: ["fundamental_deterioration"]`

### Risk Profile
- **Primary risk**: Dividend cut → stock drops → assignment at loss
- **Secondary risk**: Minimal — limited upside means capping it is low cost
- **Assignment outcome**: Acceptable — you can re-buy and restart the covered call cycle
- **Total income focus**: premium + dividend = maximize yield on capital
