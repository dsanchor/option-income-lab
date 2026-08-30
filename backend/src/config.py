import os
import re
import time
from datetime import datetime
import yaml
from typing import Any, Dict

from .ai_functions import AI_FUNCTIONS, SUPPORTED_AI_PROVIDERS
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
        if provider not in SUPPORTED_AI_PROVIDERS:
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
        if raw in SUPPORTED_AI_PROVIDERS:
            return raw
        env = (os.environ.get('AI_PROVIDER') or '').strip().lower()
        if env in SUPPORTED_AI_PROVIDERS:
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

    def model_for(
        self,
        role: str,
        config_key: str | None = None,
        default: str | None = None,
    ) -> str:
        """Resolve function override with backward-compatible legacy fallbacks."""
        override = self._function_override(role).get('model')
        if override:
            return str(override).strip()
        metadata = AI_FUNCTIONS.get(role, {})
        legacy_task = config_key or metadata.get('legacy_task')
        if legacy_task and role != 'plan_monitor':
            legacy_model = str(
                self.config.get(legacy_task, {}).get('model') or ''
            ).strip()
            if legacy_model:
                return legacy_model
        legacy_section = metadata.get('legacy_model_section')
        if legacy_section:
            legacy_model = str(
                self.config.get(legacy_section, {}).get('model') or ''
            ).strip()
            if legacy_model:
                return legacy_model
        models = self._ai_section().get('models', {})
        return (
            models.get(role)
            or default
            or metadata.get('default_model')
            or self.model_deployment
        )

    def llm_config(self) -> LlmConfig:
        return self._llm_config_for_provider(self.ai_provider)

    def llm_config_for(self, config_key: str | None = None) -> LlmConfig:
        """Backward-compatible task provider resolver."""
        provider = self.ai_provider
        if config_key:
            raw = str(
                self.config.get(config_key, {}).get('provider') or ''
            ).strip().lower()
            if raw:
                if raw not in SUPPORTED_AI_PROVIDERS:
                    raise ValueError(
                        f"Invalid {config_key}.provider: {raw!r} "
                        "(use azure or gemini)"
                    )
                provider = raw
        return self._llm_config_for_provider(provider)

    def provider_for(self, role: str) -> str:
        """Resolve function override → legacy task override → global provider."""
        override = str(
            self._function_override(role).get('provider') or ''
        ).strip().lower()
        if override:
            if override not in SUPPORTED_AI_PROVIDERS:
                raise ValueError(
                    f"Invalid ai_function_overrides.{role}.provider: "
                    f"{override!r}"
                )
            return override
        legacy_task = AI_FUNCTIONS.get(role, {}).get('legacy_task')
        legacy_provider = str(
            self.config.get(legacy_task, {}).get('provider') or ''
        ).strip().lower() if legacy_task else ''
        if legacy_provider in SUPPORTED_AI_PROVIDERS:
            return legacy_provider
        return self.ai_provider

    def llm_config_for_function(self, role: str) -> LlmConfig:
        return self._llm_config_for_provider(self.provider_for(role))

    def function_llm_configs(self) -> Dict[str, LlmConfig]:
        return {
            role: self.llm_config_for_function(role)
            for role in AI_FUNCTIONS
        }

    def function_model_deployments(self) -> Dict[str, str]:
        """Return per-function model deployments for all AI functions."""
        return {
            role: self.model_for(role)
            for role in AI_FUNCTIONS
        }

    def _function_override(self, role: str) -> Dict[str, Any]:
        return dict(
            self.config.get('ai_function_overrides', {}).get(role) or {}
        )

    def _llm_config_for_provider(self, provider: str) -> LlmConfig:
        return LlmConfig(
            provider=provider,
            api_key=(
                self.config.get('gemini', {}).get('api_key', '')
                if provider == 'gemini'
                else self.config.get('azure', {}).get('api_key', '')
            ),
            endpoint=self.azure_endpoint if provider == 'azure' else None,
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
        tzinfo = datetime.now().astimezone().tzinfo
        if tzinfo is None:
            return time.tzname[0] if time.tzname else 'local'
        return (
            getattr(tzinfo, 'key', None)
            or getattr(tzinfo, 'zone', None)
            or tzinfo.tzname(None)
            or str(tzinfo)
        )

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

    # ── Plan Monitor ────────────────────────────────────────────────────

    @property
    def plan_monitor_enabled(self) -> bool:
        """Whether the plan monitor is enabled (default: True)."""
        return bool(self.config.get('plan_monitor', {}).get('enabled', True))

    @property
    def plan_monitor_cron(self) -> str:
        """Plan monitor cron expression (default: '0 4,16 * * 1-5')."""
        return str(self.config.get('plan_monitor', {}).get('cron', '0 4,16 * * 1-5'))

    @property
    def plan_monitor_model(self) -> str:
        """Plan monitor model (default: 'gpt-5.4-mini')."""
        return self.model_for('plan_monitor')

    @property
    def activity_chat_model(self) -> str:
        """Activity chat model (default: 'gpt-5.4-mini')."""
        return self.model_for('activity_chat')

    @property
    def dps_insights_model(self) -> str:
        """DPS insights model (default: 'gpt-5.4-mini')."""
        return self.model_for('dps_insights')

    # ── Options Chain Scheduler ────────────────────────────────────────

    @property
    def options_chain_scheduler_enabled(self) -> bool:
        """Whether options chain scheduler is enabled (default: True)."""
        return bool(self.config.get('options_chain_scheduler', {}).get('enabled', True))

    @property
    def options_chain_scheduler_cron(self) -> str:
        """Options chain scheduler cron expression (default: '0 * * * *')."""
        return str(self.config.get('options_chain_scheduler', {}).get('cron', '0 * * * *'))

    # ── Price Forecast ─────────────────────────────────────────────────

    @property
    def price_forecast_enabled(self) -> bool:
        """Whether the deterministic price-forecast job is enabled (default: True)."""
        return bool(self.config.get('price_forecast', {}).get('enabled', True))

    @property
    def price_forecast_cron(self) -> str:
        """Price-forecast cron expression (default: '0 21 * * 1-5')."""
        return str(self.config.get('price_forecast', {}).get('cron', '0 21 * * 1-5'))

    @property
    def price_forecast_band_confidence(self) -> float:
        """Primary band central confidence (0.50/0.68/0.80/0.95). Default 0.50."""
        try:
            v = float(self.config.get('price_forecast', {}).get('band_confidence', 0.50))
        except (TypeError, ValueError):
            return 0.50
        return v if v in (0.50, 0.68, 0.80, 0.90, 0.95) else 0.50

    @property
    def price_forecast_vol_source(self) -> str:
        """Volatility source: 'hv' | 'ewma' | 'iv_hv'. Default 'iv_hv'."""
        v = str(self.config.get('price_forecast', {}).get('vol_source', 'iv_hv')).lower()
        return v if v in ('hv', 'ewma', 'iv_hv') else 'iv_hv'

    @property
    def price_forecast_trend_window(self) -> int:
        """Sessions used for the short-horizon (1d/1w) trend regression. Default 20."""
        try:
            v = int(self.config.get('price_forecast', {}).get('trend_window', 20))
        except (TypeError, ValueError):
            return 20
        return v if 5 <= v <= 120 else 20

    @property
    def price_forecast_trend_window_long(self) -> int:
        """Sessions used for the long-horizon (2w/4w) trend regression. Default 40."""
        try:
            v = int(self.config.get('price_forecast', {}).get('trend_window_long', 40))
        except (TypeError, ValueError):
            return 40
        return v if 5 <= v <= 120 else 40
