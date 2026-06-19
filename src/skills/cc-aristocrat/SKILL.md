---
name: cc-aristocrat
description: Covered call parameters for Aristocrat-category stocks (≥25 years dividend growth, yield ≥2%).
---

## Category Profile: Aristocrat

Aristocrats are ultra-stable, long-track-record dividend growers (e.g., JNJ, KO, PG, MMM).
They have low volatility, predictable earnings, and modest price appreciation.
Premium income supplements an already reliable dividend stream.

## Adjusted Parameters

**All existing rules remain in force** — earnings gate, DTE ≤ 45 hard cap, fundamental quality checks, and all WAIT triggers. This skill ONLY adjusts thresholds within those rules.

### Delta Range
- **Target**: 0.20–0.30 (standard)
- **Rationale**: Aristocrats move slowly — assignment risk is lower at a given delta. Standard delta range is safe.

### Premium Thresholds
- **Minimum premium**: ≥ 0.5% of stock price for 30–45 DTE (reduced from default 1.0%)
- **Minimum annualized return**: ≥ 8% (reduced from default 12%)
- **WAIT threshold**: premium < 0.3% of stock price
- **Rationale**: Aristocrats have structurally low IV. Requiring 1%+ premium means you almost never sell calls on them. The dividend yield (≥2%) compensates — total return = premium + dividend.

### IV Requirements
- **IV Rank**: No minimum requirement (Aristocrats rarely have elevated IV)
- **IV Percentile**: No minimum requirement
- **Rationale**: Low IV is the norm for these stocks, not a warning sign. Premium is lower but so is assignment risk.

### Technical Adjustments
- Steady uptrend (price > 20MA > 50MA) is **normal** for Aristocrats — do NOT treat as a WAIT signal
- Use higher strikes (above R1/R2) in uptrends to preserve upside
- Only WAIT on explosive breakout (gap + 2x volume) — same as base rules

### Market State Guidance
When evaluating the current market state for this Aristocrat, consider:
- **Overbought (RSI > 70)**: Ideal for selling calls — these stocks mean-revert reliably
- **Neutral/Trending (RSI 40–70)**: Standard opportunity — sell calls at resistance
- **Oversold (RSI < 35)**: Less favorable for calls — the stock may bounce. Consider wider strikes or WAIT
- **High IV event**: Rare but excellent — use standard delta range and capture the elevated premium

### Ex-Dividend Consideration
- Aristocrats pay regular dividends — ALWAYS check ex-dividend date
- If ex-div within DTE and strike <10% OTM → prefer expiration after ex-div or widen strike
- Factor dividend yield into total return calculation: total = premium yield + dividend yield

### Risk Profile
- Assignment at strike is low-risk — you're selling a quality stock you can re-buy
- Missed upside is the main risk, but Aristocrats have modest appreciation anyway
- Total return mindset: premium + dividend > price appreciation concern
