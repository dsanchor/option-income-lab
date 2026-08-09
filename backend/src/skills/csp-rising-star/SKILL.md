---
name: csp-rising-star
description: Cash-secured put parameters for Rising Star-category stocks (CAGR ≥15%, ≤10 years growth, yield <2%).
---

## Category Profile: Rising Star

Rising Stars are young, high-growth dividend initiators with higher volatility.
They offer attractive put premiums due to higher IV, but assignment carries more risk — these stocks can have larger drawdowns.
Use CSPs selectively to enter positions at deep discounts.

## Adjusted Parameters

**All existing rules remain in force** — earnings gate, DTE ≤ 45 hard cap, investment worthiness check, and all WAIT triggers. This skill ONLY adjusts thresholds within those rules.

### Delta Range
- **Target**: -0.15 to -0.25 (conservative — below default)
- **After significant pullback to strong support**: -0.20 to -0.30
- **Rationale**: Rising Stars can drop sharply. Lower delta = deeper OTM = more room for error. Only increase delta at very strong support after oversold conditions.

### Premium Thresholds
- **Minimum premium**: ≥ 1.2% of strike price for 30–45 DTE
- **Minimum annualized return**: ≥ 15%
- **WAIT threshold**: premium < 0.8% of strike price
- **Rationale**: Higher IV in Rising Stars naturally generates more premium. Require meaningful premium to justify the volatility risk.

### IV Requirements
- **IV Rank**: ≥ 40 preferred
- **WAIT if**: IV Rank < 25 — premium won't compensate for the volatility risk
- **Rationale**: These stocks have naturally elevated IV. A moderate bar ensures you're selling at favorable volatility.

### Technical Adjustments — Patience Required
- **RSI < 35 at major support**: Best setup — oversold + support confluence
- **RSI 35–45 with stabilization**: Acceptable if support is clear
- **RSI > 50**: WAIT — not enough of a pullback to justify the risk
- **Breaking below SMA 200**: WAIT unless fundamentals are exceptionally strong — could be a trend change
- **Falling knife (no stabilization)**: Strict WAIT — do NOT try to catch the bottom

### Market State Guidance
When evaluating the current market state for this Rising Star:
- **Oversold at support (RSI < 30)**: Best opportunity — use full delta range
- **Weakening (RSI 35–45)**: Cautious — only at clear support with stabilization signals
- **Neutral (RSI 45–60)**: Generally WAIT — not enough discount
- **Momentum (RSI > 65)**: WAIT — no reason to sell puts when stock is running
- **High IV spike**: Good premium but verify the cause — if it's bad news, WAIT

### Fundamental Quality — Extra Scrutiny
- Rising Stars are younger companies — track record is shorter
- Verify: revenue growth sustained, path to profitability clear, competitive moat forming
- Check recent quarters: any deceleration in growth? Market may punish severely.
- Be stricter on investment worthiness — would you hold this stock through a 30% drawdown?

### Support Level Selection
- Use wider support zones — Rising Stars have more volatile price action
- SMA 200 is the primary support level
- Previous major lows from large pullbacks
- Target strikes well below current price — 10-15% OTM minimum

### Risk Profile
- **Primary risk**: Growth deceleration → large drawdown → assignment at loss
- **Secondary risk**: Higher volatility means faster moves through strikes
- **Assignment outcome**: Acceptable only if conviction in the growth thesis is high
- **Recovery expectation**: Variable — some Rising Stars recover quickly, others don't
- **Max allocation**: Consider position sizing — don't over-concentrate in volatile names
