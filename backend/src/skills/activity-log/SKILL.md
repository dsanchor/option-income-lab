---
name: activity-log
description: How to interpret previous monitor activity history without flip-flopping decisions.
---

## Purpose

Load this skill when previous monitor activities are included in the prompt.

## How to Use Previous Activities

1. **Track trend** — determine whether the position is getting safer or riskier over time.
2. **Avoid flip-flopping** — if conditions have not materially changed, keep the same activity.
3. **Detect escalation** — repeated WAIT decisions with worsening delta or price drift can signal an approaching roll.
4. **Apply the anti-flip-flop rule for near-ATM positions**:
   - if the previous activity was `WAIT`
   - and delta changed by **< 0.10**
   - and price changed by **< 1%**
   - and the broader condition set has not materially worsened
   - then keep `WAIT`

A single noisier monitoring snapshot is **not** enough to reverse a prior WAIT. Look for consistent deterioration across multiple readings before escalating.
