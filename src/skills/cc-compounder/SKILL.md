---
name: cc-compounder
description: Covered call parameters for Compounder-category stocks (CAGR ≥10%, yield <3%, payout ≤50%).
---

## Category Profile: Compounder

Compounders are quality growth stocks with moderate dividends that reinvest heavily (e.g., MSFT, AAPL, V, UNH).
They have moderate volatility, strong earnings growth, and meaningful price appreciation potential.
Covered calls must balance income against capping a stock that appreciates reliably.

## Adjusted Parameters

**All existing rules remain in force** — earnings gate, DTE ≤ 45 hard cap, fundamental quality checks, and all WAIT triggers. This skill ONLY adjusts thresholds within those rules.

### Delta Range
- **Target**: 0.15–0.25 (conservative — lower than default)
- **In strong uptrend**: 0.10–0.20 (protect upside)
- **Rationale**: Compounders have meaningful appreciation potential. Lower delta protects upside participation while still generating premium.

### Premium Thresholds
- **Minimum premium**: ≥ 0.6% of stock price for 30–45 DTE (reduced from default 1.0%)
- **Minimum annualized return**: ≥ 8% (reduced from default 12%)
- **WAIT threshold**: premium < 0.4% of stock price
- **Rationale**: These stocks grow — the real return includes price appreciation. Accept lower premium to protect upside.

### IV Requirements
- **IV Rank**: ≥ 30 preferred (lower bar than default 50)
- **WAIT if**: IV Rank < 20 AND premium below minimum
- **Rationale**: Compounders have moderate IV. Rigid IV requirements miss good setups.

### Technical Adjustments
- **Strong uptrend with rising momentum**: Use minimum delta (0.10–0.15) or WAIT — protect growth
- **Range-bound / consolidation**: Standard delta (0.20–0.25) — ideal for covered calls
- **Overbought (RSI > 70)**: Favorable — these stocks often consolidate after runs. Standard delta.
- **Pullback in uptrend (price < 20MA but > 200MA)**: WAIT — let the pullback resolve, don't cap recovery

### Market State Guidance
When evaluating the current market state for this Compounder:
- **Momentum (RSI > 65, near highs)**: Use very low delta (0.10–0.15) to avoid capping strong moves. Consider WAIT if momentum is accelerating.
- **Neutral (RSI 40–65)**: Standard opportunity — delta 0.20–0.25
- **Weak (RSI < 40)**: Calls generate less premium AND you risk capping a recovery. WAIT or use very short DTE.
- **High IV (earnings proximity, sector rotation)**: Excellent — use standard delta, capture elevated premium

### Ex-Dividend Consideration
- Low dividends (yield <3%) — ex-div impact is smaller
- Still check ex-div date but early assignment risk is lower due to small dividend

### Risk Profile
- **Primary risk**: Capping upside on a stock that appreciates 10–15%/year
- **Mitigation**: Low delta + strikes well above resistance
- **Assignment outcome**: Selling a quality compounder at profit is acceptable but not ideal — plan to re-enter
