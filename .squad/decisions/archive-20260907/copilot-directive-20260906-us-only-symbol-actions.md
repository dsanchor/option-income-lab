### 2026-09-06: Restrict Symbol Details option actions to US listings
**By:** Copilot (via Copilot)
**What:** In Symbol Details, show Analyze, covered-call, cash-secured-put, Buy Tracker, Alerts, tracking-disable controls, and the entire Options section only for securities whose exchange MIC is XNYS or XNAS. Non-US listings keep Summary, Stocks, Portfolio, and other non-options information. Enforce the same eligibility in action endpoints so the rule is not UI-only.
**Why:** User wants option agents and related tracking available only for NYSE and Nasdaq securities for now.
