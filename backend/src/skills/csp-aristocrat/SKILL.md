---
name: csp-aristocrat
description: Cash-secured put parameters for Aristocrat-category stocks (≥25 years dividend growth, yield ≥2%).
---

## Category Profile: Aristocrat

Aristocrats are ultra-stable, long-track-record dividend growers (e.g., JNJ, KO, PG, MMM).
Getting assigned means owning a world-class dividend stock at a discount — an excellent outcome.
The quality floor is very high, so you can be more aggressive on entry.

## Adjusted Parameters

**All existing rules remain in force** — earnings gate, DTE ≤ 45 hard cap, investment worthiness check, and all WAIT triggers. This skill ONLY adjusts thresholds within those rules.

### Delta Range
- **Target**: -0.25 to -0.35 (standard to slightly aggressive)
- **At strong support**: -0.30 to -0.40 (higher assignment probability is acceptable)
- **Rationale**: Assignment = buying a top-tier dividend stock at a discount. You WANT to own this stock. Higher delta is acceptable.

### Premium Thresholds
- **Minimum premium**: ≥ 0.8% of strike price for 30–45 DTE (reduced from default 1.5%)
- **Minimum annualized return**: ≥ 10% (reduced from default 18%)
- **WAIT threshold**: premium < 0.5% of strike price (reduced from default 0.9%)
- **Rationale**: Aristocrats have low IV → low premiums. But the quality of the stock makes even modest premium worthwhile. Total return = premium + future dividend stream.

### IV Requirements
- **IV Rank**: No minimum requirement
- **IV Percentile**: No minimum requirement
- **WAIT only if**: premium below the reduced WAIT threshold AND no technical oversold signal
- **Rationale**: Low IV is structural for Aristocrats. Requiring IV Rank ≥ 50 would eliminate nearly all opportunities.

### Technical Adjustments — Oversold is Opportunity
- **RSI < 40**: Good opportunity (relaxed from default RSI < 30)
- **RSI < 30**: Excellent opportunity — increase delta toward -0.35
- **At or below SMA 200**: Strong signal IF fundamentals intact. Aristocrats tend to recover to trend.
- **Breaking support with high volume**: Still a WAIT — even Aristocrats can have structural issues

### Market State Guidance
When evaluating the current market state for this Aristocrat:
- **Oversold (RSI < 35)**: IDEAL — Aristocrats mean-revert reliably. Use full delta range.
- **Neutral (RSI 35–60)**: Standard opportunity if at/near support levels
- **Overbought (RSI > 70)**: Less favorable — stock is extended. Use lower delta or WAIT for pullback.
- **High IV event**: Rare but excellent — use aggressive delta and capture elevated premium

### Fundamental Quality — Streamlined
- Aristocrats have ≥25 years of dividend growth — fundamental quality is pre-validated
- Still check for: recent earnings misses, unusual debt increases, payout ratio spikes
- But do NOT require full fundamental re-analysis each time — the track record speaks
- If payout ratio suddenly > 80%: flag as `risk_flags: ["fundamental_deterioration"]`

### Support Level Selection
- Aristocrats have reliable institutional support at major levels
- SMA 200 is often strong support — good strike target
- Round numbers (psychological support) work well for these stable stocks
- After ex-dividend drops: stock typically recovers → sell puts on the dip

### Ex-Dividend Consideration
- Puts on dividend stocks: ex-div price drop creates PUT SELLING opportunities
- After ex-div date: stock drops by dividend amount → sell puts at the lower price
- Early assignment risk on puts is minimal unless deep ITM

### Risk Profile
- **Primary risk**: Sector rotation or rare fundamental shift
- **Assignment outcome**: Excellent — you own a 25+ year dividend grower at a discount
- **Total return mindset**: premium collected + dividend yield once assigned
