import os
import re
import yaml
import pytz
from typing import Any, Dict

from .llm import LlmConfig


class Config:
    """Configuration loader with environment variable substitution."""

    def __init__(self, config_path: str = "config.yaml"):
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except ImportError:
            pass

        with open(config_path, 'r') as f:
            raw_config = yaml.safe_load(f)

        self.config = self._substitute_env_vars(raw_config)
        self._validate()

    def _substitute_env_vars(self, obj: Any) -> Any:
        """Recursively substitute ${VAR_NAME} with environment variables."""
        if isinstance(obj, dict):
            return {k: self._substitute_env_vars(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._substitute_env_vars(item) for item in obj]
        elif isinstance(obj, str):
            pattern = r'\$\{([^}]+)\}'
            matches = re.findall(pattern, obj)
            result = obj
            for var_name in matches:
                env_value = os.environ.get(var_name, '')
                result = result.replace(f'${{{var_name}}}', env_value)
            return result
        else:
            return obj

    def _validate(self) -> None:
        """Validate required configuration fields."""
        required_fields = [
            ('cosmosdb', 'endpoint'),
            ('cosmosdb', 'key'),
            ('scheduler', 'cron'),
        ]

        for *path, field in required_fields:
            obj = self.config
            for key in path:
                if key not in obj:
                    raise ValueError(
                        f"Missing required config: {'.'.join(path + [field])}"
                    )
                obj = obj[key]
            if field not in obj or not obj[field]:
                raise ValueError(
                    f"Missing required config: {'.'.join(path + [field])}"
                )

        if not self.model_deployment:
            raise ValueError(
                "Missing required config: ai.model_deployment "
                "(or legacy azure.model_deployment)"
            )

        provider = self._resolve_ai_provider(self.config.get('ai') or {})
        if provider not in ('azure', 'gemini'):
            raise ValueError(
                f"Invalid ai.provider: {provider!r} (use azure or gemini)"
            )
        if provider == 'azure':
            for field in ('project_endpoint', 'api_key'):
                if not self.config.get('azure', {}).get(field):
                    raise ValueError(f"Missing required config: azure.{field}")
        elif not self.config.get('gemini', {}).get('api_key'):
            raise ValueError("Missing required config: gemini.api_key")

    @staticmethod
    def _resolve_ai_provider(ai: Dict[str, Any]) -> str:
        """Resolve ai.provider from config value or AI_PROVIDER env (default: azure)."""
        raw = (ai.get('provider') or '').strip().lower()
        if raw in ('azure', 'gemini'):
            return raw
        env = (os.environ.get('AI_PROVIDER') or '').strip().lower()
        if env in ('azure', 'gemini'):
            return env
        return 'azure'

    def _ai_section(self) -> Dict[str, Any]:
        """Shared model settings; merges legacy azure.model_deployment/models if needed."""
        ai = dict(self.config.get('ai') or {})
        azure = self.config.get('azure', {})
        if not ai.get('model_deployment'):
            ai['model_deployment'] = azure.get('model_deployment', '')
        if not ai.get('models'):
            ai['models'] = azure.get('models', {}) or {}
        ai['provider'] = self._resolve_ai_provider(ai)
        return ai

    # ── AI (Azure + Gemini) ────────────────────────────────────────────

    @property
    def ai_provider(self) -> str:
        return self._ai_section()['provider']

    @property
    def azure_endpoint(self) -> str:
        return self.config.get('azure', {}).get('project_endpoint', '')

    @property
    def model_deployment(self) -> str:
        return self._ai_section().get('model_deployment', '')

    @property
    def api_key(self) -> str:
        if self.ai_provider == 'gemini':
            return self.config.get('gemini', {}).get('api_key', '')
        return self.config.get('azure', {}).get('api_key', '')

    def model_for(self, role: str) -> str:
        """Return model for a specific role, falling back to model_deployment."""
        models = self._ai_section().get('models', {})
        return models.get(role) or self.model_deployment

    def llm_config(self) -> LlmConfig:
        return LlmConfig(
            provider=self.ai_provider,
            api_key=self.api_key,
            endpoint=self.azure_endpoint if self.ai_provider == 'azure' else None,
        )

    # ── CosmosDB ───────────────────────────────────────────────────────

    @property
    def cosmosdb_endpoint(self) -> str:
        return self.config['cosmosdb']['endpoint']

    @property
    def cosmosdb_key(self) -> str:
        return self.config['cosmosdb']['key']

    @property
    def cosmosdb_database(self) -> str:
        return self.config.get('cosmosdb', {}).get(
            'database', 'stock-options-manager'
        )

    # ── Scheduler ──────────────────────────────────────────────────────

    @property
    def cron_expression(self) -> str:
        return self.config['scheduler']['cron']

    @cron_expression.setter
    def cron_expression(self, value: str):
        self.config['scheduler']['cron'] = value

    @property
    def timezone(self) -> str:
        tz_str = self.config.get('scheduler', {}).get('timezone', 'America/New_York')
        try:
            pytz.timezone(tz_str)
            return tz_str
        except pytz.exceptions.UnknownTimeZoneError:
            print(f"WARNING: Invalid timezone '{tz_str}', falling back to 'America/New_York'")
            return 'America/New_York'

    @timezone.setter
    def timezone(self, value: str):
        try:
            pytz.timezone(value)
            self.config['scheduler']['timezone'] = value
        except pytz.exceptions.UnknownTimeZoneError:
            raise ValueError(f"Invalid timezone: {value}")

    # ── Context ────────────────────────────────────────────────────────

    @property
    def max_activity_entries(self) -> int:
        """Recent activities for context injection (0=none, max 5). Default 2."""
        val = self.config.get('context', {}).get('max_activity_entries', 2)
        return max(0, min(5, val))

    @property
    def activity_ttl_days(self) -> int:
        return self.config.get('context', {}).get('activity_ttl_days', 90)

    # ── Telegram ──────────────────────────────────────────────────────

    @property
    def telegram_enabled(self) -> bool:
        return self.config.get('telegram', {}).get('enabled', False)

    @property
    def telegram_bot_token(self) -> str:
        return self.config.get('telegram', {}).get('bot_token', '')

    @property
    def telegram_chat_id(self) -> str:
        return self.config.get('telegram', {}).get('chat_id', '')

    # ── yfinance ─────────────────────────────────────────────────────

    @property
    def yfinance_config(self) -> dict:
        """Return the full yfinance config section for the provider factory."""
        yf = self.config.get('yfinance', {})
        return {
            "cache_ttl": int(yf.get('cache_ttl', 300)),
        }

    @property
    def yfinance_cache_ttl(self) -> int:
        """Cache TTL in seconds for yfinance provider (default: 300)."""
        return int(self.config.get('yfinance', {}).get('cache_ttl', 300))

    @property
    def yfinance_randomize_symbols(self) -> bool:
        """Shuffle symbol order to vary processing (default: True)."""
        return bool(self.config.get('yfinance', {}).get('randomize_symbols', True))

    # ── Summary Agent ──────────────────────────────────────────────────

    @property
    def summary_agent_enabled(self) -> bool:
        """Whether summary agent is enabled (default: True)."""
        return bool(self.config.get('summary_agent', {}).get('enabled', True))

    @property
    def summary_agent_cron(self) -> str:
        """Summary agent cron expression (default: '0 8 * * *')."""
        return str(self.config.get('summary_agent', {}).get('cron', '0 8 * * *'))

    @property
    def summary_agent_activity_count(self) -> int:
        """Number of recent activities per symbol to analyze (default: 3)."""
        return int(self.config.get('summary_agent', {}).get('activity_count', 3))

    # ── Options Chain Scheduler ────────────────────────────────────────

    @property
    def options_chain_scheduler_enabled(self) -> bool:
        """Whether options chain scheduler is enabled (default: True)."""
        return bool(self.config.get('options_chain_scheduler', {}).get('enabled', True))

    @property
    def options_chain_scheduler_cron(self) -> str:
        """Options chain scheduler cron expression (default: '0 * * * *')."""
        return str(self.config.get('options_chain_scheduler', {}).get('cron', '0 * * * *'))
