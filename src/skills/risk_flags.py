from textwrap import dedent


def get_earnings_flag_definitions() -> str:
    """Return the shared earnings-risk flag definitions."""
    return dedent("""- `earnings_before_expiry`: Position expiration is AFTER earnings date (legacy flag, equivalent to `earnings_within_dte`)
- `earnings_within_dte`: Position expiration is after earnings — the core earnings risk for monitors
- `earnings_approaching`: Earnings 15-30 days away AND position spans earnings — time to plan a roll
- `earnings_soon`: Earnings 7-14 days away — elevated urgency if position spans earnings
- `earnings_imminent`: Earnings <7 days away — critical urgency if position spans earnings
- `unknown_earnings`: No earnings date available — apply conservative approach
""")


def get_monitor_risk_flags(option_type: str = "call") -> str:
    """Return the shared monitor risk-flag taxonomy."""
    option_type = option_type.lower()
    calendar_flags = '`ex_dividend_risk`, `catalyst_pending`' if option_type == 'call' else '`catalyst_pending`'
    technical_flags = '`breakout_momentum`, `resistance_level`' if option_type == 'call' else '`breakdown_momentum`, `support_break`'
    return dedent(f"""- `approaching_itm`, `high_delta`, `low_extrinsic` (position)
- `earnings_before_expiry`, `earnings_approaching`, `earnings_soon`, `earnings_imminent`, `earnings_within_dte`, `unknown_earnings` (earnings — all defined in the MANDATORY EARNINGS GATE)
- {calendar_flags} (calendar)
- {technical_flags} (technical)
- `fundamental_deterioration`, `analyst_downgrade` (fundamental)
- `profit_optimization` (optimization rolls)
- `ultra_defensive_roll` (roll with net debit ≤$1, acceptable insurance cost)
- `no_viable_roll` (no roll candidate meets premium-first policy thresholds)
""")
