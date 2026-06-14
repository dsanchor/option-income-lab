from textwrap import dedent


def get_roll_economics_skill(option_type: str = "call") -> str:
    """Return the shared premium-first roll-policy block for monitor prompts."""
    option_type = option_type.lower()
    option_label = "put" if option_type == "put" else "call"
    option_key = "puts" if option_type == "put" else "calls"
    verification_new = (
        '2. Find your ROLL TARGET contract: puts["<new_expiration>"]["<new_strike>"]["bid"]. This is your new_premium.'
        if option_type == "put"
        else '2. Find your ROLL TARGET contract: calls["<new_expiration>"]["<new_strike>"]["bid"]. This is your new_premium.'
    )
    search_step_2 = (
        '2. **-1 strike increment lower, same expiration**: Move the strike down by $1-$2.50 (puts roll down for safety), keep expiration'
        if option_type == "put"
        else '2. **+1 strike increment higher, same expiration**: Move the strike up by $1-$2.50 (calls roll up for safety), keep expiration'
    )
    search_step_3 = (
        '3. **-1 strike lower AND +1 week further**: Combine both — lower strike and more time'
        if option_type == "put"
        else '3. **+1 strike higher AND +1 week further**: Combine both — higher strike and more time'
    )
    return dedent(f"""### Premium-First Roll Policy (MANDATORY)

**Before recommending ANY roll**, you MUST calculate roll economics using the options chain data (Section 4). This policy enforces a strict hierarchy that prioritizes income generation and caps defensive roll costs.

**Roll Economics Calculation:**
- **Buyback cost**: ASK price of the current option (what you pay to close)
- **New premium**: BID price of the roll target option (what you collect on the new option)
- **Net credit/debit**: New premium minus buyback cost
  - Positive = net credit (you collect money)
  - Negative = net debit (you pay money)

**VERIFICATION (CRITICAL — do NOT skip):**
Before reporting roll economics, you MUST:
1. Find your CURRENT contract: {option_key}["<expiration>"]["<strike>"]["ask"]. This is your buyback_cost.
{verification_new}
3. State the full path and value: e.g., {option_key}["20260427"]["475.0"]["ask"] = 3.00
4. If EITHER key path does not exist in the data, set roll_economics to null and explain the contract was not available.
5. Quote the exact values — do NOT round, estimate, or approximate.

**Three-Tier Hierarchy:**

**Tier 1 — PREFERRED: Net Credit ≥ $1.00**
- Roll generates income of at least $1.00 per share ($100 per contract)
- Approved automatically — this is the ideal outcome
- Proceed with the roll recommendation

**Tier 2 — ACCEPTABLE (Ultra-Defensive): Net Debit ≤ $1.00**
- Roll costs money, but paying ≤$1.00 per share ($100 per contract) is acceptable insurance to avoid assignment on a position you want to keep
- This is a defensive maneuver when the stock has moved significantly against you
- MUST add `"ultra_defensive_roll"` to `risk_flags`
- Include detailed justification in the `reason` field explaining why paying this debit is warranted

**Tier 3 — REJECTED: Net Debit > $1.00**
- Do NOT recommend this roll
- The cost is too high — position has deteriorated beyond reasonable roll economics
- Execute the Roll Search Algorithm (below) to find alternatives
- If no viable alternative exists → recommend CLOSE

**Roll Search Algorithm:**

When your initial roll candidate fails the net credit test (Tier 1) or exceeds the $1 debit threshold (Tier 2), systematically search for better alternatives in this order:

1. **Same new strike, +1 week further expiration**: Keep the strike, try the next weekly expiration (more time = more premium)
{search_step_2}
{search_step_3}
4. **If all candidates fail → CLOSE**: No viable roll exists that meets the net credit or ultra-defensive thresholds

Track how many candidates you evaluated in `roll_economics.candidates_evaluated`.

**ALWAYS show the math in the `reason` field:**
- "Buyback cost: $X.XX (ask at current $XX strike, MMM DD exp)"
- "New premium: $Y.YY (bid at new $YY strike, MMM DD exp)"
- "Net credit/debit: +$Z.ZZ" or "Net debit: -$Z.ZZ"
- "Roll tier: Tier 1 (net credit)" or "Tier 2 (ultra-defensive, debit within $1 threshold)" or "Tier 3 (rejected, no viable roll found)"

**CLOSE Activity Updated Logic:**

Recommend CLOSE only when:
1. **Fundamental thesis has changed** (existing rule — you no longer want to hold the underlying), OR
2. **No viable roll exists**: After executing the Roll Search Algorithm, no candidate meets the ≥$1.00 net credit threshold AND no ultra-defensive roll (≤$1.00 debit) is acceptable

When recommending CLOSE due to #2, set `roll_economics.roll_tier = "no_viable_roll"` and add `"no_viable_roll"` to `risk_flags`.
""")
