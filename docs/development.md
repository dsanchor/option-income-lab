# Development

[← Back to README](../README.md)

### Agent Skills Architecture

Agent instructions use the **native agent-framework `SkillsProvider`** for shared knowledge blocks. Instead of duplicating common sections (earnings gates, roll economics, data format guides) across every instruction file, they are extracted into reusable `SKILL.md` files under `src/skills/`.

**How it works — Progressive Disclosure:**

1. **Advertise** — Skill names and descriptions are injected into the agent's system prompt (~100 tokens per skill)
2. **Load on demand** — The agent calls `load_skill` tool to retrieve full content only when needed
3. **Read resources** — Supplementary files available via `read_skill_resource` tool

```python
# In agent_runner.py
from agent_framework import Agent, SkillsProvider

skills_provider = SkillsProvider.from_paths(skill_paths="src/skills/earnings-gate-monitor")
agent = Agent(
    client=client,
    instructions="...",  # Only role-specific instructions
    context_providers=[skills_provider],  # Skills loaded on demand
)
```

**Available skills:**

| Skill | Description | Used by |
|-------|-------------|---------|
| `earnings-gate-monitor` | Earnings decision matrix for open positions | Assessment agents |
| `earnings-gate-sell` | Earnings decision matrix for new positions | Covered call / CSP watchers |
| `roll-economics` | Premium-First Roll Policy (3-tier hierarchy) | Roll management agents |
| `data-source` | Yahoo Finance data format guide | All agents |
| `risk-flags` | Risk flag taxonomy | Assessment + Roll agents |
| `activity-log` | Previous activity log interpretation | Assessment agents |
| `cc-aristocrat` | Covered call params for Aristocrat stocks | Covered call watcher |
| `cc-compounder` | Covered call params for Compounder stocks | Covered call watcher |
| `cc-rising-star` | Covered call params for Rising Star stocks | Covered call watcher |
| `cc-high-yield` | Covered call params for High Yield stocks | Covered call watcher |
| `cc-balanced` | Covered call params for Balanced stocks | Covered call watcher |
| `csp-aristocrat` | CSP params for Aristocrat stocks | CSP watcher |
| `csp-compounder` | CSP params for Compounder stocks | CSP watcher |
| `csp-rising-star` | CSP params for Rising Star stocks | CSP watcher |
| `csp-high-yield` | CSP params for High Yield stocks | CSP watcher |
| `csp-balanced` | CSP params for Balanced stocks | CSP watcher |

**Benefits:**
- **Reduced token cost** — Skills only loaded when the agent needs them (progressive disclosure)
- **No duplication** — Shared knowledge lives in one place
- **Cleaner instruction files** — Only role-specific logic, ~20% shorter
- **Standard format** — SKILL.md with YAML frontmatter follows the `agentskills.io` specification

### Instruction Files

Each agent has its own instruction file returning a system prompt string:
- `covered_call_instructions.py` — Covered call watcher
- `cash_secured_put_instructions.py` — Cash secured put watcher
- `open_call_assessment_instructions.py` — Open call Phase 1 (assessment)
- `open_call_roll_instructions.py` — Open call Phase 2 (roll management)
- `open_put_assessment_instructions.py` — Open put Phase 1 (assessment)
- `open_put_roll_instructions.py` — Open put Phase 2 (roll management)
- `buy_tracker_instructions.py` — Buy tracker (informational, no supervisor/alpha review)
- `plan_monitor_instructions.py` — Plan monitor (evaluates action plans against market data)
- `supervisor_instructions.py` — Quality auditor (9 playbooks × 4 agent contexts)
- `alpha_instructions.py` — Alpha Advisor (aggressive perspective)

All instructions assume pre-fetched market data — the LLM receives data as text and performs analysis only (no tools, no HTTP access).

### SDK Information

This project uses the **Microsoft Agent Framework** (`agent-framework` package from https://github.com/microsoft/agent-framework).

Key components:
- `agent_framework.Agent` — Agent runner class with `context_providers` support for native Skills
- `agent_framework.SkillsProvider` — Discovers SKILL.md files and provides progressive disclosure via tools
- `agent_framework.openai.OpenAIChatCompletionClient` — Chat client for Azure OpenAI and OpenAI-compatible APIs
- `src/llm.py` — Provider factory (`azure` / `gemini`) shared by agents and web chat endpoints

Market data is fetched via `yfinance` Python library — overview, technicals, forecast, dividends, and options chains are all retrieved through Yahoo Finance's API. All fetching is driven from Python (`yfinance_data_provider.py`), not by the LLM. The LLM receives pre-fetched data as text and performs analysis only — no tools are given to the agent (except the skill-loading tools injected by SkillsProvider).

---