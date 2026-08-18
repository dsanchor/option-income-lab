# Squad Team

> options-agent

## Coordinator

| Name | Role | Notes |
|------|------|-------|
| Squad | Coordinator | Routes work, enforces handoffs and reviewer gates. |

## Members

| Name | Role | Charter | Status |
|------|------|---------|--------|
| Danny | Lead | .squad/agents/danny/charter.md | 🏗️ Lead |
| Rusty | Agent Dev | .squad/agents/rusty/charter.md | 🔧 Agent Dev |
| Linus | Quant Dev | .squad/agents/linus/charter.md | 📊 Quant Dev |
| Basher | Tester | .squad/agents/basher/charter.md | 🧪 Tester |
| Livingston | Persistence & Integration Engineer | .squad/agents/livingston/charter.md | 🔧 Integration |
| Scribe | Scribe | .squad/agents/scribe/charter.md | 📋 Scribe |
| Ralph | Work Monitor | — | 🔄 Monitor |

## Project Context

- **Project:** options-agent
- **User:** dsanchor
- **Created:** 2026-03-26
- **Stack:** Python, Microsoft Agent Framework, Azure Foundry (gpt-5.1)
- **MCP Data:** mcp_massive 0.8.7 (Massive.com)
- **Description:** Two periodic trading agents — one for covered call sell signals, one for cash-secured put sell signals. Local runtime, configurable polling interval, stock symbols from files, decision logs with context carry-forward, separate sell signal log.
