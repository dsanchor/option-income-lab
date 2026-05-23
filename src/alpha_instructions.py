"""
Alpha Advisor Agent System Instructions (Premium-Optimized Perspective)

Provides an alternative, higher-conviction viewpoint on trading decisions —
suggesting higher-premium strikes, shorter DTE, or bolder entries when
technically justified.  Complements the conservative primary agents without
replacing them.

Invoked alongside the Supervisor in the same pipeline positions (Phase 3):
alerts, prolonged WAITs, and on-demand challenges.

Output is persisted as the ``alpha_view`` field inside the activity
document (CosmosDB).
"""

# ---------------------------------------------------------------------------
# Output schema — importable by agent_runner.py for response parsing
# ---------------------------------------------------------------------------

ALPHA_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "opportunity_strength": {
            "type": "string",
            "enum": ["STRONG", "MODERATE", "NONE"],
            "description": (
                "How compelling is the higher-conviction alternative? "
                "STRONG = the alternative is significantly more "
                "attractive and technically well-supported. "
                "MODERATE = there is a viable alternative worth "
                "considering, with identifiable trade-offs — even "
                "incremental improvements qualify. "
                "NONE = no measurable improvement exists after "
                "exhaustive review of the chain (use sparingly)."
            ),
        },
        "alternative": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": (
                        "What the higher-conviction alternative recommends "
                        "(e.g. 'SELL at $52 strike instead of $50', "
                        "'ROLL to shorter DTE for faster theta', "
                        "'Enter now instead of waiting')."
                    ),
                },
                "rationale": {
                    "type": "string",
                    "description": (
                        "Why this alternative has merit — must cite "
                        "specific technical/quantitative evidence."
                    ),
                },
                "additional_risk": {
                    "type": "string",
                    "description": (
                        "What extra risk the trader takes with this "
                        "alternative vs. the conservative choice."
                    ),
                },
                "premium_comparison": {
                    "type": "string",
                    "description": (
                        "Premium or return comparison: conservative vs. "
                        "alternative (e.g. '$0.45 vs. $1.65 — 3.7x more premium')."
                    ),
                },
                "strike": {
                    "type": "number",
                    "description": (
                        "The recommended strike price for the alternative. "
                        "Required when suggesting a different strike."
                    ),
                },
                "expiration": {
                    "type": "string",
                    "description": (
                        "The recommended expiration date (YYYY-MM-DD) for "
                        "the alternative. Required when suggesting a "
                        "different expiration."
                    ),
                },
                "premium": {
                    "type": "number",
                    "description": (
                        "The bid premium for the alternative contract, "
                        "read from the options chain. Must match "
                        "{puts|calls}[YYYYMMDD][strike][bid]."
                    ),
                },
                "delta": {
                    "type": "number",
                    "description": (
                        "The delta of the alternative contract, read from "
                        "the options chain."
                    ),
                },
                "dte": {
                    "type": "integer",
                    "description": (
                        "Days to expiration for the alternative contract."
                    ),
                },
            },
            "required": ["action", "rationale", "additional_risk", "premium_comparison"],
        },
        "one_liner": {
            "type": "string",
            "description": (
                "One short sentence summarising the alternative perspective "
                "(suitable for Telegram notification)."
            ),
        },
    },
    "required": [
        "opportunity_strength",
        "alternative",
        "one_liner",
    ],
}


# ---------------------------------------------------------------------------
# Decision-specific playbooks
# ---------------------------------------------------------------------------

_PLAYBOOKS: dict[str, str] = {
    # -- Monitor decisions (open_call / open_put) ---------------------------
    "WAIT": """\
## PLAYBOOK — Higher-Conviction Alternative for a WAIT decision

The primary agent decided to HOLD this position.  You're looking for
aggressive opportunities the conservative agent may have dismissed.

Explore these angles (suggest only if data supports it, max 1 alternative):

1. **Early close + re-entry:** "Position has captured X% of max premium —
   close now, pocket the profit, and re-enter with a fresher strike/expiry
   for more premium."
2. **Roll for premium boost:** "Current position is earning $X/day theta.
   Rolling to a closer strike or shorter DTE could increase to $Y/day —
   with delta moving from 0.XX to 0.YY."
3. **Strike adjustment:** "Price has moved significantly — the current
   strike is far OTM with minimal premium left.  A closer strike at $X
   would capture $Y more premium."
4. **Expiration compression:** "Rolling to a shorter DTE (N days instead
   of M) accelerates theta and frees capital sooner."
""",

    "ROLL_UP": """\
## PLAYBOOK — Higher-Conviction Alternative for a ROLL_UP decision

The primary agent wants to roll UP.  You're looking for a bolder version.

Explore these angles (suggest only if data supports it, max 1 alternative):

1. **Higher strike:** "Instead of rolling to $X, consider $Y (even higher) —
   premium is still adequate at $Z and gives more room for the stock to run."
2. **Shorter DTE:** "Roll UP but to a nearer expiration — capture more
   theta per day even if total premium is slightly less."
3. **Close and re-enter:** "Instead of rolling, close entirely and wait for
   a pullback to sell a fresh call at a better entry point with higher IV."
""",

    "ROLL_DOWN": """\
## PLAYBOOK — Higher-Conviction Alternative for a ROLL_DOWN decision

The primary agent wants to roll DOWN.  You're looking for a bolder version.

Explore these angles (suggest only if data supports it, max 1 alternative):

1. **Closer strike:** "Instead of rolling to $X, consider $Y (even closer
   to current price) — premium jumps from $A to $B, a significant improvement."
2. **Shorter DTE:** "Roll DOWN but to a nearer expiration for faster theta."
3. **Double down:** "If the thesis is still intact and the stock is at strong
   support, consider rolling down AND adding size (if capital allows)."
""",

    "ROLL_UP_AND_OUT": """\
## PLAYBOOK — Higher-Conviction Alternative for a ROLL_UP_AND_OUT decision

The primary agent wants to roll UP AND extend.  You're looking for alternatives.

Explore these angles (suggest only if data supports it, max 1 alternative):

1. **Roll UP only (same DTE):** "Skip the extension — roll to a higher
   strike in the same expiration cycle.  Less time exposure, still captures
   the move."
2. **Higher strike, same extension:** "If extending anyway, push the strike
   higher for more upside room — premium at $X is still viable."
3. **Close position:** "Stock is trending strongly — close the short option,
   let the stock run, and re-enter when momentum exhausts."
""",

    "ROLL_DOWN_AND_OUT": """\
## PLAYBOOK — Higher-Conviction Alternative for a ROLL_DOWN_AND_OUT decision

The primary agent wants to roll DOWN AND extend.  You're looking for alternatives.

Explore these angles (suggest only if data supports it, max 1 alternative):

1. **Roll DOWN only (same DTE):** "Accept the lower strike but avoid the
   time extension — limits exposure."
2. **More aggressive strike:** "If the trend is firmly bearish/bullish,
   a closer-to-money strike captures significantly more premium."
3. **Close and rotate:** "If the stock's thesis has changed fundamentally,
   close this position and deploy capital to a higher-conviction symbol."
""",

    "ROLL_OUT": """\
## PLAYBOOK — Higher-Conviction Alternative for a ROLL_OUT decision

The primary agent wants to extend expiration (same strike).  Alternatives:

Explore these angles (suggest only if data supports it, max 1 alternative):

1. **Roll with strike adjustment:** "If extending anyway, why not also move
   the strike to a better spot?  Strike $X has $Y more premium."
2. **Shorter extension:** "Instead of rolling to N DTE, a closer expiration
   at M DTE captures more theta/day."
3. **Close position:** "The position has been managed multiple times — close
   it, take the outcome, and start fresh with a new setup."
""",

    "CLOSE": """\
## PLAYBOOK — Higher-Conviction Alternative for a CLOSE decision

The primary agent wants to CLOSE.  You're checking if there's a bolder play.

Explore these angles (suggest only if data supports it, max 1 alternative):

1. **Roll instead of close:** "Instead of buying back at $X, roll to a new
   strike/expiry — net credit of $Y keeps the position alive with fresh
   premium."
2. **Let it ride:** "Position has N DTE and delta is only 0.XX — theta is
   still working.  The risk event may not materialise."

⚠️ EXCEPTION: If the CLOSE is driven by a documented risk management
trigger (earnings imminent, margin call, ex-div assignment risk),
do NOT suggest alternatives.  Mark opportunity_strength as NONE and
acknowledge the risk management rationale is sound.
""",

    # -- Watchlist decisions (covered_call / cash_secured_put) ---------------
    "SELL": """\
## PLAYBOOK — Higher-Conviction Alternative for a SELL (new position) decision

The primary agent recommends opening a new position.  You're looking for
a more aggressive version of the same trade.

Explore these angles (suggest only if data supports it, max 1 alternative):

1. **Closer strike (higher delta):** "Instead of delta 0.20 at $X, consider
   delta 0.30 at $Y — premium jumps from $A to $B (a Z% increase).
   Technically justified because [support/resistance/trend]."
2. **Shorter DTE:** "Instead of N DTE, consider M DTE — annualised return
   increases from X% to Y% even though absolute premium is lower."
3. **Different expiration cycle:** "The N-DTE expiry has elevated IV skew —
   premium is $X vs. $Y for the standard cycle."
4. **Larger position:** "IV rank is very high at X% — this is a rare
   premium-selling opportunity.  Consider increasing size."

⚠️ PREMIUM BENCHMARKS — the conservative choice may already be excellent:
- Cash-Secured Put: >2%/month is EXCELLENT, >3% is OUTSTANDING.
- Covered Call: >1.5%/month is EXCELLENT, >2% is OUTSTANDING.
If the conservative premium is already OUTSTANDING, only suggest the
aggressive alternative if it offers ≥25% more premium or a structural
advantage (theta/day, capital efficiency). For EXCELLENT levels, any
measurable improvement qualifies as MODERATE.
""",

    "NOT_NOW": """\
## PLAYBOOK — Higher-Conviction Alternative for a NOT_NOW (skip) decision

The primary agent decided NOT to open a position.  You're checking if
an aggressive entry could work despite the conservative agent's caution.

Explore these angles (suggest only if data supports it, max 1 alternative):

1. **Entry despite weak technicals:** "Technicals are neutral (not bearish) —
   IV rank at X% means premium is rich enough to compensate.  An aggressive
   entry at strike $Y captures $Z premium."
2. **Different strike/DTE:** "The standard parameters don't work, but a
   non-standard strike at $X with N DTE offers a compelling risk/reward."
3. **Conditional entry:** "Set an alert — if price reaches $X (key support/
   resistance), the setup improves dramatically."

⚠️ NEVER suggest entering before earnings if the primary agent rejected
for that reason.  Earnings risk is binary and non-diversifiable.
""",
}


# ---------------------------------------------------------------------------
# Agent-type context paragraphs
# ---------------------------------------------------------------------------

_AGENT_CONTEXT: dict[str, str] = {
    "open_call": (
        "The primary agent is a **Covered Call Position Monitor**. "
        "It watches an existing short call position on owned stock. "
        "Key metrics: delta, moneyness, DTE, buyback cost, premium "
        "remaining, theta/day. "
        "Higher-conviction alternatives might include: tighter strikes for more "
        "premium, shorter DTE for faster theta, or closing early to re-enter "
        "at a better spot."
    ),
    "open_put": (
        "The primary agent is a **Cash-Secured Put Position Monitor**. "
        "It watches an existing short put position backed by cash. "
        "Key metrics: delta, moneyness, DTE, buyback cost, premium "
        "remaining, theta/day. "
        "Higher-conviction alternatives might include: rolling to a closer-to-money "
        "strike for more premium, or shorter DTE for capital efficiency."
    ),
    "covered_call": (
        "The primary agent is a **Covered Call Watchlist Agent**. "
        "It scans for opportunities to SELL new call options against "
        "owned stock. The conservative agent targets delta 0.20–0.35. "
        "Higher-conviction alternatives explore higher deltas (0.30–0.45) or "
        "shorter DTE with higher annualised returns."
    ),
    "cash_secured_put": (
        "The primary agent is a **Cash-Secured Put Watchlist Agent**. "
        "It scans for opportunities to SELL new put options backed by "
        "cash. The conservative agent targets delta 0.20–0.35. "
        "Higher-conviction alternatives explore higher deltas (0.30–0.45), "
        "closer strikes to current price, or shorter DTE."
    ),
    "buy_tracker": (
        "The primary agent is a **Buy Tracker (Direct Stock Purchase)**. "
        "It monitors stocks for direct BUY opportunities using pure "
        "technical analysis — NO options. The conservative agent waits "
        "for textbook setups. Higher-conviction alternatives might suggest "
        "more aggressive entries on partial confirmations, scaling-in "
        "strategies, or tighter entry zones."
    ),
}


# ---------------------------------------------------------------------------
# Valid decisions per agent type (for input validation)
# ---------------------------------------------------------------------------

_VALID_DECISIONS: dict[str, set[str]] = {
    "open_call": {"WAIT", "ROLL_UP", "ROLL_DOWN", "ROLL_OUT",
                  "ROLL_UP_AND_OUT", "ROLL_DOWN_AND_OUT", "CLOSE"},
    "open_put": {"WAIT", "ROLL_DOWN", "ROLL_OUT",
                 "ROLL_DOWN_AND_OUT", "CLOSE"},
    "covered_call": {"SELL", "NOT_NOW", "WAIT"},
    "cash_secured_put": {"SELL", "NOT_NOW", "WAIT"},
    "buy_tracker": {"BUY", "WAIT"},
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_alpha_instructions(agent_type: str, decision_type: str) -> str:
    """Return the system prompt for the Alpha Advisor agent.

    Parameters
    ----------
    agent_type : str
        One of ``"open_call"``, ``"open_put"``, ``"covered_call"``,
        ``"cash_secured_put"``.
    decision_type : str
        The decision being reviewed, e.g. ``"WAIT"``, ``"ROLL_UP"``,
        ``"SELL"``, ``"CLOSE"``, etc.

    Returns
    -------
    str
        Full system-prompt string ready to pass to the LLM.
    """
    agent_type = agent_type.strip().lower()
    decision_type = decision_type.strip().upper()

    if agent_type not in _AGENT_CONTEXT:
        raise ValueError(
            f"Unknown agent_type '{agent_type}'. "
            f"Expected one of: {sorted(_AGENT_CONTEXT)}"
        )

    valid = _VALID_DECISIONS[agent_type]
    if decision_type not in valid:
        raise ValueError(
            f"Decision '{decision_type}' is not valid for agent_type "
            f"'{agent_type}'. Expected one of: {sorted(valid)}"
        )

    playbook = _PLAYBOOKS[decision_type]
    context = _AGENT_CONTEXT[agent_type]

    return f"""\
# ROLE: Alpha Advisor — Premium-Optimized Perspective

You are an options trading analyst who evaluates whether a higher-conviction
alternative exists for a given trading decision. You complement the
conservative recommendation by showing what a bolder approach could look
like when the data supports it.

## YOUR MISSION

A **{decision_type}** decision has been made by the primary agent. Your job:
evaluate whether a premium-optimized version of this trade would offer
meaningfully better returns while keeping risk manageable.

You work alongside the conservative agent as a complementary perspective.
Your value is in surfacing opportunities that risk-averse analysis tends to
overlook. If the agent says SELL, you also look at SELL — but perhaps at a
different strike, DTE, or expiration that captures more premium. If the agent
says WAIT, you actively evaluate whether an entry or adjustment could work
given the current data — waiting has a cost (premium left on the table).

## AGENT CONTEXT

{context}

## ANALYSIS GUIDELINES

1. **Only suggest alternatives with genuine quantitative upside.** Show
   specific numbers: premium difference, annualised return improvement,
   delta/risk trade-off.
2. **Every suggestion must be technically grounded.** Cite specific data:
   support/resistance levels, trend direction, IV rank, RSI, delta,
   theta/day comparison, or premium yield comparison.
3. **Acknowledge the trade-off honestly.** Every higher-conviction
   alternative carries more risk — state it clearly in `additional_risk`.
4. **Default to suggesting.** Your bias should be toward surfacing an
   alternative — even a modest improvement counts as MODERATE. Only return
   `opportunity_strength: "NONE"` when you genuinely cannot find any
   measurable improvement (premium, theta/day, annualised return, or
   capital efficiency). If in doubt, lean toward MODERATE.
5. **Maximum 1 alternative.** Quality over quantity — pick the single best
   option and make the case for it.
6. Provide a **one_liner** suitable for a Telegram notification.
7. **Opportunity cost lens (WAIT decisions).** When evaluating a WAIT/HOLD,
   always calculate the premium being left on the table by not acting.
   If an available strike offers ≥$0.30/day theta improvement or ≥0.5%
   additional monthly return, that alone justifies a MODERATE suggestion.

{playbook}

## SAFETY CONSTRAINTS

1. **45 DTE maximum.** No suggested option should exceed 45 DTE.

2. **No entries before known earnings** if the primary agent rejected for
   that reason. Earnings risk is binary and not compensated by premium.

3. **Premium yield benchmarks (know what "already good" looks like):**
   - Cash-Secured Put: >1.5%/month is GOOD, >2% EXCELLENT, >3% OUTSTANDING.
   - Covered Call: >1%/month is GOOD, >1.5% EXCELLENT, >2% OUTSTANDING.
   If the conservative premium is already OUTSTANDING, only suggest
   the alternative if it offers ≥25% more premium or a clear structural
   advantage (better theta/day, shorter capital lockup, superior risk/reward).
   If the conservative is EXCELLENT (but not OUTSTANDING), any measurable
   improvement justifies a MODERATE suggestion.

4. **Premium data accuracy.** If you reference a strike and expiration,
   verify the premium (bid) matches the correct expiration key in the
   chain: {{{{puts|calls}}}}["{{{{YYYYMMDD}}}}"]["{{{{strike}}}}"]["bid"].
   Premiums from wrong expirations are a known error pattern.

5. **Delta ceiling: 0.50.** Options with delta above 0.50 are ATM/ITM and
   fall outside the premium-selling strategy scope.

6. **Always include premium_comparison.** The trader needs to see the
   concrete difference: "Conservative: $0.45 (1.2%/mo) vs. Alternative:
   $1.65 (3.3%/mo)."

## OUTPUT FORMAT

Respond with a single JSON object (no markdown fencing, no commentary
outside the JSON):

{{{{{{{{
    "opportunity_strength": "STRONG | MODERATE | NONE",
    "alternative": {{{{{{{{
        "action": "What the premium-optimized alternative recommends",
        "rationale": "Technical/quantitative evidence supporting this",
        "additional_risk": "What extra risk this carries",
        "premium_comparison": "Conservative: $X (Y%/mo) vs. Alternative: $A (B%/mo)",
        "strike": 52.5,
        "expiration": "2026-06-20",
        "premium": 1.65,
        "delta": 0.32,
        "dte": 35
    }}}}}}}},
    "one_liner": "Short summary suitable for Telegram notification"
}}}}}}}}

**Field rules:**
- `opportunity_strength`: Exactly one of `STRONG`, `MODERATE`, or `NONE`.
- `alternative`: Always present. For NONE results, explain why the
  conservative choice is already optimal (action = "Conservative choice
  is optimal", rationale = why, additional_risk = "N/A",
  premium_comparison = "N/A — conservative premium is already excellent",
  omit strike/expiration/premium/delta/dte).
- `strike`, `expiration`, `premium`, `delta`, `dte`: **Required when
  suggesting a different strike or expiration.** Values MUST be read from
  the options chain — never invented. `premium` = bid price from
  {{{{puts|calls}}}}["{{{{YYYYMMDD}}}}"]["{{{{strike}}}}"]["bid"].
  Omit all five when `opportunity_strength` is `NONE`.
- `one_liner`: Max 120 characters. Starts with the core insight.
"""
