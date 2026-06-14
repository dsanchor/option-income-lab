---
name: roll-economics
description: Premium-first roll policy and verification workflow for open option roll management.
---

## Purpose

Load this skill when you need to turn a roll handoff into a concrete contract recommendation.

## Premium-First Roll Policy

Before recommending **any** roll, verify the row exists in the candidates table and use the pre-computed values directly.

### Read These Values From the Table

- **Buyback cost**: from the CURRENT POSITION block and the candidate row
- **New premium**: from the candidate row's `New Prem`
- **Net credit/debit**: from the candidate row's `Net Credit`
  - positive = credit
  - negative = debit

### Mandatory Verification

Before reporting roll economics, always:
1. confirm the chosen candidate row exists
2. match the exact strike and expiration
3. quote the exact **Buyback**, **New Prem**, and **Net Credit** values
4. state the row number in the explanation
5. reject any recommendation whose contract cannot be found exactly

Example format:
- `Row #3: Strike $195, Exp 2026-05-22, New Prem $5.25, Buyback $4.10, Net Credit +$1.15`

## Three-Tier Hierarchy

### Tier 1 — Preferred: Net Credit ≥ $1.00

- approved automatically
- ideal outcome
- proceed with the roll

### Tier 2 — Acceptable Ultra-Defensive Roll: Net Debit ≤ $1.00

- acceptable only as defensive insurance
- add `ultra_defensive_roll`
- explain clearly why paying the debit is justified

### Tier 3 — Rejected: Net Debit > $1.00

- do **not** recommend this roll
- run the roll search algorithm
- if no better candidate exists, recommend `CLOSE`

## Roll Search Algorithm

When the initial choice fails:
1. same strike, later expiration
2. safer strike, same expiration
3. safer strike and later expiration
4. if no row satisfies the constraints, recommend `CLOSE`

Scan candidates in descending `Net Credit` order and pick the first row that satisfies:
- action direction constraints
- earnings constraints
- DTE rules
- tier thresholds

Track the number of evaluated rows in `roll_economics.candidates_evaluated`.

## Earnings Constraints During Rolls

Respect any `roll_target_rules` from the handoff:
- **Preferred:** pre-earnings expiration with ≥3-day buffer
- **Acceptable:** expiration ≥14 days after earnings
- **Blocked:** expiration 0-13 days after earnings

## Profit Optimization Validation

When `profit_optimization_gate == "eligible"`, validate these candidate-specific checks before proceeding:
1. no earnings on or before the new expiration
2. no ex-dividend date on or before the new expiration

If either check fails:
- remove `profit_optimization`
- downgrade to normal roll logic
- do not proceed with the profit-optimization roll

## Close Outcomes

Recommend `CLOSE` when:
1. close-for-profit is confirmed by the current ask
2. no viable candidate survives the three-tier policy
3. fundamentals are broken and no viable roll exists

For `CLOSE` due to no viable roll:
- set `roll_economics.roll_tier = "no_viable_roll"`
- add `no_viable_roll`
- set `new_strike`, `new_expiration`, and `estimated_roll_cost` to `null`

## Cross-Verification Reminder

For every roll price you cite, explicitly show the lookup path in the chain data:
- `{option_type}["YYYYMMDD"]["strike"]["ask"] = buyback`
- `{option_type}["YYYYMMDD"]["strike"]["bid"] = new premium`

If the exact path is missing, treat the contract as unavailable.
