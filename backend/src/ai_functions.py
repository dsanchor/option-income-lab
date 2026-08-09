"""Canonical catalog of independently configurable AI functions."""

AI_FUNCTIONS = {
    "monitor_assessment": {
        "label": "Position Assessment",
        "group": "Monitoring",
        "description": "Phase 1 risk assessment for open call and put positions.",
        "legacy_task": "scheduler",
    },
    "monitor_roll": {
        "label": "Roll Management",
        "group": "Monitoring",
        "description": "Phase 2 roll candidate selection for open positions.",
        "legacy_task": "scheduler",
    },
    "analysis": {
        "label": "Following Analysis",
        "group": "Monitoring",
        "description": "Covered Call and Cash-Secured Put watchlist analysis.",
        "legacy_task": "scheduler",
    },
    "buy_tracker": {
        "label": "Buy Tracker",
        "group": "Monitoring",
        "description": "Stock accumulation timing analysis.",
        "legacy_task": "scheduler",
    },
    "supervisor": {
        "label": "Supervisor Review",
        "group": "Monitoring",
        "description": "Post-decision quality and risk audit.",
        "legacy_task": "scheduler",
    },
    "alpha": {
        "label": "Alpha Review",
        "group": "Monitoring",
        "description": "Aggressive alternative perspective on agent decisions.",
        "legacy_task": "scheduler",
    },
    "summary": {
        "label": "Portfolio Summary",
        "group": "Reporting",
        "description": "Scheduled portfolio summary generation.",
        "legacy_task": "summary_agent",
    },
    "banner": {
        "label": "Dashboard Banner",
        "group": "Reporting",
        "description": "Dashboard news and portfolio insight banner.",
        "legacy_task": "banner_agent",
    },
    "report": {
        "label": "Symbol Report",
        "group": "Reporting",
        "description": "Comprehensive symbol situation report.",
    },
    "technical_analysis": {
        "label": "Technical Analysis",
        "group": "Reporting",
        "description": "On-demand technical analysis report.",
    },
    "plan_monitor": {
        "label": "Plan Monitor",
        "group": "Reporting",
        "description": "Evaluation and annotation of active action plans.",
        "legacy_task": "plan_monitor",
        "legacy_model_section": "plan_monitor",
        "default_model": "gpt-5.4-mini",
    },
    "chat": {
        "label": "Portfolio Chat",
        "group": "Chat",
        "description": "Global portfolio chat responses.",
    },
    "symbol_chat": {
        "label": "Symbol Chat",
        "group": "Chat",
        "description": "Symbol-specific chat responses.",
    },
    "activity_chat": {
        "label": "Activity Chat",
        "group": "Chat",
        "description": "Conversation about a specific activity.",
        "legacy_model_section": "activity_chat",
        "default_model": "gpt-5.4-mini",
    },
    "dps_insights": {
        "label": "DPS Insights",
        "group": "Chat",
        "description": "Natural-language interpretation of DPS analysis.",
        "legacy_model_section": "dps_insights",
        "default_model": "gpt-5.4-mini",
    },
}

SUPPORTED_AI_PROVIDERS = ("azure", "gemini")
