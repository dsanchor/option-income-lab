from textwrap import dedent


def get_activity_log_interpretation() -> str:
    """Return the shared previous-activity interpretation block."""
    return dedent("""## INTERPRETING PREVIOUS ACTIVITY LOG

You will receive previous monitor activities. Use them to:
1. **Track Trend**: Is the position getting safer or riskier over time?
2. **Avoid Flip-Flopping**: If conditions haven't materially changed, maintain the same activity
3. **Detect Escalation**: Multiple consecutive WAITs with rising delta → approaching roll territory
""")
