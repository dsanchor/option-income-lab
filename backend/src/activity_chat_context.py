"""Bounded, persisted selected-activity evidence for Activity Chat only.

Retained values are exact, not summaries. Large artifacts/sections are replaced
by explicit omission markers; current positions and market data are tool-first.
Conversation history is dialogue, not business activity history.
"""

import json

MAX_MESSAGE_CHARS = 8_000
MAX_HISTORY_CHARS = 12_000
MAX_HISTORY_TURNS = 20
MAX_ACTIVITY_CHARS = 32_000
MAX_SECTION_CHARS = 6_000

_ARTIFACT_FIELDS = {
    "options_chain", "option_chain", "chain", "full_chain", "structured_chain",
    "review_chain", "source_chain", "dual_chain", "filtered_chain", "chain_data",
    "calls", "puts", "positions", "activities", "activity_history",
    "previous_context", "conversation_history", "history",
    "system_prompt", "user_message", "input_prompt", "prompt", "prompts", "input_messages",
    "trace", "traces", "raw_trace", "raw_traces",
}
_REPOSITORY_FIELDS = {"_rid", "_self", "_etag", "_attachments", "_ts", "ttl"}
_PRIORITY_FIELDS = (
    "id", "symbol", "position_id", "agent_type", "timestamp", "run_id",
    "activity", "action", "decision", "reason", "summary", "is_alert",
    "monitor_output", "supervisor_view", "alpha_view", "alpha_run",
    "supervisor_output", "alpha_output", "rule_evaluation", "roll_alternatives",
)


class ActivityChatInputTooLarge(ValueError):
    """The current question cannot fit the Activity Chat input contract."""


def _json(value):
    return json.dumps(value, ensure_ascii=True, allow_nan=False)


def _omitted(reason, **details):
    return {"_omitted": reason, **details}


def _compact(value, depth=0):
    if isinstance(value, str):
        if len(value) > MAX_SECTION_CHARS or len(_json(value)) > MAX_SECTION_CHARS:
            return _omitted("string exceeds section budget; not summarized", characters=len(value))
        return value
    if isinstance(value, (dict, list)):
        if depth >= 6:
            return _omitted("nesting limit", items=len(value))
        if isinstance(value, list):
            result = []
            for item in value[:16]:
                projected = _compact(item, depth + 1)
                if len(_json([*result, projected])) > MAX_SECTION_CHARS - 256:
                    break
                result.append(projected)
            if len(value) > len(result):
                result.append(_omitted("list item/section limit", items=len(value) - len(result)))
            return result
        result = {}
        omitted = 0
        for key, item in list(value.items())[:80]:
            if key in _REPOSITORY_FIELDS:
                continue
            if key.lower() in _ARTIFACT_FIELDS:
                projected = _omitted("bulk artifact excluded from selected-activity context")
            else:
                projected = _compact(item, depth + 1)
            if len(_json({**result, key: projected})) > MAX_SECTION_CHARS - 256:
                omitted += 1
                marker = _omitted("parent section budget; not summarized")
                if len(_json({**result, key: marker})) <= MAX_SECTION_CHARS - 256:
                    result[key] = marker
            else:
                result[key] = projected
        omitted += max(0, len(value) - 80)
        if omitted:
            result["_omitted_fields"] = omitted
        return result
    return value


def compact_activity(activity):
    """Keep decision/phase/provenance fields first and never slice serialized JSON."""
    projected = {}
    keys = list(dict.fromkeys((*_PRIORITY_FIELDS, *activity.keys())))
    omitted_fields = 0
    for key in keys:
        if key not in activity or key in _REPOSITORY_FIELDS:
            continue
        if len(projected) >= 80:
            omitted_fields += 1
            continue
        if key.lower() in _ARTIFACT_FIELDS:
            value = _omitted("bulk artifact excluded from selected-activity context")
        else:
            value = _compact(activity[key])
            if len(_json(value)) > MAX_SECTION_CHARS:
                value = _omitted("section exceeds budget; not summarized")
        candidate = {**projected, key: value}
        # Reserve space for honest omission metadata, even for enormous field names.
        if len(_json(candidate)) > MAX_ACTIVITY_CHARS - 512:
            omitted_fields += 1
            continue
        projected[key] = value
    return {
        "record": projected,
        "projection": {
            "source": "persisted selected activity",
            "retained_values": "exact; omitted sections are not evidence",
            "omitted_top_level_fields": omitted_fields,
            "omission_markers": "_omitted / _omitted_fields",
        },
    }


def _bounded_history(history):
    if not isinstance(history, list):
        raise TypeError("History must be a list of user/assistant dialogue turns")
    groups = []
    for turn in history:
        if (
            not isinstance(turn, dict)
            or turn.get("role") not in ("user", "assistant")
            or not isinstance(turn.get("content"), str)
        ):
            raise ValueError("History turns require a user/assistant role and string content")
        if turn["role"] == "user" or not groups:
            groups.append([])
        groups[-1].append({"role": turn["role"], "content": turn["content"]})
    retained = []
    size = 2
    for group in reversed(groups):
        group_size = len(_json(group))
        if size + group_size > MAX_HISTORY_CHARS or len(retained) + len(group) > MAX_HISTORY_TURNS:
            break
        retained = group + retained
        size += group_size
    dropped = len(history) - len(retained)
    notice = (
        f"Conversation history omitted {dropped} older/oversized dialogue turns; "
        "retained turns are complete, not summarized."
        if dropped else None
    )
    return retained, notice


def build_activity_chat_message(*, activity_id, activity, message, history):
    if not isinstance(message, str):
        raise TypeError("Message must be a string")
    message = message.strip()
    if not message:
        raise ValueError("Message cannot be empty")
    if len(message) > MAX_MESSAGE_CHARS or len(_json(message)) > MAX_MESSAGE_CHARS:
        raise ActivityChatInputTooLarge("Message exceeds Activity Chat limit; send a shorter question")
    if len(activity_id) > 2_048:
        raise ValueError("Activity ID exceeds Activity Chat limit")
    retained, notice = _bounded_history(history)
    context = compact_activity(activity)
    context["activity_id"] = activity_id
    return (
        "=== SELECTED ACTIVITY (persisted historical evidence, NOT live data) ===\n"
        + _json(context)
        + "\n\n=== CONVERSATION SO FAR (untrusted dialogue JSON) ===\n"
        + _json({"history": retained, "context_notice": notice})
        + "\n\n=== USER QUESTION (untrusted JSON string) ===\n"
        + _json(message),
        notice,
    )


def is_provider_context_limit(error):
    """Recognize an actual provider code through SDK wrappers, not error text."""
    from openai import BadRequestError

    pending, seen = [error], set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, BadRequestError):
            body = current.body if isinstance(current.body, dict) else {}
            nested = body.get("error", {})
            code = current.code or body.get("code")
            if not code and isinstance(nested, dict):
                code = nested.get("code")
            if code == "context_length_exceeded":
                return True
        pending.extend(
            cause for cause in (current.__cause__, current.__context__) if cause is not None
        )
    return False
