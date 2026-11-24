"""Upcoming dividend-date policy for new covered calls and cash-secured puts."""

UNAVAILABLE_EX_DIVIDEND_NOTE = (
    "Upcoming ex-dividend date unavailable; this is informational only, "
    "not evidence that no future dividend exists."
)

SELL_EX_DIVIDEND_POLICY = f"""
## UPCOMING EX-DIVIDEND DATE — NEW SELL AGENTS ONLY

For covered_call and cash_secured_put analysis (including exact-contract
validation and review of SELL, WAIT or NOT_NOW), an unknown upcoming
ex-dividend date is INFORMATIONAL ONLY. Missing, null, unavailable or
past-only dates do not establish a future date. Check the supplied calendar
and dividends data, but NEVER fabricate a date, infer one from dividend
history, or claim that missing data proves no future dividend or no risk.

If no upcoming date is known, include this brief note in the primary
`reason` / ex-dividend check detail, or the review's existing narrative fields:
"{UNAVAILABLE_EX_DIVIDEND_NOTE}"
In primary `rule_checks`, use `informational` for `cc_ex_div_check` or
`csp_ex_div_check`, not a fabricated `pass`.

This absence alone must NOT cause WAIT / NOT_NOW, block SELL, lower confidence,
increase risk_rating (including its calendar component), set `ex_dividend_risk`
or `incomplete_data`, or raise Supervisor challenge_strength. It is NOT a hard
gate and needs NO Alpha parameter relaxation; do not invent a relaxable
parameter for it. Review an unjustified missing-date WAIT under this policy,
while still requiring all other applicable gates and reviews to pass.

This policy takes precedence over category-skill wording such as "ALWAYS check
ex-dividend date": checking available evidence is required, obtaining a known
future date is not. When an upcoming date IS known, preserve all existing
conflict, strike-proximity, dividend-amount and assignment rules. Earnings,
DTE, liquidity, Greeks, fundamental and catalyst gates remain unchanged.
Do NOT force SELL when another gate fails. This policy does not apply to
open-position assessment or roll/re-sell policy.
"""


def get_sell_dividend_policy(agent_type: str) -> str:
    return (
        SELL_EX_DIVIDEND_POLICY
        if agent_type in {"covered_call", "cash_secured_put"}
        else ""
    )
