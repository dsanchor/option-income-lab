---
name: csp-balanced
description: Cash-secured put parameters for Balanced-category stocks (default — meets basic dividend criteria).
---

## Category Profile: Balanced

Balanced stocks are the default category — they pay dividends but don't qualify for Aristocrat, Compounder, Rising Star, or High Yield. Standard cash-secured put rules apply with no special adjustments.

## Adjusted Parameters

**All existing rules remain in force** — earnings gate, DTE ≤ 45 hard cap, investment worthiness check, and all WAIT triggers. This skill applies the standard default parameters.

### Delta Range
- **Target**: -0.20 to -0.30 (standard default)
- **At strong support**: -0.25 to -0.35
- **In weak market**: -0.15 to -0.25

### Premium Thresholds
- **Minimum premium**: ≥ 1.2% of strike price for 30–45 DTE
- **Minimum annualized return**: ≥ 14%
- **WAIT threshold**: premium < 0.7% of strike price

### IV Requirements
- **IV Rank**: ≥ 35 preferred
- **WAIT if**: IV Rank < 20 AND premium below minimum

### Technical Adjustments
- Follow standard cash-secured put technical analysis
- No category-specific overrides

### Market State Guidance
When evaluating the current market state for this Balanced stock:
- **Oversold (RSI < 35)**: Good opportunity if fundamentals intact
- **Neutral (RSI 35–55)**: Standard — only at clear support levels
- **Overbought (RSI > 70)**: WAIT — stock is extended
- **High IV**: Favorable — use standard delta range

### Fundamental Quality
- Standard investment worthiness check applies fully
- No category-specific streamlining

### Risk Profile
- Standard risk/reward — no special considerations
- Evaluate each setup on its own merits
