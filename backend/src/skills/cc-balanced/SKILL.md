---
name: cc-balanced
description: Covered call parameters for Balanced-category stocks (default — meets basic dividend criteria).
---

## Category Profile: Balanced

Balanced stocks are the default category — they pay dividends but don't qualify for Aristocrat, Compounder, Rising Star, or High Yield. They represent a mix of characteristics without a dominant profile. Standard covered call rules apply with no special adjustments.

## Adjusted Parameters

**All existing rules remain in force** — earnings gate, DTE ≤ 45 hard cap, fundamental quality checks, and all WAIT triggers. This skill applies the standard default parameters.

### Delta Range
- **Target**: 0.20–0.30 (standard default)
- **In strong uptrend**: 0.15–0.25
- **In sideways/down**: 0.25–0.35

### Premium Thresholds
- **Minimum premium**: ≥ 0.8% of stock price for 30–45 DTE
- **Minimum annualized return**: ≥ 10%
- **WAIT threshold**: premium < 0.5% of stock price

### IV Requirements
- **IV Rank**: ≥ 35 preferred
- **WAIT if**: IV Rank < 20 AND premium below minimum

### Technical Adjustments
- Follow standard covered call technical analysis
- No category-specific overrides

### Market State Guidance
When evaluating the current market state for this Balanced stock:
- **Momentum (RSI > 70)**: Use lower delta (0.15–0.20) to protect upside
- **Neutral (RSI 40–70)**: Standard delta (0.20–0.30)
- **Weak (RSI < 35)**: Consider WAIT if fundamentals are uncertain
- **High IV**: Favorable — use standard delta range

### Ex-Dividend Consideration
- Standard ex-div rules apply (same as base instructions)

### Risk Profile
- Standard risk/reward — no special considerations
- Evaluate each setup on its own merits
