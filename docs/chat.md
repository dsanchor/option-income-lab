# Chat

[← Back to README](../README.md)

The Chat menu opens `/chat` directly. The former API Chat is now **Chat**;
Options Chat and Quick Analysis are no longer global choices. A local greeting
does not invoke the model. **New conversation** clears the ephemeral history.
The technical `api` mode and `api_chat` model settings remain compatible.

The application imposes no token, message-length, conversation-history, request-body,
or response-length ceilings on these chats, including the legacy conversational
API modes. Forecast chat includes all sanitized forecast rows in the selected
range without row-count or context-byte truncation. Model/provider context and
output limits and server/platform transport constraints still apply; genuine
provider errors remain errors. Schema validation, data access boundaries, and
the selected forecast ranges are unchanged. The initial forecast report retains
a compact writing preference, not an enforced output cap.

## Shared read-only MCP access

Chat, activity chats, Symbol chats, and forecast follow-ups share the existing
`OIL_MCP_URL` business connection and optional `FINANCIAL_NEWS_MCP_URL` connection.
URLs come only from server environment configuration. Only existing allowlisted
business tools and `get_financial_news` are exposed, not administrative tools.
Each turn owns and closes its connections.

Main Chat requires the business MCP. Contextual chats can still answer from their
supplied context when it is not configured, with an explicit unavailable-tool
limitation. A configured business connection failure returns an error; optional
news discovery failure leaves business access available and discloses that news
cannot be retrieved.

Activity chats retain the exact historical agent decision and separately labeled
current market data. Symbol chats retain their Symbol scope and US-options
eligibility checks. Forecast initial reports remain bounded to their validated
historical snapshot and never connect to MCP. Forecast follow-ups may retrieve
additional same-Symbol evidence, clearly separating source/time and live data from
the selected historical snapshot. All chats retain independent model settings.

Conversation, business context, and tool responses are data, not instructions.
External news receives only public tickers/topics, never conversation history,
activities, positions, holdings, accounts, balances, or identity. News is optional,
limited to recent articles, and not proof that no event occurred.

Import chat and non-conversational report/explanation jobs are unchanged.
