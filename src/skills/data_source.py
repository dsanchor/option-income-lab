from textwrap import dedent


def get_data_source_skill(
    option_type: str = "call",
    include_dividends: bool = False,
    is_monitor: bool = True,
    extra_sections: str = "",
) -> str:
    """Return the shared data-source and parsing guidance."""
    option_type = option_type.lower()
    chain_side = "PUT side" if option_type == "put" else "chain"
    overview_use = (
        "current price vs strike comparison, fundamental quality check"
        if option_type == "put"
        else "current price vs strike comparison, dividend/ex-div risk, earnings proximity"
    )
    technical_focus = (
        "Is price accelerating downward toward your strike? Or holding above?"
        if option_type == "put"
        else "Is price accelerating toward your strike? Or consolidating safely below?"
    )
    forecast_use = (
        "earnings date proximity, analyst sentiment (downgrades could push price down), fundamental quality"
        if option_type == "put"
        else "earnings date proximity, analyst sentiment (upgrades could push price up)"
    )

    sections = [
        "## DATA SOURCE",
        "",
        "All market data has been **pre-fetched from Yahoo Finance** and is included directly in your message. You do NOT have any data fetching tools. Do NOT attempt to call any tools — simply analyze the data provided.",
        "",
        "**Data characteristics:**",
        '- Values may show "—" during non-market hours — note this and proceed with available data',
        "- Pre-calculated technicals — RSI, MACD, Stochastic, CCI, ADX, all MAs (10-200) with Buy/Sell/Neutral signals are computed via pandas-ta",
        "- Pivot points — Classic, Fibonacci, Camarilla, Woodie, DM with R1-R3, S1-S3",
        "",
        "### Data Review",
        "",
    ]

    if is_monitor:
        sections.append("Market data has been pre-fetched and included in your message. You will find four sections:")
    else:
        sections.append("Market data has been pre-fetched and included in your message. You will find:")

    sections.extend(
        [
            "",
            "1. **OVERVIEW PAGE** — Current price, market cap, P/E ratio, dividend yield, 52-week high/low, volume, sector, industry, earnings date.",
            "   *(JSON format with self-descriptive keys — fundamentals, exchange, ticker, etc.)*",
            f"   - Use for: {overview_use}",
            "",
            "2. **TECHNICALS PAGE** — Oscillator summaries, moving average data, and pivot points.",
            "   *(JSON format — summary, oscillators, moving_averages with individual indicator values)*",
            "   - Use for: momentum assessment (is price trending toward strike?), support/resistance levels",
            f"   - Key focus: {technical_focus}",
            "",
            "3. **FORECAST PAGE** — Price targets, analyst ratings, EPS history, revenue data.",
            "   *(JSON format — price_target, analyst_rating with individual analyst counts)*",
            f"   - Use for: {forecast_use}",
        ]
    )

    if include_dividends:
        sections.extend(
            [
                "",
                "4. **DIVIDENDS PAGE** — Dividend yield, payout history, and ex-dividend details.",
                "   *(JSON format with next dividend dates and historical payout fields)*",
                "   - Use for: dividend safety, ex-dividend timing, and assignment considerations",
            ]
        )

    if is_monitor:
        section_number = 5 if include_dividends else 4
        sections.extend(
            [
                "",
                f"{section_number}. **OPTIONS CHAIN** — Structured JSON containing call and put contracts grouped by expiration date.",
                "   The data is provided in the OPTIONS CHAIN FORMAT documented above the JSON payload.",
                "   Each contract has named fields: strike, bid, ask, mid, iv, delta, gamma, theta, vega, rho, etc.",
                "   - Use for: current Greeks of your position, roll candidates, IV assessment",
                f"   - **Critical**: Find your strike in the {chain_side} to get current delta, gamma, IV",
                "   - **For ROLL economics**: buyback_cost = 'ask' of your CURRENT option, new_premium = 'bid' of the roll TARGET option",
                "   - **Fallback** (if options chain shows [ERROR: ...] or is empty):",
                "     - Use pivot points for strike targets, note data was unavailable",
            ]
        )
    else:
        sections.extend(
            [
                "",
                "**Note:** You do NOT receive the full options chain. Your position's current delta and IV are provided in the position context data. You do not need the chain for your assessment.",
            ]
        )

    if extra_sections:
        sections.extend(["", extra_sections.strip("\n")])

    sections.extend(
        [
            "",
            "Parse these sections to extract the data you need for analysis. If any section shows [ERROR: ...], note it and work with available data.",
        ]
    )

    return dedent("\n".join(sections))
