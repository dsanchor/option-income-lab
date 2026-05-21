import asyncio
import json
import logging
import os
import re
import time
import traceback
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from agent_framework import Agent
from agent_framework.openai import OpenAIChatCompletionClient

from .cosmos_db import CosmosDBService
from .llm import LlmConfig, create_async_chat_client
from .context import ContextProvider
from .options_chain_filters import (
    filter_options_chain_by_type,
    filter_options_chain_for_position,
    filter_options_chain_by_delta,
    filter_options_chain_by_roll_direction,
    format_roll_candidates_table,
)
from .yfinance_data_provider import YFinanceDataProvider, create_provider, get_shared_provider, OPTIONS_CHAIN_SCHEMA_DESCRIPTION
from .supervisor_instructions import get_supervisor_instructions, SUPERVISOR_OUTPUT_SCHEMA
from .alpha_instructions import get_alpha_instructions, ALPHA_OUTPUT_SCHEMA

# Canonical timestamp format — used for ALL activity and alert log entries.
TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"

# Valid action values for Phase 1 → Phase 2 handoff and Phase 2 output.
# Bare "ROLL" is NEVER valid — a direction is always required.
VALID_ROLL_ACTIONS = {
    "ROLL_DOWN", "ROLL_UP", "ROLL_OUT",
    "ROLL_UP_AND_OUT", "ROLL_DOWN_AND_OUT",
}
VALID_PHASE2_ACTIVITIES = VALID_ROLL_ACTIONS | {"WAIT", "CLOSE"}

# ---------------------------------------------------------------------------
# Debug logging setup – console only
# ---------------------------------------------------------------------------
os.makedirs("logs", exist_ok=True)

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

_fmt = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s – %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

_console_handler = logging.StreamHandler()
_console_handler.setLevel(logging.INFO)
_console_handler.setFormatter(_fmt)

logger.addHandler(_console_handler)


class AgentRunner:
    """Manages agent execution using Microsoft Agent Framework with yfinance pre-fetch."""

    PROLONGED_WAIT_THRESHOLD = 5
    SUPERVISOR_COOLDOWN = 3  # WAITs between repeated supervisor/alpha reviews
    
    def __init__(self, llm: LlmConfig, model: str, telegram_notifier=None,
                 project_endpoint: str = None, api_key: str = None):
        """Initialize the agent runner.

        Args:
            llm: Provider credentials (azure or gemini)
            model: Default model name / deployment
            telegram_notifier: Optional TelegramNotifier for alert notifications
            project_endpoint: Deprecated — use llm= (Azure backward compat)
            api_key: Deprecated — use llm=
        """
        if project_endpoint is not None and api_key is not None:
            llm = LlmConfig(provider='azure', api_key=api_key, endpoint=project_endpoint)
        self._llm = llm
        self._default_model = model
        self._clients: Dict[str, object] = {}
        self.telegram_notifier = telegram_notifier

    def _get_client(self, model: str = None) -> OpenAIChatCompletionClient:
        """Return a cached chat client for the given model name."""
        deployment = model or self._default_model
        if deployment not in self._clients:
            logger.info(
                "Creating OpenAIChatCompletionClient provider=%s model=%s",
                self._llm.provider, deployment,
            )
            self._clients[deployment] = create_async_chat_client(
                deployment, self._llm,
            )
        return self._clients[deployment]

    @property
    def client(self):
        """Backward-compatible accessor — returns the default model client."""
        return self._get_client()
    
    # ── Options chain formatting ────────────────────────────────────────

    @staticmethod
    def _format_options_chain(raw_chain: str, symbol: str, current_strike: float = None, option_type: str = None) -> str:
        """Format options chain data for agent consumption.

        The yfinance provider returns already-structured JSON. Parse it
        and apply filters.
        """
        try:
            structured = json.loads(raw_chain) if isinstance(raw_chain, str) else raw_chain
        except (json.JSONDecodeError, TypeError):
            structured = {"calls": {}, "puts": {}}

        if structured.get("calls") or structured.get("puts"):
            if option_type:
                structured = filter_options_chain_by_type(structured, option_type)
            if current_strike is not None:
                structured = filter_options_chain_for_position(structured, current_strike, option_type)
            structured = filter_options_chain_by_delta(structured)

            # Sanity check: warn if contracts have null bid values
            null_bid_count = 0
            total_count = 0
            for side in ("calls", "puts"):
                for _exp, strikes in structured.get(side, {}).items():
                    for _sk, contract in strikes.items():
                        total_count += 1
                        if contract.get("bid") is None:
                            null_bid_count += 1
            if null_bid_count > 0:
                logger.warning(
                    "Options chain for %s: %d/%d contracts have NULL bid — "
                    "agents will be unable to calculate premium.",
                    symbol, null_bid_count, total_count,
                )

            return (
                OPTIONS_CHAIN_SCHEMA_DESCRIPTION + "\n"
                + json.dumps(structured, indent=2)
            )
        logger.warning(
            "Options chain for %s: empty calls/puts — "
            "falling back to raw text (%d chars).",
            symbol, len(raw_chain) if raw_chain else 0,
        )
        return raw_chain

    @staticmethod
    def _format_current_contract_chain(
        raw_chain: str,
        symbol: str,
        current_strike: float,
        expiration: str,
        option_type: str,
    ) -> str:
        """Extract only the current contract from the options chain.

        Returns the chain schema description plus a minimal JSON containing
        just the single strike/expiration being monitored.  This gives
        Phase 1 (assessment) the delta, IV, gamma, theta etc. for the
        current position without the full chain noise.
        """
        try:
            structured = json.loads(raw_chain) if isinstance(raw_chain, str) else raw_chain
        except (json.JSONDecodeError, TypeError):
            structured = {"calls": {}, "puts": {}}

        bucket_key = "calls" if option_type == "call" else "puts"
        bucket = structured.get(bucket_key, {})

        # Normalise expiration to YYYYMMDD (the chain key format)
        exp_key = expiration.replace("-", "")
        strike_key = str(current_strike)
        # Try common float representations (72 → "72.0", 72.5 → "72.5")
        contract = None
        for sk in (strike_key, f"{current_strike:.1f}", f"{current_strike:.2f}"):
            contract = bucket.get(exp_key, {}).get(sk)
            if contract is not None:
                strike_key = sk
                break

        if contract is None:
            return f"(current contract {option_type} ${current_strike} exp {expiration} not found in chain)"

        minimal_chain = {
            "symbol": structured.get("symbol", symbol),
            "timestamp": structured.get("timestamp", ""),
            "current_position": {
                "strike": current_strike,
                "expiration": expiration,
                "type": option_type,
            },
            bucket_key: {
                exp_key: {
                    strike_key: contract,
                },
            },
        }
        return (
            OPTIONS_CHAIN_SCHEMA_DESCRIPTION + "\n"
            + json.dumps(minimal_chain, indent=2)
        )

    # ── JSON / SUMMARY extraction ──────────────────────────────────────

    @staticmethod
    def _try_extract_json(response_text: str) -> Optional[Dict]:
        """Try to parse a JSON activity block from the agent response.

        Looks for fenced ```json blocks first, then falls back to finding a
        raw JSON object that contains an ``"activity"`` key.
        """
        # 1. Fenced code block: ```json ... ```
        fenced = re.findall(r'```json\s*\n(.*?)```', response_text, re.DOTALL)
        for block in fenced:
            block = block.strip()
            try:
                data = json.loads(block)
                if isinstance(data, dict) and "activity" in data:
                    return data
            except json.JSONDecodeError:
                continue

        # 2. Raw JSON object containing "activity"
        for match in re.finditer(r'\{[^{}]*"activity"\s*:', response_text):
            start = match.start()
            depth = 0
            for i in range(start, len(response_text)):
                if response_text[i] == '{':
                    depth += 1
                elif response_text[i] == '}':
                    depth -= 1
                    if depth == 0:
                        candidate = response_text[start:i + 1]
                        try:
                            data = json.loads(candidate)
                            if isinstance(data, dict) and "activity" in data:
                                return data
                        except json.JSONDecodeError:
                            break

        return None

    @staticmethod
    def _extract_summary_line(response_text: str) -> Optional[str]:
        """Extract the SUMMARY: line from the agent response."""
        for line in response_text.split('\n'):
            stripped = line.strip()
            if stripped.startswith("SUMMARY:"):
                return stripped
        return None

    def _extract_activity_line(self, symbol: str, response_text: str) -> Tuple[str, Optional[Dict]]:
        """Extract a concise activity line and optional JSON from the response.

        Returns:
            (summary_line, json_data) — json_data is None when the agent used
            the legacy pipe-delimited format.
        """
        ticker = symbol.split('-', 1)[1] if '-' in symbol else symbol

        # Try structured JSON format first
        json_data = self._try_extract_json(response_text)
        if json_data is not None:
            summary = self._extract_summary_line(response_text)
            if summary:
                return summary, json_data
            # Build a SUMMARY from the JSON fields
            activity = json_data.get("activity", "WAIT")
            agent_type = json_data.get("agent", "covered_call").replace("_", " ")
            if activity == "SELL":
                strike = json_data.get("strike", "?")
                exp = json_data.get("expiration", "?")
                iv = json_data.get("iv", "?")
                iv_rank = json_data.get("iv_rank", "?")
                premium = json_data.get("premium", "?")
                premium_pct = json_data.get("premium_pct", "?")
                summary = (
                    f"SUMMARY: {ticker} | SELL {agent_type} | "
                    f"Strike ${strike} exp {exp} | IV {iv}% (Rank {iv_rank}) | "
                    f"Premium ${premium} ({premium_pct}%)"
                )
            else:
                iv = json_data.get("iv", "?")
                iv_rank = json_data.get("iv_rank", "?")
                reason_short = (json_data.get("reason") or "")[:80]
                waiting = json_data.get("waiting_for") or ""
                summary = (
                    f"SUMMARY: {ticker} | WAIT | IV {iv}% (Rank {iv_rank}) "
                    f"{reason_short} | Waiting for: {waiting}"
                )
            return summary, json_data

        # Fallback: legacy pipe-delimited line
        for line in response_text.split('\n'):
            if ticker in line and ('SELL' in line.upper() or 'WAIT' in line.upper()):
                return line.strip(), None

        # Last resort: synthesise a summary
        activity = "SELL" if "SELL" in response_text.upper() and "CLEAR SELL ALERT" in response_text.upper() else "WAIT"
        reason = response_text[:100].replace('\n', ' ').strip()
        return f"{ticker} | ACTIVITY: {activity} | Reason: {reason}", None

    # ------------------------------------------------------------------
    # Premium validation against actual chain data
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_premium_against_chain(
        json_data: Optional[Dict],
        raw_chain: str,
        symbol: str,
        agent_type: str,
    ) -> Optional[Dict]:
        """Cross-check the agent's reported premium against the actual chain data.

        If the agent reported a strike + expiration + premium, look up the actual
        bid in the parsed chain. If they don't match, correct the premium and
        log a warning. Returns the corrected json_data (or original if valid).
        """
        if json_data is None:
            return json_data

        try:
            activity = str(json_data.get("activity", "")).upper().strip()

            # Determine if this is a roll action
            is_roll = activity in VALID_ROLL_ACTIONS

            # For watchlist agents: only validate SELL activities
            if not is_roll and activity != "SELL":
                return json_data

            # Determine bucket key based on agent type
            _put_types = {"cash_secured_put", "open_put", "open_put_monitor"}
            _call_types = {"covered_call", "open_call", "open_call_monitor"}
            if agent_type in _put_types:
                bucket_key = "puts"
            elif agent_type in _call_types:
                bucket_key = "calls"
            else:
                logger.debug(
                    "Premium validation: unknown agent_type '%s' for %s — skipping",
                    agent_type, symbol,
                )
                return json_data

            try:
                structured = json.loads(raw_chain) if isinstance(raw_chain, str) else raw_chain
            except (json.JSONDecodeError, TypeError):
                structured = {"calls": {}, "puts": {}}
            bucket = structured.get(bucket_key, {})
            if not bucket:
                logger.debug(
                    "Premium validation: no %s data in chain for %s — skipping",
                    bucket_key, symbol,
                )
                return json_data

            # --- Validate primary premium (SELL or new leg of ROLL) ---
            if is_roll:
                strike_val = json_data.get("new_strike")
                exp_val = json_data.get("new_expiration")
                premium_val = json_data.get("new_premium")
                # Also check inside roll_economics
                re_block = json_data.get("roll_economics")
                if isinstance(re_block, dict):
                    if premium_val is None:
                        premium_val = re_block.get("new_premium")
            else:
                strike_val = json_data.get("strike")
                exp_val = json_data.get("expiration")
                premium_val = json_data.get("premium")

            if strike_val is not None and exp_val is not None and premium_val is not None:
                json_data = AgentRunner._validate_single_premium(
                    json_data, bucket, bucket_key,
                    strike_val, exp_val, premium_val,
                    symbol, is_roll=is_roll, field_prefix="new_" if is_roll else "",
                )

            # --- For rolls, also validate buyback cost (ask of current contract) ---
            if is_roll:
                re_block = json_data.get("roll_economics")
                if isinstance(re_block, dict):
                    bb_cost = re_block.get("buyback_cost")
                    cur_strike = json_data.get("current_strike")
                    cur_exp = json_data.get("current_expiration")
                    if bb_cost is not None and cur_strike is not None and cur_exp is not None:
                        json_data = AgentRunner._validate_buyback_cost(
                            json_data, bucket, bucket_key,
                            cur_strike, cur_exp, bb_cost, symbol,
                        )

        except Exception:
            logger.debug(
                "Premium validation error for %s — skipping:\n%s",
                symbol, traceback.format_exc(),
            )

        return json_data

    @staticmethod
    def _validate_single_premium(
        json_data: Dict,
        bucket: Dict,
        bucket_key: str,
        strike_val,
        exp_val,
        premium_val,
        symbol: str,
        is_roll: bool = False,
        field_prefix: str = "",
    ) -> Dict:
        """Validate a single premium value against chain data."""
        exp_key = str(exp_val).replace("-", "")
        exp_data = bucket.get(exp_key)
        if exp_data is None:
            logger.warning(
                "Premium validation: expiration %s not found in %s chain for %s — cannot verify",
                exp_key, bucket_key, symbol,
            )
            return json_data

        # Try multiple strike key formats
        strike_float = float(strike_val)
        strike_keys = [
            str(strike_float),
            f"{strike_float:.1f}",
            f"{strike_float:.2f}",
        ]
        contract = None
        matched_key = None
        for sk in strike_keys:
            if sk in exp_data:
                contract = exp_data[sk]
                matched_key = sk
                break

        if contract is None:
            logger.warning(
                "Premium validation: strike %s not found in %s[%s] for %s — cannot verify",
                strike_val, bucket_key, exp_key, symbol,
            )
            return json_data

        actual_bid = contract.get("bid")
        if actual_bid is None:
            return json_data

        try:
            reported = float(premium_val)
            actual = float(actual_bid)
        except (TypeError, ValueError):
            return json_data

        if abs(reported - actual) <= 0.02:
            logger.debug(
                "Premium validation OK for %s: %s[%s][%s] bid=$%.2f matches reported $%.2f",
                symbol, bucket_key, exp_key, matched_key, actual, reported,
            )
            return json_data

        # Mismatch — correct it
        logger.warning(
            "Premium mismatch for %s: agent reported $%.2f but chain shows $%.2f "
            "for %s['%s']['%s']. Correcting.",
            symbol, reported, actual, bucket_key, exp_key, matched_key,
        )

        if is_roll:
            json_data["new_premium"] = actual
            re_block = json_data.get("roll_economics")
            if isinstance(re_block, dict):
                re_block["new_premium"] = actual
                # Recalculate net credit if buyback_cost exists
                bb = re_block.get("buyback_cost")
                if bb is not None:
                    try:
                        re_block["net_credit"] = round(actual - float(bb), 2)
                    except (TypeError, ValueError):
                        pass
        else:
            json_data["premium"] = actual
            # Recalculate premium_pct
            try:
                if bucket_key == "puts":
                    json_data["premium_pct"] = round(
                        (actual / strike_float) * 100, 2
                    )
                else:
                    underlying = json_data.get("underlying_price")
                    if underlying is not None:
                        json_data["premium_pct"] = round(
                            (actual / float(underlying)) * 100, 2
                        )
            except (TypeError, ValueError, ZeroDivisionError):
                pass

        json_data["premium_corrected"] = True

        # Also correct delta if chain has a different value
        chain_delta = contract.get("delta")
        if chain_delta is not None:
            reported_delta = json_data.get("delta")
            if reported_delta is not None:
                try:
                    if abs(float(reported_delta) - float(chain_delta)) > 0.01:
                        logger.warning(
                            "Delta mismatch for %s: agent reported %s but chain shows %s. Correcting.",
                            symbol, reported_delta, chain_delta,
                        )
                        json_data["delta"] = float(chain_delta)
                except (TypeError, ValueError):
                    pass

        return json_data

    @staticmethod
    def _validate_buyback_cost(
        json_data: Dict,
        bucket: Dict,
        bucket_key: str,
        cur_strike,
        cur_exp,
        buyback_cost,
        symbol: str,
    ) -> Dict:
        """Validate buyback cost (ask price of current contract) against chain."""
        exp_key = str(cur_exp).replace("-", "")
        exp_data = bucket.get(exp_key)
        if exp_data is None:
            return json_data

        strike_float = float(cur_strike)
        strike_keys = [str(strike_float), f"{strike_float:.1f}", f"{strike_float:.2f}"]
        contract = None
        for sk in strike_keys:
            if sk in exp_data:
                contract = exp_data[sk]
                break

        if contract is None:
            return json_data

        actual_ask = contract.get("ask")
        if actual_ask is None:
            return json_data

        try:
            reported = float(buyback_cost)
            actual = float(actual_ask)
        except (TypeError, ValueError):
            return json_data

        if abs(reported - actual) <= 0.02:
            return json_data

        logger.warning(
            "Buyback cost mismatch for %s: agent reported $%.2f but chain shows ask $%.2f "
            "for %s['%s']. Correcting.",
            symbol, reported, actual, bucket_key, exp_key,
        )

        re_block = json_data.get("roll_economics")
        if isinstance(re_block, dict):
            re_block["buyback_cost"] = actual
            # Recalculate net credit
            new_prem = re_block.get("new_premium")
            if new_prem is not None:
                try:
                    re_block["net_credit"] = round(float(new_prem) - actual, 2)
                except (TypeError, ValueError):
                    pass
        json_data["premium_corrected"] = True

        return json_data

    # Activities that are NOT alerts (non-actionable states)
    _NON_ALERT_ACTIVITIES = frozenset({
        "WAIT", "HOLD", "DO_NOTHING", "DOING_NOTHING", "SKIPPED",
    })

    # Roll activities that trigger alerts (position monitors)
    _ROLL_ACTIVITIES = frozenset({
        "ROLL_UP", "ROLL_DOWN", "ROLL_OUT",
        "ROLL_UP_AND_OUT", "ROLL_DOWN_AND_OUT", "CLOSE",
    })

    def _is_alert(self, response_text: str, json_data: Optional[Dict] = None) -> bool:
        """Check if response indicates an alert.
        
        Rule: Anything that is NOT wait, hold, doing nothing, or skipped is an alert.
        This includes SELL, ROLL_*, CLOSE, and any other action-oriented activities.
        """
        if json_data is not None:
            activity = json_data.get("activity", "").upper().strip()
            if activity:
                # If activity is NOT in the non-alert list, it's an alert
                return activity not in self._NON_ALERT_ACTIVITIES
        
        # Fallback text check - look for non-alert keywords
        upper = response_text.upper()
        # Check if it explicitly states a non-alert activity
        for non_alert in self._NON_ALERT_ACTIVITIES:
            if f"ACTIVITY: {non_alert}" in upper or f'"activity": "{non_alert}"' in upper.replace(" ", ""):
                return False
        
        # If we find any activity indicator but no non-alert match, assume it's an alert
        if "ACTIVITY:" in upper or '"activity"' in upper:
            return True
        
        # Legacy fallback: check for explicit alert indicators
        return "CLEAR SELL ALERT" in upper or "🚨" in response_text or "ALERT: SELL" in upper

    def _extract_alert_enrichment(self, json_data: Optional[Dict]) -> Dict:
        """Extract alert-specific enrichment fields (confidence, risk_flags).
        
        Returns a dict with only alert-enrichment fields present in json_data.
        Per Danny's unified schema: alerts are activities with is_alert=true
        and these additional fields merged in.
        """
        enrichment = {}
        if json_data is not None:
            if "confidence" in json_data:
                enrichment["confidence"] = json_data["confidence"]
            if "risk_flags" in json_data:
                enrichment["risk_flags"] = json_data["risk_flags"]
        return enrichment

    # ------------------------------------------------------------------
    # Prolonged WAIT detection
    # ------------------------------------------------------------------

    def _detect_prolonged_wait(
        self,
        cosmos: "CosmosDBService",
        symbol: str,
        agent_type: str,
        position_id: str | None = None,
        threshold: int | None = None,
    ) -> bool:
        """Check if the last N activities for this symbol/position were all WAITs.

        Returns True if the most recent ``threshold`` activities are all
        non-alert WAIT decisions (indicating the agent has been passive
        for too long) AND enough WAITs have passed since the last
        supervisor/alpha review (cooldown).  Never raises — returns False on
        any error so the pipeline is never blocked.
        """
        if threshold is None:
            threshold = self.PROLONGED_WAIT_THRESHOLD
        try:
            # Fetch enough activities to check both threshold and cooldown
            fetch_count = threshold + self.SUPERVISOR_COOLDOWN + 5
            recent = cosmos.get_recent_activities(
                symbol=symbol,
                agent_type=agent_type,
                max_entries=fetch_count,
                position_id=position_id,
                include_alerts=True,
            )
            if len(recent) < threshold:
                return False
            # Check that the most recent `threshold` activities are all WAITs
            for act in recent[:threshold]:
                if act.get("error"):
                    return False
                if act.get("is_alert", False):
                    return False
                activity = str(act.get("activity", "")).upper()
                if activity != "WAIT":
                    return False

            # Cooldown: count WAITs since the last alpha review
            waits_since_last_review = 0
            for act in recent:
                if act.get("alpha_view"):
                    break
                waits_since_last_review += 1
            # If a previous review exists, enforce cooldown
            if waits_since_last_review < len(recent) and waits_since_last_review < self.SUPERVISOR_COOLDOWN:
                return False

            return True
        except Exception:
            logger.debug(
                "Prolonged WAIT check failed for %s/%s — defaulting to False",
                symbol, agent_type, exc_info=True,
            )
            return False

    # ------------------------------------------------------------------
    # Supervisor Agent — post-decision quality audit (Phase 3a)
    # ------------------------------------------------------------------

    async def _run_supervisor_review(
        self,
        activity_payload: dict,
        market_data: str,
        previous_context: str,
        agent_type: str,
        model: str = None,
    ) -> dict | None:
        """Run a supervisor agent to audit the primary decision.

        Creates a separate agent instance with supervisor instructions.
        Returns the parsed supervisor_view dict, or None on failure.
        The supervisor MUST NEVER block the primary decision flow.
        """
        try:
            activity_str = activity_payload.get("activity", "SELL")
            decision_type = activity_str.upper()

            _AGENT_TYPE_MAP = {
                "open_call_monitor": "open_call",
                "open_put_monitor": "open_put",
            }
            supervisor_agent_type = _AGENT_TYPE_MAP.get(agent_type, agent_type)

            instructions = get_supervisor_instructions(supervisor_agent_type, decision_type)

            message = f"""Audit the following trading decision:

=== DECISION TO AUDIT ===
{json.dumps(activity_payload, indent=2, default=str)}

=== MARKET DATA ===
{market_data}

=== PREVIOUS CONTEXT (decision history) ===
{previous_context}

=== OUTPUT FORMAT ===
{SUPERVISOR_OUTPUT_SCHEMA}

Provide your supervisor audit in the JSON format specified above."""

            agent = Agent(
                client=self._get_client(model),
                name=f"Supervisor_{agent_type}",
                instructions=instructions,
            )
            result = await agent.run(message)
            response_text = result.text or str(result)

            logger.info(
                "Supervisor review completed for %s — response length=%d",
                activity_payload.get("symbol", "?"), len(response_text),
            )
            logger.debug(
                "Supervisor first 500 chars: %s", response_text[:500],
            )

            # Parse JSON from response
            supervisor_data = None
            for block in re.findall(r'```json\s*\n(.*?)```', response_text, re.DOTALL):
                try:
                    supervisor_data = json.loads(block.strip())
                    break
                except json.JSONDecodeError:
                    continue

            if supervisor_data is None and '"challenge_strength"' in response_text:
                for i in range(len(response_text)):
                    if response_text[i] == '{':
                        depth = 0
                        for j in range(i, len(response_text)):
                            if response_text[j] == '{':
                                depth += 1
                            elif response_text[j] == '}':
                                depth -= 1
                                if depth == 0:
                                    try:
                                        parsed = json.loads(response_text[i:j + 1])
                                        if "challenge_strength" in parsed:
                                            supervisor_data = parsed
                                    except json.JSONDecodeError:
                                        pass
                                    break
                        if supervisor_data is not None:
                            break

            if supervisor_data is None:
                logger.warning("Supervisor returned no parseable JSON")
                return None

            required = {"challenge_strength", "counter_arguments", "net_assessment"}
            missing = required - set(supervisor_data.keys())
            if missing:
                logger.warning("Supervisor JSON missing fields: %s", missing)
                return None

            # Derive one_liner if the LLM omitted it
            if "one_liner" not in supervisor_data:
                supervisor_data["one_liner"] = str(
                    supervisor_data.get("net_assessment", "See audit details")
                )[:120]
                logger.info("Supervisor: derived one_liner from net_assessment")

            strength = str(supervisor_data.get("challenge_strength", "")).upper()
            if strength not in ("WEAK", "MODERATE", "STRONG"):
                logger.warning("Supervisor invalid challenge_strength: %s", strength)
                return None
            supervisor_data["challenge_strength"] = strength

            logger.info(
                "Supervisor audit for %s: strength=%s one_liner=%s",
                activity_payload.get("symbol", "?"),
                strength,
                str(supervisor_data.get("one_liner", ""))[:80],
            )
            return supervisor_data

        except Exception:
            logger.warning(
                "Supervisor review failed for %s — original decision unaffected",
                activity_payload.get("symbol", "?"),
                exc_info=True,
            )
            return None

    # ------------------------------------------------------------------
    # Alpha Advisor — aggressive perspective (Phase 3b)
    # ------------------------------------------------------------------

    async def _run_alpha_review(
        self,
        activity_payload: dict,
        market_data: str,
        previous_context: str,
        agent_type: str,
        model: str = None,
    ) -> dict | None:
        """Run an alpha advisor agent for aggressive perspective.

        Creates a separate agent instance with alpha advisor instructions.
        Returns the parsed alpha_view dict, or None on failure.
        The alpha advisor MUST NEVER block the primary decision flow.
        """
        try:
            activity_str = activity_payload.get("activity", "SELL")
            decision_type = activity_str.upper()

            _AGENT_TYPE_MAP = {
                "open_call_monitor": "open_call",
                "open_put_monitor": "open_put",
            }
            alpha_agent_type = _AGENT_TYPE_MAP.get(agent_type, agent_type)

            instructions = get_alpha_instructions(alpha_agent_type, decision_type)

            message = f"""Provide an aggressive alternative perspective on this decision:

=== DECISION TO REVIEW ===
{json.dumps(activity_payload, indent=2, default=str)}

=== MARKET DATA ===
{market_data}

=== PREVIOUS CONTEXT (decision history) ===
{previous_context}

=== OUTPUT FORMAT ===
{json.dumps(ALPHA_OUTPUT_SCHEMA, indent=2)}

Provide your alpha advisor analysis in the JSON format specified above."""

            agent = Agent(
                client=self._get_client(model),
                name=f"Alpha_{agent_type}",
                instructions=instructions,
            )
            result = await agent.run(message)
            response_text = result.text or str(result)

            logger.info(
                "Alpha review completed for %s — response length=%d",
                activity_payload.get("symbol", "?"), len(response_text),
            )
            logger.debug(
                "Alpha first 500 chars: %s", response_text[:500],
            )

            # Parse JSON from response
            alpha_data = None
            for block in re.findall(r'```(?:json)?\s*\n(.*?)```', response_text, re.DOTALL):
                try:
                    alpha_data = json.loads(block.strip())
                    break
                except json.JSONDecodeError:
                    continue

            if alpha_data is None and ('opportunity_strength' in response_text):
                for i in range(len(response_text)):
                    if response_text[i] == '{':
                        depth = 0
                        for j in range(i, len(response_text)):
                            if response_text[j] == '{':
                                depth += 1
                            elif response_text[j] == '}':
                                depth -= 1
                                if depth == 0:
                                    candidate = response_text[i:j + 1]
                                    try:
                                        parsed = json.loads(candidate)
                                        if "opportunity_strength" in parsed:
                                            alpha_data = parsed
                                    except json.JSONDecodeError:
                                        # Try fixing single quotes → double quotes
                                        try:
                                            fixed = candidate.replace("'", '"')
                                            parsed = json.loads(fixed)
                                            if "opportunity_strength" in parsed:
                                                alpha_data = parsed
                                                logger.info("Alpha: parsed after single-quote fix")
                                        except json.JSONDecodeError:
                                            pass
                                    break
                        if alpha_data is not None:
                            break

            if alpha_data is None:
                logger.warning(
                    "Alpha Advisor returned no parseable JSON. "
                    "First 800 chars: %s", response_text[:800],
                )
                return None

            required = {"opportunity_strength", "alternative"}
            missing = required - set(alpha_data.keys())
            if missing:
                logger.warning("Alpha Advisor JSON missing fields: %s", missing)
                return None

            # Derive one_liner if the LLM omitted it
            if "one_liner" not in alpha_data:
                alt = alpha_data.get("alternative", {})
                alpha_data["one_liner"] = str(
                    alt.get("action", "See alternative details")
                )[:120]
                logger.info("Alpha Advisor: derived one_liner from alternative.action")

            strength = str(alpha_data.get("opportunity_strength", "")).upper()
            if strength not in ("NONE", "MODERATE", "STRONG"):
                logger.warning("Alpha Advisor invalid opportunity_strength: %s", strength)
                return None
            alpha_data["opportunity_strength"] = strength

            logger.info(
                "Alpha Advisor for %s: opportunity=%s one_liner=%s",
                activity_payload.get("symbol", "?"),
                strength,
                str(alpha_data.get("one_liner", ""))[:80],
            )
            return alpha_data

        except Exception:
            logger.warning(
                "Alpha review failed for %s — original decision unaffected",
                activity_payload.get("symbol", "?"),
                exc_info=True,
            )
            return None

    def _build_market_data_block(self, data: dict, symbol: str, exchange: str) -> str:
        """Build the market data text block for supervisor/alpha context."""
        return f"""--- OVERVIEW PAGE ({exchange}:{symbol}) ---
{data.get('overview', '')}

--- TECHNICALS PAGE ({exchange}:{symbol}) ---
{data.get('technicals', '')}

--- FORECAST PAGE ({exchange}:{symbol}) ---
{data.get('forecast', '')}

--- DIVIDENDS PAGE ({exchange}:{symbol}) ---
{data.get('dividends', '')}"""

    async def run_symbol_agent(
        self,
        name: str,
        instructions: str,
        symbol: str,
        exchange: str,
        agent_type: str,
        cosmos: CosmosDBService,
        context_provider: ContextProvider,
        max_activity_entries: int = 2,
        fetcher=None,
        model: str = None,
        supervisor_model: str = None,
        alpha_model: str = None,
    ):
        """Run agent analysis for a single symbol.

        Args:
            name: Agent name (e.g. "CoveredCallAgent")
            instructions: Base instructions for the agent
            symbol: Ticker symbol (e.g. "AAPL")
            exchange: Exchange code (e.g. "NASDAQ")
            agent_type: Agent type key (e.g. "covered_call")
            cosmos: CosmosDBService instance for persistence
            context_provider: ContextProvider for activity history injection
            max_activity_entries: Max recent activities for context (0–5)
            fetcher: YFinanceDataProvider instance (shared across symbols)
        """
        print(f"\n--- Analyzing {symbol} ---")
        logger.info("Starting pre-fetch + agent.run() for symbol=%s", symbol)

        analysis_ts = datetime.now().strftime(TIMESTAMP_FORMAT)
        run_start = time.time()

        try:
            # Context injection from CosmosDB
            previous_context = context_provider.get_context(
                symbol, agent_type, max_entries=max_activity_entries,
            )

            # Pre-fetch all market data via yfinance
            data = await fetcher.fetch_all(symbol, force_refresh=True)

            message = f"""Analyze {symbol} (exchange: {exchange}).

=== PRE-FETCHED MARKET DATA ===

--- OVERVIEW ({symbol}) ---
{data['overview']}

--- TECHNICALS ({symbol}) ---
{data['technicals']}

--- FORECAST ({symbol}) ---
{data['forecast']}

--- DIVIDENDS ({symbol}) ---
{data['dividends']}

--- OPTIONS CHAIN ({symbol}) ---
{self._format_options_chain(data.get('options_chain', ''), symbol)}

=== END OF DATA ===

Previous activities for {symbol}:
{previous_context}

Current timestamp: {analysis_ts}
All market data has been pre-fetched above. Do NOT use any browser tools — analyze the data provided and output your activity in the required JSON format. Use the timestamp above in your JSON output; do NOT generate your own."""

            agent = Agent(
                client=self._get_client(model),
                name=name,
                instructions=instructions,
            )
            result = await agent.run(message)
            response_text = result.text or str(result)

            logger.info(
                "agent.run() completed for %s – response length=%d",
                symbol, len(response_text),
            )
            logger.debug(
                "Response first 500 chars for %s: %s",
                symbol, response_text[:500],
            )

            print(f"Response: {response_text[:200]}...")

            # Parse activity from agent output
            activity_line, json_data = self._extract_activity_line(symbol, response_text)

            # Validate premium against actual chain data
            if json_data is not None:
                json_data = self._validate_premium_against_chain(
                    json_data, data.get('options_chain', ''), symbol, agent_type,
                )

            # Build activity payload
            activity_payload: Dict = {}
            if json_data is not None:
                activity_payload = dict(json_data)
                activity_payload["timestamp"] = analysis_ts
            else:
                activity_payload = {
                    "symbol": symbol,
                    "exchange": exchange,
                    "summary": activity_line,
                    "timestamp": analysis_ts,
                }

            # Determine if this is an alert (anything NOT wait/hold/do_nothing)
            is_alert = self._is_alert(response_text, json_data)
            activity_payload["is_alert"] = is_alert
            
            # If alert, merge alert-enrichment fields into activity payload
            if is_alert:
                alert_enrichment = self._extract_alert_enrichment(json_data)
                activity_payload.update(alert_enrichment)

            # Write activity to CosmosDB (unified write path)
            dec_doc = cosmos.write_activity(
                symbol=symbol,
                agent_type=agent_type,
                activity_data=activity_payload,
                timestamp=analysis_ts,
            )
            
            # ── Supervisor always runs; Alpha only on alerts / prolonged waits ──
            market_data = self._build_market_data_block(data, symbol, exchange)
            prolonged_wait = False

            if is_alert:
                print(f"⚠️ SELL ALERT logged for {symbol}")
                # Both supervisor + alpha run in parallel
                supervisor_view, alpha_view = await asyncio.gather(
                    self._run_supervisor_review(
                        activity_payload=activity_payload,
                        market_data=market_data,
                        previous_context=previous_context,
                        agent_type=agent_type,
                        model=supervisor_model,
                    ),
                    self._run_alpha_review(
                        activity_payload=activity_payload,
                        market_data=market_data,
                        previous_context=previous_context,
                        agent_type=agent_type,
                        model=alpha_model,
                    ),
                )
            else:
                prolonged_wait = self._detect_prolonged_wait(cosmos, symbol, agent_type)
                if prolonged_wait:
                    print(f"⏳ Prolonged WAIT detected for {symbol} — triggering supervisor + alpha review")
                    # Both supervisor + alpha run in parallel
                    supervisor_view, alpha_view = await asyncio.gather(
                        self._run_supervisor_review(
                            activity_payload=activity_payload,
                            market_data=market_data,
                            previous_context=previous_context,
                            agent_type=agent_type,
                            model=supervisor_model,
                        ),
                        self._run_alpha_review(
                            activity_payload=activity_payload,
                            market_data=market_data,
                            previous_context=previous_context,
                            agent_type=agent_type,
                            model=alpha_model,
                        ),
                    )
                else:
                    # Supervisor runs alone (unconditional)
                    supervisor_view = await self._run_supervisor_review(
                        activity_payload=activity_payload,
                        market_data=market_data,
                        previous_context=previous_context,
                        agent_type=agent_type,
                        model=supervisor_model,
                    )
                    alpha_view = None
                    print(f"Logged activity")

            # Persist supervisor result (always)
            if supervisor_view is not None:
                cosmos.update_activity_field(
                    doc_id=dec_doc["id"],
                    symbol=symbol,
                    field="supervisor_view",
                    value=supervisor_view,
                )
                print(f"🛡️ Supervisor [{supervisor_view['challenge_strength']}]: {supervisor_view['one_liner']}")

            # Persist alpha result (conditional)
            if alpha_view is not None:
                cosmos.update_activity_field(
                    doc_id=dec_doc["id"],
                    symbol=symbol,
                    field="alpha_view",
                    value=alpha_view,
                )
                print(f"🔍 Alpha [{alpha_view['opportunity_strength']}]: {alpha_view['one_liner']}")

            # Telegram notifications
            if is_alert and self.telegram_notifier:
                alert_data = {
                    "timestamp": analysis_ts,
                    "symbol": symbol,
                    "exchange": exchange,
                    "activity": json_data.get("activity", "SELL") if json_data else "SELL",
                    "strike": json_data.get("strike") if json_data else None,
                    "expiration": json_data.get("expiration") if json_data else None,
                    "underlying_price": json_data.get("underlying_price") if json_data else None,
                    "confidence": json_data.get("confidence") if json_data else None,
                    "risk_rating": json_data.get("risk_rating") if json_data else None,
                    "risk_flags": json_data.get("risk_flags") if json_data else None,
                    "premium": json_data.get("premium") if json_data else None,
                }
                if supervisor_view is not None:
                    alert_data["supervisor_view"] = supervisor_view
                if alpha_view is not None:
                    alert_data["alpha_view"] = alpha_view
                self.telegram_notifier.send_alert(
                    symbol=symbol, agent_type=agent_type,
                    alert_data=alert_data, is_roll=False,
                )
            elif prolonged_wait and self.telegram_notifier:
                has_supervisor_finding = (supervisor_view is not None and
                                          supervisor_view.get("challenge_strength") in ("MODERATE", "STRONG"))
                has_alpha_finding = (alpha_view is not None and
                                    alpha_view.get("opportunity_strength") in ("MODERATE", "STRONG"))
                if has_supervisor_finding or has_alpha_finding:
                    self.telegram_notifier.send_prolonged_wait_alert(
                        symbol=symbol,
                        agent_type=agent_type,
                        supervisor_view=supervisor_view,
                        alpha_view=alpha_view,
                        consecutive_waits=self.PROLONGED_WAIT_THRESHOLD,
                        underlying_price=json_data.get("underlying_price") if json_data else None,
                    )

        except Exception as e:
            logger.error(
                "agent.run() FAILED for %s:\n%s",
                symbol, traceback.format_exc(),
            )
            print(f"Error analyzing {symbol}: {e}")
            cosmos.write_activity(
                symbol=symbol,
                agent_type=agent_type,
                activity_data={
                    "error": str(e),
                    "symbol": symbol,
                    "exchange": exchange,
                    "timestamp": analysis_ts,
                    "is_alert": False,
                },
                timestamp=analysis_ts,
            )

        # ── Telemetry (best-effort, never blocks) ─────────────────
        try:
            total_duration = round(time.time() - run_start, 2)
            cosmos.write_telemetry("agent_run", {
                "symbol": symbol,
                "agent_type": agent_type,
                "duration_seconds": total_duration,
            })
        except Exception:
            logger.debug("Telemetry write skipped for %s", symbol)

    # ------------------------------------------------------------------
    # Position Monitor (single position, CosmosDB-backed)
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Phase 1 → Phase 2 handoff detection
    # ------------------------------------------------------------------

    @staticmethod
    def _try_extract_handoff_json(response_text: str) -> Optional[Dict]:
        """Extract a handoff JSON block from Phase 1 output.

        A handoff block contains ``action_needed`` (a ROLL_* action)
        signalling that Phase 2 (roll management) should run.  Returns *None*
        when the output is a WAIT activity, the action is invalid, or cannot
        be parsed.

        Invalid actions (bare ``ROLL`` or unknown values) are rejected here
        with a warning so Phase 2 never runs with an ambiguous direction.
        """

        def _validate_action(data: Dict) -> Optional[Dict]:
            """Return *data* if action_needed is valid, else *None*."""
            action = str(data.get("action_needed", "")).upper().strip()
            if action in VALID_ROLL_ACTIONS:
                return data
            if action == "ROLL":
                logger.warning(
                    "Phase 1 returned bare 'ROLL' (no direction) — "
                    "rejecting handoff (will treat as WAIT)",
                )
            else:
                logger.warning(
                    "Phase 1 returned invalid action_needed '%s' — "
                    "rejecting handoff (will treat as WAIT)",
                    action,
                )
            return None

        # Check fenced ```json blocks first
        for block in re.findall(r'```json\s*\n(.*?)```', response_text, re.DOTALL):
            block = block.strip()
            try:
                data = json.loads(block)
                if isinstance(data, dict) and "action_needed" in data:
                    return _validate_action(data)
            except json.JSONDecodeError:
                continue

        # Fallback: raw JSON object containing "action_needed"
        for match in re.finditer(r'\{[^{}]*"action_needed"\s*:', response_text):
            start = match.start()
            depth = 0
            for i in range(start, len(response_text)):
                if response_text[i] == '{':
                    depth += 1
                elif response_text[i] == '}':
                    depth -= 1
                    if depth == 0:
                        candidate = response_text[start:i + 1]
                        try:
                            data = json.loads(candidate)
                            if isinstance(data, dict) and "action_needed" in data:
                                return _validate_action(data)
                        except json.JSONDecodeError:
                            break
        return None

    # ------------------------------------------------------------------
    # Phase 1: Position Assessment
    # ------------------------------------------------------------------

    async def _run_position_assessment(
        self,
        name: str,
        instructions: str,
        symbol: str,
        exchange: str,
        position_type: str,
        strike: float,
        expiration: str,
        data: dict,
        previous_context: str,
        analysis_ts: str,
        current_contract_chain: str = "",
        model: str = None,
    ) -> Tuple[str, Optional[Dict], Optional[Dict]]:
        """Run Phase 1 — position assessment agent.

        Returns:
            (response_text, activity_json, handoff_json)
            - activity_json is set when agent outputs a standard activity (WAIT).
            - handoff_json is set when agent outputs an action_needed (ROLL).
            Exactly one of activity_json / handoff_json will be non-None on success.
        """
        message = f"""Analyze open {position_type} position for {symbol}:
- Current strike: ${strike}
- Current expiration: {expiration}
- Exchange: {exchange}

=== PRE-FETCHED MARKET DATA ===

--- OVERVIEW ({symbol}) ---
{data['overview']}

--- TECHNICALS ({symbol}) ---
{data['technicals']}

--- FORECAST ({symbol}) ---
{data['forecast']}

--- CURRENT CONTRACT ({position_type.upper()} ${strike} exp {expiration}) ---
{current_contract_chain}

=== END OF DATA ===

Previous monitor activities for {symbol}:
{previous_context}

Current timestamp: {analysis_ts}
Analyze the position risk and output your response in the required JSON format. Use the timestamp above in your JSON output; do NOT generate your own."""

        agent = Agent(
            client=self._get_client(model),
            name=name,
            instructions=instructions,
        )
        result = await agent.run(message)
        response_text = result.text or str(result)

        logger.info(
            "Phase 1 (assessment) completed for %s – response length=%d",
            symbol, len(response_text),
        )
        logger.debug(
            "Phase 1 first 500 chars for %s: %s",
            symbol, response_text[:500],
        )
        print(f"Phase 1 response: {response_text[:200]}...")

        # Try handoff first (action_needed), then standard activity
        handoff_json = self._try_extract_handoff_json(response_text)
        if handoff_json is not None:
            return response_text, None, handoff_json

        # Standard activity (WAIT path)
        activity_json = self._try_extract_json(response_text)
        return response_text, activity_json, None

    # ------------------------------------------------------------------
    # Phase 2: Roll Management
    # ------------------------------------------------------------------

    async def _run_roll_management(
        self,
        name: str,
        roll_instructions: str,
        handoff_json: Dict,
        filtered_chain_text: str,
        analysis_ts: str,
        full_symbol: str,
        model: str = None,
    ) -> Tuple[str, Optional[Dict]]:
        """Run Phase 2 — roll management agent.

        Receives the Phase 1 handoff payload and the full filtered options
        chain.  Returns (response_text, json_data) following the same
        activity schema as the original single-agent output.
        """
        phase1_text = json.dumps(handoff_json, indent=2)

        message = f"""POSITION ASSESSMENT RESULT:
{phase1_text}

ROLL CANDIDATES:
{filtered_chain_text}

Based on the assessment and candidates above, select the best roll candidate and produce the final activity JSON.
Pick a candidate by its row number. Use the pre-computed values (net credit, DTE, etc.) directly — do NOT recalculate.

Current timestamp: {analysis_ts}
Output your activity in the required JSON format. Use the timestamp above in your JSON output; do NOT generate your own."""

        agent = Agent(
            client=self._get_client(model),
            name=f"{name}_roll",
            instructions=roll_instructions,
        )
        result = await agent.run(message)
        response_text = result.text or str(result)

        logger.info(
            "Phase 2 (roll mgmt) completed for %s – response length=%d",
            full_symbol, len(response_text),
        )
        logger.debug(
            "Phase 2 first 500 chars for %s: %s",
            full_symbol, response_text[:500],
        )
        print(f"Phase 2 response: {response_text[:200]}...")

        json_data = self._try_extract_json(response_text)
        return response_text, json_data

    # ------------------------------------------------------------------
    # Position Monitor — 2-phase orchestrator
    # ------------------------------------------------------------------

    async def run_position_monitor(
        self,
        name: str,
        symbol: str,
        exchange: str,
        position: dict,
        agent_type: str,
        cosmos: CosmosDBService,
        context_provider: ContextProvider,
        max_activity_entries: int = 2,
        fetcher=None,
        assessment_instructions: str = None,
        roll_instructions: str = None,
        assessment_model: str = None,
        roll_model: str = None,
        supervisor_model: str = None,
        alpha_model: str = None,
    ):
        """Run position monitor for a single open position (2-phase).

        * **Phase 1 (Position Assessment):** Evaluates position risk using
          overview, technicals, forecast, and previous context — no full chain.
          If the result is WAIT, persists and returns immediately.
        * **Phase 2 (Roll Management):** Only invoked when Phase 1 decides
          action ≠ WAIT.  Receives the Phase 1 handoff JSON plus the full
          filtered options chain and roll-specific instructions.

        Args:
            name: Agent name (e.g. "OpenCallMonitor")
            symbol: Ticker symbol
            exchange: Exchange code
            position: Position dict with strike, expiration, position_id, type
            agent_type: Agent type key (e.g. "open_call_monitor")
            cosmos: CosmosDBService instance
            context_provider: ContextProvider for history injection
            max_activity_entries: Max recent activities for context (0–5)
            fetcher: YFinanceDataProvider instance (shared)
            assessment_instructions: Phase 1 system instructions
            roll_instructions: Phase 2 system instructions
        """
        strike = position["strike"]
        expiration = position["expiration"]
        position_id = position.get("position_id", "")
        position_type = position.get("type", "call")

        print(f"\n--- Monitoring {symbol} ${strike} exp {expiration} (2-phase) ---")
        logger.info(
            "Position monitor 2-phase for %s strike=%s exp=%s",
            symbol, strike, expiration,
        )

        analysis_ts = datetime.now().strftime(TIMESTAMP_FORMAT)
        run_start = time.time()

        try:
            # Context injection from CosmosDB (filtered by position)
            previous_context = context_provider.get_context(
                symbol, agent_type, max_entries=max_activity_entries,
                position_id=position_id,
            )

            data = await fetcher.fetch_all(symbol, force_refresh=True)

            # Pre-compute the structured filtered chain (for Phase 2)
            try:
                structured_chain = json.loads(data.get('options_chain', '{}'))
            except (json.JSONDecodeError, TypeError):
                structured_chain = {"calls": {}, "puts": {}}
            if structured_chain.get("calls") or structured_chain.get("puts"):
                structured_chain = filter_options_chain_by_type(structured_chain, position_type)
                structured_chain = filter_options_chain_for_position(
                    structured_chain, float(strike), position_type,
                )
                structured_chain = filter_options_chain_by_delta(structured_chain)
            filtered_chain_text = (
                OPTIONS_CHAIN_SCHEMA_DESCRIPTION + "\n"
                + json.dumps(structured_chain, indent=2)
            ) if (structured_chain.get("calls") or structured_chain.get("puts")) else data.get('options_chain', '')

            # ── Two-phase execution ─────────────────────────────────
            # Phase 1 gets only the current contract's chain data
            current_contract_chain = self._format_current_contract_chain(
                data.get('options_chain', ''), symbol,
                current_strike=float(strike),
                expiration=expiration,
                option_type=position_type,
            )

            response_text, activity_json, handoff_json = await self._run_position_assessment(
                name=name,
                instructions=assessment_instructions,
                symbol=symbol,
                exchange=exchange,
                position_type=position_type,
                strike=strike,
                expiration=expiration,
                data=data,
                previous_context=previous_context,
                analysis_ts=analysis_ts,
                current_contract_chain=current_contract_chain,
                model=assessment_model,
            )

            if handoff_json is not None:
                # Phase 1 says action needed → run Phase 2
                logger.info(
                    "Phase 1 triggered action '%s' for %s — launching Phase 2",
                    handoff_json.get("action_needed"), symbol,
                )
                print(f"↪ Phase 1 action: {handoff_json.get('action_needed')} — running roll management…")

                # Apply direction-aware filtering so Phase 2 only sees
                # strikes/expirations valid for the roll direction.
                roll_type = handoff_json.get("action_needed", "")
                _bb_cost = None  # buyback cost — used in error handler
                if roll_type and (structured_chain.get("calls") or structured_chain.get("puts")):
                    direction_filtered = filter_options_chain_by_roll_direction(
                        structured_chain,
                        current_strike=float(strike),
                        current_expiration=expiration,
                        roll_type=roll_type,
                        option_type=position_type,
                    )
                    # Pre-compute candidates as a readable table.
                    # Look up buyback cost from the pre-direction-filtered chain
                    # because direction filtering usually excludes the current contract.
                    _bb_cost = None
                    _bb_bucket_key = "calls" if position_type == "call" else "puts"
                    _bb_bucket = structured_chain.get(_bb_bucket_key, {})
                    _bb_exp_key = expiration.replace("-", "")
                    _bb_strike_key = str(float(strike))
                    if _bb_exp_key in _bb_bucket and _bb_strike_key in _bb_bucket[_bb_exp_key]:
                        _bb_ask = _bb_bucket[_bb_exp_key][_bb_strike_key].get("ask")
                        if _bb_ask is not None:
                            _bb_cost = float(_bb_ask)

                    underlying_px = float(
                        handoff_json.get("underlying_price", 0) or 0
                    )
                    filtered_chain_text = format_roll_candidates_table(
                        chain=direction_filtered,
                        current_strike=float(strike),
                        current_expiration=expiration,
                        option_type=position_type,
                        underlying_price=underlying_px,
                        roll_type=roll_type,
                        buyback_cost=_bb_cost,
                    )

                try:
                    phase2_response, phase2_json = await self._run_roll_management(
                        name=name,
                        roll_instructions=roll_instructions,
                        handoff_json=handoff_json,
                        filtered_chain_text=filtered_chain_text,
                        analysis_ts=analysis_ts,
                        full_symbol=symbol,
                        model=roll_model,
                    )
                    # Use Phase 2 output as the final result
                    response_text = phase2_response
                    json_data = phase2_json

                    # Validate Phase 2 produced usable JSON
                    if json_data is None or "activity" not in (json_data or {}):
                        logger.warning(
                            "Phase 2 returned malformed output for %s — degrading to error payload",
                            symbol,
                        )
                        print(f"⚠️ Phase 2 malformed output for {symbol} — degrading to error payload")
                        raise ValueError("Phase 2 returned no valid activity JSON")

                    # Reject bare "ROLL" from Phase 2 — direction is required
                    p2_activity = str(json_data.get("activity", "")).upper().strip()
                    if p2_activity == "ROLL":
                        logger.warning(
                            "Phase 2 returned bare 'ROLL' for %s — converting to CLOSE",
                            symbol,
                        )
                        json_data["activity"] = "CLOSE"
                        json_data["reason"] = (
                            json_data.get("reason", "")
                            + " [Auto-corrected: bare ROLL converted to CLOSE"
                            " — direction was required]"
                        )
                    elif p2_activity not in VALID_PHASE2_ACTIVITIES:
                        logger.warning(
                            "Phase 2 returned invalid activity '%s' for %s — converting to CLOSE",
                            p2_activity, symbol,
                        )
                        json_data["activity"] = "CLOSE"
                        json_data["reason"] = (
                            json_data.get("reason", "")
                            + f" [Auto-corrected: invalid activity '{p2_activity}'"
                            " converted to CLOSE]"
                        )

                    # Validate that ROLL actions include specific target strike and expiration
                    p2_activity = str(json_data.get("activity", "")).upper().strip()
                    if p2_activity in VALID_ROLL_ACTIONS:
                        new_strike = json_data.get("new_strike")
                        new_expiration = json_data.get("new_expiration")
                        if new_strike is None or new_expiration is None:
                            logger.warning(
                                "Phase 2 returned %s for %s without new_strike/new_expiration — converting to CLOSE",
                                p2_activity, symbol,
                            )
                            json_data["activity"] = "CLOSE"
                            json_data["reason"] = (
                                json_data.get("reason", "")
                                + f" [Auto-corrected: {p2_activity} had no specific target"
                                " (new_strike/new_expiration missing) — converted to CLOSE]"
                            )
                            json_data["new_strike"] = None
                            json_data["new_expiration"] = None
                            json_data["estimated_roll_cost"] = None
                            if "roll_economics" in json_data:
                                json_data["roll_economics"] = None
                        elif json_data.get("roll_economics") is None:
                            logger.warning(
                                "Phase 2 returned %s for %s without roll_economics — converting to CLOSE",
                                p2_activity, symbol,
                            )
                            json_data["activity"] = "CLOSE"
                            json_data["reason"] = (
                                json_data.get("reason", "")
                                + f" [Auto-corrected: {p2_activity} had no roll_economics — converted to CLOSE]"
                            )

                except Exception as phase2_err:
                    # Phase 2 failed — persist as CLOSE with error flag.
                    # We CANNOT emit a ROLL without targets — Phase 2 is
                    # the only agent that selects strike/expiration.
                    logger.error(
                        "Phase 2 (roll mgmt) FAILED for %s: %s\n%s",
                        symbol, phase2_err, traceback.format_exc(),
                    )
                    print(f"⚠️ Phase 2 error for {symbol}: {phase2_err} — persisting as CLOSE with error flag")

                    # Build a degraded CLOSE activity from the handoff JSON
                    _raw_reason = handoff_json.get("reason", "")
                    # Sanitize internal "Agent 2" references — the reason is user-facing
                    _raw_reason = re.sub(
                        r'\s*;?\s*Agent\s*2\s+[^.]*\.?', '', _raw_reason,
                    ).strip()
                    if not _raw_reason:
                        _raw_reason = "Roll was warranted but roll agent failed."

                    # Include buyback cost if available
                    _close_reason = _raw_reason + " [Roll agent unavailable — recommend closing position]"
                    if _bb_cost is not None:
                        _close_reason += f" Buyback cost (ask): ${_bb_cost:.2f}."

                    json_data = {
                        "symbol": handoff_json.get("symbol", symbol),
                        "exchange": handoff_json.get("exchange", exchange),
                        "activity": "CLOSE",
                        "current_strike": handoff_json.get("current_strike", strike),
                        "current_expiration": handoff_json.get("current_expiration", expiration),
                        "underlying_price": handoff_json.get("underlying_price"),
                        "assignment_risk": handoff_json.get("assignment_risk"),
                        "reason": _close_reason,
                        "confidence": handoff_json.get("confidence"),
                        "roll_economics": None,
                        "risk_flags": list(handoff_json.get("risk_flags", [])) + ["roll_agent_error"],
                        "timestamp": analysis_ts,
                    }
            else:
                # Phase 1 returned WAIT — use directly
                json_data = activity_json

            # ── Validate premiums against actual chain data ────────────
            if json_data is not None:
                json_data = self._validate_premium_against_chain(
                    json_data, data.get('options_chain', ''), symbol, agent_type,
                )

            # ── Final safety net: no ROLL without targets ──────────────
            if json_data is not None:
                _final_act = str(json_data.get("activity", "")).upper().strip()
                if _final_act in VALID_ROLL_ACTIONS:
                    if json_data.get("new_strike") is None or json_data.get("new_expiration") is None:
                        logger.warning(
                            "Safety net: %s for %s has no targets — converting to CLOSE",
                            _final_act, symbol,
                        )
                        json_data["activity"] = "CLOSE"
                        json_data["new_strike"] = None
                        json_data["new_expiration"] = None
                        json_data["estimated_roll_cost"] = None
                        if "roll_economics" in json_data:
                            json_data["roll_economics"] = None
                        json_data["reason"] = (
                            json_data.get("reason", "")
                            + f" [Safety net: {_final_act} had no target"
                            " strike/expiration — converted to CLOSE]"
                        )

            # ── Persist activity (common path) ────────────────────────
            activity_line, json_data = self._extract_activity_line(symbol, response_text) if json_data is None else (
                self._extract_summary_line(response_text) or "", json_data
            )

            activity_payload: Dict = {}
            if json_data is not None:
                activity_payload = dict(json_data)
                activity_payload["timestamp"] = analysis_ts
            else:
                activity_payload = {
                    "symbol": symbol,
                    "exchange": exchange,
                    "current_strike": strike,
                    "current_expiration": expiration,
                    "summary": activity_line,
                    "timestamp": analysis_ts,
                }
            activity_payload["position_id"] = position_id

            # Normalize monitor-agent field names so templates/APIs
            # can use standard names (strike, expiration, activity)
            activity_payload.setdefault(
                "strike",
                activity_payload.get("new_strike")
                or activity_payload.get("current_strike"),
            )
            activity_payload.setdefault(
                "expiration",
                activity_payload.get("new_expiration")
                or activity_payload.get("current_expiration"),
            )
            if "action" in activity_payload and "activity" not in activity_payload:
                activity_payload["activity"] = activity_payload["action"]

            # Determine if this is an alert (anything NOT wait/hold/do_nothing)
            is_alert = self._is_alert(response_text, json_data)
            activity_payload["is_alert"] = is_alert

            
            # If alert, merge alert-enrichment fields into activity payload
            if is_alert:
                alert_enrichment = self._extract_alert_enrichment(json_data)
                activity_payload.update(alert_enrichment)

            # Write activity to CosmosDB (unified write path)
            dec_doc = cosmos.write_activity(
                symbol=symbol,
                agent_type=agent_type,
                activity_data=activity_payload,
                timestamp=analysis_ts,
            )
            
            if is_alert:
                print(f"⚠️ ROLL ALERT logged for {symbol} ${strike} exp {expiration}")

                # ── Supervisor always runs; Alpha only on alerts / prolonged waits ──
                market_data = self._build_market_data_block(data, symbol, exchange)
                # Both supervisor + alpha run in parallel
                supervisor_view, alpha_view = await asyncio.gather(
                    self._run_supervisor_review(
                        activity_payload=activity_payload,
                        market_data=market_data,
                        previous_context=previous_context,
                        agent_type=agent_type,
                        model=supervisor_model,
                    ),
                    self._run_alpha_review(
                        activity_payload=activity_payload,
                        market_data=market_data,
                        previous_context=previous_context,
                        agent_type=agent_type,
                        model=alpha_model,
                    ),
                )

                # Persist supervisor result (always)
                if supervisor_view is not None:
                    cosmos.update_activity_field(
                        doc_id=dec_doc["id"],
                        symbol=symbol,
                        field="supervisor_view",
                        value=supervisor_view,
                    )
                    print(f"🛡️ Supervisor [{supervisor_view['challenge_strength']}]: {supervisor_view['one_liner']}")

                # Persist alpha result (conditional)
                if alpha_view is not None:
                    cosmos.update_activity_field(
                        doc_id=dec_doc["id"],
                        symbol=symbol,
                        field="alpha_view",
                        value=alpha_view,
                    )
                    print(f"🔍 Alpha [{alpha_view['opportunity_strength']}]: {alpha_view['one_liner']}")

                if self.telegram_notifier:
                    # Extract roll economics for Telegram notification
                    _re = json_data.get("roll_economics") if json_data else None
                    alert_data = {
                        "timestamp": analysis_ts,
                        "symbol": symbol,
                        "exchange": exchange,
                        "action": json_data.get("activity", "ROLL") if json_data else "ROLL",
                        "current_strike": strike,
                        "current_expiration": expiration,
                        "new_strike": json_data.get("new_strike") if json_data else None,
                        "new_expiration": json_data.get("new_expiration") if json_data else None,
                        "underlying_price": json_data.get("underlying_price") if json_data else None,
                        "confidence": json_data.get("confidence") if json_data else None,
                        "assignment_risk": json_data.get("assignment_risk") if json_data else None,
                        "risk_flags": json_data.get("risk_flags") if json_data else None,
                        "buyback_cost": _re.get("buyback_cost") if isinstance(_re, dict) else None,
                        "new_premium": _re.get("new_premium") if isinstance(_re, dict) else None,
                        "net_credit_debit": _re.get("net_credit") if isinstance(_re, dict) else None,
                    }
                    # Normalize for templates
                    alert_data["activity"] = alert_data["action"]
                    alert_data["strike"] = alert_data.get("new_strike") or alert_data.get("current_strike")
                    alert_data["expiration"] = alert_data.get("new_expiration") or alert_data.get("current_expiration")
                    if supervisor_view is not None:
                        alert_data["supervisor_view"] = supervisor_view
                    if alpha_view is not None:
                        alert_data["alpha_view"] = alpha_view
                    self.telegram_notifier.send_alert(
                        symbol=symbol, agent_type=agent_type,
                        alert_data=alert_data, is_roll=True,
                    )
            else:
                # ── Supervisor always runs; Alpha only on prolonged waits ──
                market_data = self._build_market_data_block(data, symbol, exchange)
                prolonged_wait = self._detect_prolonged_wait(cosmos, symbol, agent_type, position_id=position_id)

                if prolonged_wait:
                    print(f"⏳ Prolonged WAIT detected for {symbol} ${strike} — triggering supervisor + alpha review")
                    # Both supervisor + alpha run in parallel
                    supervisor_view, alpha_view = await asyncio.gather(
                        self._run_supervisor_review(
                            activity_payload=activity_payload,
                            market_data=market_data,
                            previous_context=previous_context,
                            agent_type=agent_type,
                            model=supervisor_model,
                        ),
                        self._run_alpha_review(
                            activity_payload=activity_payload,
                            market_data=market_data,
                            previous_context=previous_context,
                            agent_type=agent_type,
                            model=alpha_model,
                        ),
                    )
                else:
                    # Supervisor runs alone (unconditional)
                    supervisor_view = await self._run_supervisor_review(
                        activity_payload=activity_payload,
                        market_data=market_data,
                        previous_context=previous_context,
                        agent_type=agent_type,
                        model=supervisor_model,
                    )
                    alpha_view = None
                    print(f"Logged activity")

                # Persist supervisor result (always)
                if supervisor_view is not None:
                    cosmos.update_activity_field(
                        doc_id=dec_doc["id"],
                        symbol=symbol,
                        field="supervisor_view",
                        value=supervisor_view,
                    )
                    print(f"🛡️ Supervisor [{supervisor_view['challenge_strength']}]: {supervisor_view['one_liner']}")

                # Persist alpha result (conditional)
                if alpha_view is not None:
                    cosmos.update_activity_field(
                        doc_id=dec_doc["id"],
                        symbol=symbol,
                        field="alpha_view",
                        value=alpha_view,
                    )
                    print(f"🔍 Alpha [{alpha_view['opportunity_strength']}]: {alpha_view['one_liner']}")

                if prolonged_wait and self.telegram_notifier:
                    has_supervisor_finding = (supervisor_view is not None and
                                              supervisor_view.get("challenge_strength") in ("MODERATE", "STRONG"))
                    has_alpha_finding = (alpha_view is not None and
                                        alpha_view.get("opportunity_strength") in ("MODERATE", "STRONG"))
                    if has_supervisor_finding or has_alpha_finding:
                        self.telegram_notifier.send_prolonged_wait_alert(
                            symbol=symbol,
                            agent_type=agent_type,
                            supervisor_view=supervisor_view,
                            alpha_view=alpha_view,
                            consecutive_waits=self.PROLONGED_WAIT_THRESHOLD,
                            underlying_price=json_data.get("underlying_price") if json_data else None,
                        )

        except Exception as e:
            logger.error(
                "Position monitor FAILED for %s strike=%s exp=%s:\n%s",
                symbol, strike, expiration, traceback.format_exc(),
            )
            print(f"Error monitoring {symbol} ${strike} exp {expiration}: {e}")
            cosmos.write_activity(
                symbol=symbol,
                agent_type=agent_type,
                activity_data={
                    "error": str(e),
                    "symbol": symbol,
                    "exchange": exchange,
                    "current_strike": strike,
                    "current_expiration": expiration,
                    "position_id": position_id,
                    "timestamp": analysis_ts,
                    "is_alert": False,
                },
                timestamp=analysis_ts,
            )

        # ── Telemetry (best-effort, never blocks) ─────────────────
        try:
            total_duration = round(time.time() - run_start, 2)
            fetch_stats = getattr(fetcher, "last_fetch_stats", {})
            for resource, stats in fetch_stats.items():
                cosmos.write_telemetry("tv_fetch", {
                    "symbol": symbol,
                    "resource": resource,
                    "duration_seconds": stats["duration"],
                    "response_size_chars": stats["size"],
                    "error": stats.get("error", False),
                })
            cosmos.write_telemetry("agent_run", {
                "symbol": symbol,
                "agent_type": agent_type,
                "duration_seconds": total_duration,
                "two_phase": True,
            })
        except Exception:
            logger.debug("Telemetry write skipped for %s", symbol)

    async def run_summary_agent(
        self,
        cosmos: CosmosDBService,
        telegram_notifier,
        activity_count: int = 3,
        model: str = None,
    ):
        """Generate and send daily portfolio summary via Telegram.
        
        Args:
            cosmos: CosmosDBService instance
            telegram_notifier: TelegramNotifier instance
            activity_count: Number of recent activities per symbol (default: 3)
        """
        from .summary_instructions import TV_SUMMARY_INSTRUCTIONS
        
        logger.info("="*70)
        logger.info("Summary Agent - Starting execution")
        logger.info("  Activity count per symbol: %d", activity_count)
        
        # Gate check: skip if Telegram is not enabled
        if telegram_notifier is None:
            logger.info("Summary agent skipped — Telegram notifier not configured")
            print("⏭️  Summary agent skipped — Telegram notifier not configured")
            return
        
        # Check if Telegram is actually enabled via credentials
        creds = telegram_notifier._get_credentials()
        if creds is None:
            logger.info("Summary agent skipped — Telegram notifications disabled")
            print("⏭️  Summary agent skipped — Telegram notifications disabled")
            return
        
        logger.info("Telegram notifier configured - proceeding with summary")
        print("\n" + "="*70)
        print("📊 DAILY PORTFOLIO SUMMARY AGENT")
        print("="*70)
        
        try:
            import json

            # ── 1. Load all symbol configs for portfolio context ──
            all_symbols = cosmos.list_symbols()
            logger.info("Loaded %d symbol configs", len(all_symbols))

            # Build closed position IDs and portfolio structure
            closed_position_ids: set = set()
            portfolio = {
                "active_calls": [],
                "active_puts": [],
                "watching_calls": [],
                "watching_puts": [],
            }

            for sym_doc in all_symbols:
                sym = sym_doc.get("symbol", "?")
                wl = sym_doc.get("watchlist", {})
                positions = sym_doc.get("positions", [])

                # Track closed position IDs for activity filtering
                for p in positions:
                    if p.get("status") != "active":
                        pid = p.get("position_id")
                        if pid:
                            closed_position_ids.add(pid)

                # Active positions (only active, never closed)
                active_calls = [p for p in positions
                                if p.get("status") == "active"
                                and p.get("type") == "call"]
                active_puts = [p for p in positions
                               if p.get("status") == "active"
                               and p.get("type") == "put"]

                if active_calls:
                    portfolio["active_calls"].append({
                        "symbol": sym,
                        "positions": [
                            {"strike": p.get("strike"),
                             "expiration": p.get("expiration"),
                             "position_id": p.get("position_id")}
                            for p in active_calls
                        ],
                    })
                if active_puts:
                    portfolio["active_puts"].append({
                        "symbol": sym,
                        "positions": [
                            {"strike": p.get("strike"),
                             "expiration": p.get("expiration"),
                             "position_id": p.get("position_id")}
                            for p in active_puts
                        ],
                    })

                # Watchlist / following (only if enabled)
                if wl.get("covered_call"):
                    portfolio["watching_calls"].append(sym)
                if wl.get("cash_secured_put"):
                    portfolio["watching_puts"].append(sym)

            logger.info(
                "Portfolio: %d active-call symbols, %d active-put symbols, "
                "%d watching-calls, %d watching-puts, %d closed position IDs",
                len(portfolio["active_calls"]),
                len(portfolio["active_puts"]),
                len(portfolio["watching_calls"]),
                len(portfolio["watching_puts"]),
                len(closed_position_ids),
            )

            # ── 2. Fetch recent activities and filter closed positions ──
            logger.info("Fetching recent activities from CosmosDB (limit=%d per symbol)", activity_count)
            activities_by_symbol = cosmos.get_recent_activities_by_symbol(
                limit_per_symbol=activity_count
            )

            # Filter out activities linked to closed positions
            if closed_position_ids:
                for sym, acts in list(activities_by_symbol.items()):
                    filtered = [a for a in acts
                                if a.get("position_id") not in closed_position_ids]
                    if filtered:
                        activities_by_symbol[sym] = filtered
                    else:
                        del activities_by_symbol[sym]

            has_activities = bool(activities_by_symbol)
            has_portfolio = (portfolio["active_calls"] or portfolio["active_puts"]
                            or portfolio["watching_calls"] or portfolio["watching_puts"])

            if not has_activities and not has_portfolio:
                logger.info("No activities or portfolio data — summary agent has nothing to report")
                print("ℹ️  No activities or portfolio data — nothing to summarize")
                return

            logger.info("Loaded activities for %d symbol(s)", len(activities_by_symbol))
            print(f"📋 Loaded activities for {len(activities_by_symbol)} symbol(s)")

            portfolio_text = json.dumps(portfolio, indent=2, default=str)
            activities_text = json.dumps(activities_by_symbol, indent=2, default=str) if has_activities else "{}"

            logger.info("Building prompt with portfolio (%d chars) + activities (%d chars)",
                        len(portfolio_text), len(activities_text))

            # ── 3. Build the prompt ──
            prompt = f"""{TV_SUMMARY_INSTRUCTIONS}

## PORTFOLIO OVERVIEW

The following shows ALL active (open) positions and ALL symbols on the watchlist (following).
Use this to determine which symbols belong in each section.
All positions are SOLD (short) options — sold calls and sold puts.

```json
{portfolio_text}
```

## RECENT ACTIVITIES DATA

The following is a dictionary of recent activities grouped by symbol (newest first).
Only includes activities for active (open) positions — closed positions are excluded.

```json
{activities_text}
```

Generate your 3-line summaries now. Output plain text only — no JSON, no code blocks.
Every symbol listed in the portfolio overview MUST appear in the corresponding section, even if there are no recent activities for it.
"""
            
            # Run the agent
            agent = Agent(name="SummaryAgent", client=self._get_client(model))
            print("🤖 Running summary agent...")
            logger.info("Invoking Agent with %d symbols", len(activities_by_symbol))
            
            run_start = time.time()
            response = await agent.run(prompt)
            run_duration = round(time.time() - run_start, 2)
            
            logger.info("Agent response received in %.2fs", run_duration)
            
            # Extract the summary text
            summary_text = response.text.strip()
            
            if not summary_text:
                logger.warning("Summary agent returned empty response")
                print("⚠️  Summary agent returned empty response")
                return
            
            logger.info("Summary text extracted (%d chars)", len(summary_text))
            print(f"✅ Summary generated ({run_duration}s)")
            print("\n" + "-"*70)
            print(summary_text)
            print("-"*70 + "\n")
            
            # Send to Telegram
            print("📤 Sending summary to Telegram...")
            logger.info("Preparing Telegram message...")
            header = "📊 <b>Daily Portfolio Summary</b>\n\n"
            telegram_message = header + "<pre>" + summary_text + "</pre>"
            
            logger.info("Sending message to Telegram (length=%d chars)", len(telegram_message))
            success = telegram_notifier.send_message(telegram_message)
            
            if success:
                logger.info("Summary sent to Telegram successfully")
                print("✅ Summary sent to Telegram")
            else:
                logger.warning("Failed to send summary to Telegram")
                print("❌ Failed to send summary to Telegram")
            
            # Telemetry (best-effort)
            try:
                logger.debug("Writing telemetry data to CosmosDB")
                cosmos.write_telemetry("agent_run", {
                    "symbol": "ALL",
                    "agent_type": "summary",
                    "duration_seconds": run_duration,
                    "symbols_count": len(activities_by_symbol),
                })
                logger.debug("Telemetry written successfully")
            except Exception as telem_err:
                logger.debug("Telemetry write skipped for summary agent: %s", str(telem_err))
        
        except Exception as e:
            logger.error("Summary agent failed: %s", str(e), exc_info=True)
            print(f"❌ Summary agent failed: {str(e)}")
        
        logger.info("Summary Agent - Completed execution")
        logger.info("="*70)
        print("="*70 + "\n")

    async def run_report_agent(
        self,
        symbol: str,
        exchange: str,
        context_text: str,
        cosmos: CosmosDBService,
        cached_resources: list | None = None,
        model: str = None,
    ) -> str:
        """Generate a comprehensive position/situation report for a symbol.

        Uses Agent to produce a structured markdown report from
        pre-gathered context (market data + CosmosDB activities).

        Args:
            symbol: Ticker symbol (e.g. "AAPL")
            exchange: Exchange code (e.g. "NASDAQ")
            context_text: Pre-built context string with all data
            cosmos: CosmosDBService for storing the report
            cached_resources: Resources served from cache

        Returns:
            The generated markdown report text.
        """
        from .report_instructions import TV_REPORT_INSTRUCTIONS

        analysis_ts = datetime.now().strftime(TIMESTAMP_FORMAT)

        logger.info("Starting report agent for %s", symbol)
        print(f"\n--- Generating report for {symbol} ---")

        run_start = time.time()

        message = f"""Generate a comprehensive situation report for {symbol} (exchange: {exchange}).

=== AVAILABLE DATA ===

{context_text}

=== END OF DATA ===

Current timestamp: {analysis_ts}
All market data has been pre-fetched above. Do NOT use any browser tools — analyze the data provided and generate your report."""

        agent = Agent(
            client=self._get_client(model),
            name="ReportAgent",
            instructions=TV_REPORT_INSTRUCTIONS,
        )
        result = await agent.run(message)
        report_text = result.text or str(result)

        run_duration = round(time.time() - run_start, 2)
        logger.info("Report agent completed for %s in %.2fs (%d chars)",
                     symbol, run_duration, len(report_text))
        print(f"✅ Report generated ({run_duration}s, {len(report_text)} chars)")

        # Persist report to CosmosDB
        cosmos.write_report(
            symbol=symbol,
            report_markdown=report_text,
            cached_resources=cached_resources or [],
            timestamp=analysis_ts,
        )

        # Telemetry (best-effort)
        try:
            cosmos.write_telemetry("agent_run", {
                "symbol": symbol,
                "agent_type": "report",
                "duration_seconds": run_duration,
            })
        except Exception:
            logger.debug("Telemetry write skipped for report agent")

        return report_text

    # ------------------------------------------------------------------
    # Technical Analysis Agent
    # ------------------------------------------------------------------

    async def run_technical_analysis_agent(
        self,
        symbol: str,
        exchange: str,
        context_text: str,
        cosmos: CosmosDBService,
        cached_resources: list | None = None,
        model: str = None,
    ) -> str:
        """Generate a detailed technical analysis for a symbol.

        Uses Agent to produce a structured markdown analysis from
        pre-gathered market data (technicals, overview, forecast, dividends).

        Args:
            symbol: Ticker symbol (e.g. "AAPL")
            exchange: Exchange code (e.g. "NASDAQ")
            context_text: Pre-built context string with all data
            cosmos: CosmosDBService for storing the analysis
            cached_resources: Resources served from cache

        Returns:
            The generated markdown analysis text.
        """
        from .technical_analysis_instructions import TECHNICAL_ANALYSIS_INSTRUCTIONS

        analysis_ts = datetime.now().strftime(TIMESTAMP_FORMAT)

        logger.info("Starting technical analysis agent for %s", symbol)
        print(f"\n--- Generating technical analysis for {symbol} ---")

        run_start = time.time()

        message = f"""Generate a comprehensive technical analysis for {symbol} (exchange: {exchange}).

=== AVAILABLE DATA ===

{context_text}

=== END OF DATA ===

Current timestamp: {analysis_ts}
All market data has been pre-fetched above. Please analyze this data and generate your analysis."""

        agent = Agent(
            client=self._get_client(model),
            name="TechnicalAnalysisAgent",
            instructions=TECHNICAL_ANALYSIS_INSTRUCTIONS,
        )
        result = await agent.run(message)
        analysis_text = result.text or str(result)

        run_duration = round(time.time() - run_start, 2)
        logger.info("Technical analysis agent completed for %s in %.2fs (%d chars)",
                     symbol, run_duration, len(analysis_text))
        print(f"✅ Technical analysis generated ({run_duration}s, {len(analysis_text)} chars)")

        # Persist to CosmosDB
        cosmos.write_technical_analysis(
            symbol=symbol,
            analysis_markdown=analysis_text,
            cached_resources=cached_resources or [],
            timestamp=analysis_ts,
        )

        # Telemetry (best-effort)
        try:
            cosmos.write_telemetry("agent_run", {
                "symbol": symbol,
                "agent_type": "technical_analysis",
                "duration_seconds": run_duration,
            })
        except Exception:
            logger.debug("Telemetry write skipped for technical analysis agent")

        return analysis_text
