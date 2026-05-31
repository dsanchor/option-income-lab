"""Telegram Bot API integration for alert notifications.

The notifier reads from CosmosDB first (if available), falling back to config.yaml.
Changes made via the Settings UI take effect immediately — no scheduler restart needed.
"""
import logging
import os
import re
from pathlib import Path
from typing import Dict, Optional, Tuple

import requests
import yaml

logger = logging.getLogger(__name__)

AGENT_LABELS = {
    "covered_call": "Covered Call",
    "cash_secured_put": "Cash-Secured Put",
    "buy_tracker": "Buy Tracker",
    "open_call_monitor": "Open Call Monitor",
    "open_put_monitor": "Open Put Monitor",
}

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _resolve_env(s: str) -> str:
    """Resolve ${VAR_NAME} patterns in a string."""
    def _repl(m):
        return os.environ.get(m.group(1), "")
    return re.sub(r'\$\{([^}]+)\}', _repl, s)


def _read_telegram_config() -> Tuple[bool, str, str]:
    """Read current telegram settings from config.yaml.

    Returns (enabled, bot_token, chat_id) with env vars resolved.
    """
    try:
        config_path = _PROJECT_ROOT / "config.yaml"
        with open(config_path, "r") as f:
            cfg = yaml.safe_load(f) or {}
        tg = cfg.get("telegram", {})
        enabled = bool(tg.get("enabled", False))
        bot_token = _resolve_env(str(tg.get("bot_token", "")))
        chat_id = _resolve_env(str(tg.get("chat_id", "")))
        return enabled, bot_token, chat_id
    except Exception:
        logger.debug("Could not read telegram config", exc_info=True)
        return False, "", ""


class TelegramNotifier:
    """Sends alert notifications via Telegram Bot API.

    Reads settings from CosmosDB first (if available), falling back to config.yaml.
    Settings UI changes take effect without restarting the scheduler.
    """

    def __init__(self, cosmos=None):
        """Initialize the Telegram notifier.
        
        Args:
            cosmos: Optional CosmosDBService instance. If provided, will read
                    settings from CosmosDB first, falling back to config.yaml.
        """
        self._cosmos = cosmos

    def _get_credentials(self) -> Optional[Tuple[str, str]]:
        """Return (bot_token, chat_id) if enabled, else None.
        
        Tries CosmosDB first, falls back to config.yaml.
        """
        # Try CosmosDB first
        if self._cosmos:
            try:
                settings = self._cosmos.get_settings()
                tg = settings.get('telegram', {})
                if tg.get('enabled') and tg.get('bot_token') and tg.get('chat_id'):
                    return tg['bot_token'], tg['chat_id']
            except Exception:
                logger.debug("Could not read telegram config from CosmosDB, "
                             "falling back to config.yaml", exc_info=True)
        
        # Fall back to config.yaml
        enabled, bot_token, chat_id = _read_telegram_config()
        if enabled and bot_token and chat_id:
            return bot_token, chat_id
        return None

    def _is_symbol_notifications_enabled(self, symbol: str) -> bool:
        """Check if Telegram notifications are enabled for a specific symbol.
        
        Returns True if enabled or if the setting doesn't exist (default).
        """
        if not self._cosmos:
            return True
        
        try:
            symbol_doc = self._cosmos.get_symbol(symbol)
            if symbol_doc is None:
                return True
            return symbol_doc.get("telegram_notifications_enabled", True)
        except Exception:
            logger.debug("Could not check notification setting for %s, defaulting to enabled", 
                        symbol, exc_info=True)
            return True

    def send_alert(
        self,
        symbol: str,
        agent_type: str,
        alert_data: Dict,
        is_roll: bool = False,
    ) -> bool:
        """Send a formatted alert notification.

        Returns True if sent successfully, False otherwise.
        """
        if not self._is_symbol_notifications_enabled(symbol):
            logger.debug("Telegram notifications disabled for symbol %s", symbol)
            return False

        creds = self._get_credentials()
        if creds is None:
            return False

        try:
            label = AGENT_LABELS.get(agent_type, agent_type)
            if is_roll:
                text = self._format_roll_alert(symbol, label, alert_data)
            else:
                text = self._format_watchlist_alert(symbol, label, alert_data)
            return self._send(creds[0], creds[1], text)
        except Exception:
            logger.warning("Failed to build Telegram alert for %s", symbol, exc_info=True)
            return False

    # ── message formatting ────────────────────────────────────────────

    # Emoji mapping for buy tracker activities (colored alerts)
    _BUY_TRACKER_EMOJIS = {
        "STRONG_BUY": "\U0001f7e2",  # 🟢 green circle (strong)
        "BUY": "\U0001f7e9",          # 🟩 green square (lighter feel)
        "WAIT": "\U0001f7e0",         # 🟠 orange
    }

    @staticmethod
    def _format_watchlist_alert(symbol: str, agent_label: str, data: Dict) -> str:
        activity = str(data.get("activity", "ALERT")).upper()
        strike = data.get("strike")
        expiration = data.get("expiration")
        confidence = data.get("confidence", "N/A")
        risk_rating = data.get("risk_rating")
        premium = data.get("premium")
        underlying_price = data.get("underlying_price")
        entry_zone = data.get("entry_zone")

        # Buy tracker uses colored emojis per activity level
        if agent_label == "Buy Tracker":
            emoji = TelegramNotifier._BUY_TRACKER_EMOJIS.get(activity, "\U0001f6a8")
        else:
            emoji = "\U0001f6a8"  # 🚨 default for SELL alerts

        lines = [
            f"{emoji} <b>{activity} Alert: {symbol}</b>",
            f"Agent: {agent_label}",
        ]
        if underlying_price is not None:
            lines.append(f"Underlying: ${underlying_price}")
        # Buy tracker: show score instead of strike/expiration
        score = data.get("score")
        if score is not None:
            lines.append(f"Score: {score}/5")
        if strike is not None:
            lines.append(f"Strike: ${strike}")
        if expiration:
            lines.append(f"Expiration: {expiration}")
        if entry_zone:
            lines.append(f"Entry Zone: {entry_zone}")
        lines.append(f"Confidence: {confidence}")
        if premium is not None:
            lines.append(f"Premium: ${premium}")
        if risk_rating is not None:
            lines.append(f"Risk: {risk_rating}/10")

        # Inline supervisor (MODERATE/STRONG only)
        sv = data.get("supervisor_view")
        if sv and sv.get("challenge_strength") in ("MODERATE", "STRONG"):
            lines.append("")
            lines.append(f"\U0001f6e1 Supervisor [{sv['challenge_strength']}]: {sv.get('one_liner', '')}")

        # Inline alpha advisor (MODERATE/STRONG only)
        av = data.get("alpha_view")
        if av and av.get("opportunity_strength") in ("MODERATE", "STRONG"):
            lines.append("")
            lines.append(f"\U0001f50d Alpha [{av['opportunity_strength']}]: {av.get('one_liner', '')}")

        return "\n".join(lines)

    @staticmethod
    def _format_roll_alert(symbol: str, agent_label: str, data: Dict) -> str:
        action = data.get("action", "N/A")
        current_strike = data.get("current_strike", data.get("strike", "N/A"))
        current_exp = data.get("current_expiration", data.get("expiration", "N/A"))
        new_strike = data.get("new_strike", "N/A")
        new_exp = data.get("new_expiration", "N/A")
        confidence = data.get("confidence", "N/A")

        underlying_price = data.get("underlying_price")
        assignment_risk = data.get("assignment_risk")

        # Roll economics / close cost fields
        buyback_cost = data.get("buyback_cost")
        new_premium = data.get("new_premium")
        net_credit_debit = data.get("net_credit_debit")

        is_close = str(action).upper() == "CLOSE"

        if is_close:
            lines = [
                f"\U0001f6d1 <b>CLOSE Alert: {symbol}</b>",
                f"Agent: {agent_label}",
            ]
            if underlying_price is not None:
                lines.append(f"Underlying: ${underlying_price}")
            lines.append(f"Position: ${current_strike} exp {current_exp}")
            if buyback_cost is not None:
                lines.append(f"Buyback Cost: ${buyback_cost}")
            lines.append(f"Confidence: {confidence}")
        else:
            lines = [
                f"\U0001f504 <b>ROLL Alert: {symbol}</b>",
                f"Agent: {agent_label}",
            ]
            if underlying_price is not None:
                lines.append(f"Underlying: ${underlying_price}")
            lines.extend([
                f"Action: {action}",
                f"Current: ${current_strike} exp {current_exp}",
                f"New: ${new_strike} exp {new_exp}",
            ])
            if buyback_cost is not None:
                lines.append(f"Buyback Cost: ${buyback_cost}")
            if new_premium is not None:
                lines.append(f"New Premium: ${new_premium}")
            if net_credit_debit is not None:
                lines.append(f"Net Credit/Debit: ${net_credit_debit}")
            lines.append(f"Confidence: {confidence}")

        if assignment_risk is not None:
            lines.append(f"Assignment Risk: {str(assignment_risk).capitalize()}")

        # Inline supervisor (MODERATE/STRONG only)
        sv = data.get("supervisor_view")
        if sv and sv.get("challenge_strength") in ("MODERATE", "STRONG"):
            lines.append("")
            lines.append(f"\U0001f6e1 Supervisor [{sv['challenge_strength']}]: {sv.get('one_liner', '')}")

        # Inline alpha advisor (MODERATE/STRONG only)
        av = data.get("alpha_view")
        if av and av.get("opportunity_strength") in ("MODERATE", "STRONG"):
            lines.append("")
            lines.append(f"\U0001f50d Alpha [{av['opportunity_strength']}]: {av.get('one_liner', '')}")

        return "\n".join(lines)

    # ── prolonged WAIT alert ──────────────────────────────────────────

    def send_prolonged_wait_alert(
        self,
        symbol: str,
        agent_type: str,
        supervisor_view: Dict | None = None,
        alpha_view: Dict | None = None,
        consecutive_waits: int = 5,
        underlying_price: float | None = None,
    ) -> bool:
        """Send a Telegram notification for prolonged WAIT patterns.

        Returns True if sent successfully, False otherwise.
        """
        if not self._is_symbol_notifications_enabled(symbol):
            logger.debug("Telegram notifications disabled for symbol %s", symbol)
            return False

        creds = self._get_credentials()
        if creds is None:
            return False

        try:
            label = AGENT_LABELS.get(agent_type, agent_type)

            lines = [
                f"\u23f3 <b>PROLONGED WAIT: {symbol}</b> ({label})",
            ]
            if underlying_price is not None:
                lines.append(f"Underlying: ${underlying_price}")
            lines.append(f"{consecutive_waits} consecutive WAIT decisions")

            if supervisor_view is not None:
                strength = supervisor_view.get("challenge_strength", "N/A")
                one_liner = supervisor_view.get("one_liner", "")
                net_assessment = supervisor_view.get("net_assessment", "")
                counter_args = supervisor_view.get("counter_arguments", [])

                lines.append("")
                lines.append(f"\U0001f6e1 Supervisor [{strength}]: {one_liner}")

                if counter_args:
                    lines.append("")
                    lines.append("Audit findings:")
                    for i, arg in enumerate(counter_args, 1):
                        point = arg.get("point", str(arg)) if isinstance(arg, dict) else str(arg)
                        lines.append(f"  {i}. {point}")

                if net_assessment:
                    lines.append(f"Verdict: {net_assessment}")

            if alpha_view is not None:
                opp_strength = alpha_view.get("opportunity_strength", "NONE")
                alpha_liner = alpha_view.get("one_liner", "")
                alt = alpha_view.get("alternative", {})

                if opp_strength in ("MODERATE", "STRONG"):
                    lines.append("")
                    lines.append(f"\U0001f50d Alpha [{opp_strength}]: {alpha_liner}")
                    if alt.get("action"):
                        lines.append(f"  → {alt['action']}")
                    if alt.get("premium_comparison"):
                        lines.append(f"  💰 {alt['premium_comparison']}")

            return self._send(creds[0], creds[1], "\n".join(lines))
        except Exception:
            logger.warning("Failed to build prolonged WAIT alert for %s", symbol, exc_info=True)
            return False

    # ── low-level send ────────────────────────────────────────────────

    def send_message(self, text: str, parse_mode: str = "HTML") -> bool:
        """Send a raw message using current config.yaml credentials.

        Returns True if sent successfully, False otherwise.
        """
        creds = self._get_credentials()
        if creds is None:
            return False
        return self._send(creds[0], creds[1], text, parse_mode)

    @staticmethod
    def _send(bot_token: str, chat_id: str, text: str,
              parse_mode: str = "HTML") -> bool:
        """Low-level POST to the Telegram Bot API."""
        try:
            url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            resp = requests.post(
                url,
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": parse_mode,
                },
                timeout=10,
            )
            if resp.ok:
                logger.info("Telegram message sent (chat_id=%s)", chat_id)
                return True
            logger.warning(
                "Telegram API error %s: %s", resp.status_code, resp.text,
            )
            return False
        except Exception:
            logger.warning("Telegram send failed", exc_info=True)
            return False
