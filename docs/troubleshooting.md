# Troubleshooting

[← Back to README](../README.md)

### "Missing required config: azure.project_endpoint" (using Gemini)
Set `AI_PROVIDER=gemini` in `.env` (or `ai.provider: gemini` in `config.yaml`). Azure credentials are not required when using Gemini. Ensure `GOOGLE_API_KEY` is set.

### "Environment variable AZURE_AI_PROJECT_ENDPOINT not set"
You are using the Azure provider (`AI_PROVIDER` unset or `azure`). Export `AZURE_AI_PROJECT_ENDPOINT` and `AZURE_OPENAI_API_KEY`, or switch to Gemini with `AI_PROVIDER=gemini` and `GOOGLE_API_KEY`.

### "gemini API key not configured"
Set `GOOGLE_API_KEY` in `.env` when `AI_PROVIDER=gemini`.

### CosmosDB Connection Errors
- Verify `COSMOSDB_ENDPOINT` and `COSMOSDB_KEY` are set correctly
- Ensure the CosmosDB account, database (`stock-options-manager`), and containers (`symbols`, `telemetry`) exist
- Run `bash scripts/provision_cosmosdb.sh` to create missing resources

### Data Fetching Issues
- If market data fetching fails, check network connectivity and Yahoo Finance availability
- yfinance requires no authentication — if you get 429 errors, the built-in rate limiter should handle it

### LLM / Authentication Errors
- **Azure:** Ensure `AZURE_OPENAI_API_KEY` and `AZURE_AI_PROJECT_ENDPOINT` are set. Get the API key from the Azure Portal under your Azure OpenAI resource.
- **Gemini:** Ensure `GOOGLE_API_KEY` is set and `MODEL_DEPLOYMENT` uses a valid Gemini model ID (e.g. `gemini-2.0-flash`). Gemini uses Google's OpenAI-compatible API endpoint.

### Module Import Errors
Make sure you installed the correct SDK packages: `pip install agent-framework-core agent-framework-openai` (NOT `azure-ai-agents`)