from __future__ import annotations

import json
import re
from collections.abc import Callable
from copy import deepcopy
from hashlib import sha256
from typing import Any

from src.portfolio.rights_policy import (
    RIGHTS_UNSUPPORTED_MESSAGE,
    contains_legacy_rights_data,
    sanitize_legacy_movement,
)

from .canonical import COSMOS_SYSTEM_KEYS, normalize
from .models import SECTION_NAMES

SECRET_KEY_RE = re.compile(
    r"(?:^|[_-])(token|secret|password|passwd|api[_-]?key|connection[_-]?string|"
    r"credential|private[_-]?key|client[_-]?secret|chat[_-]?id)(?:$|[_-])",
    re.IGNORECASE,
)
SECRET_VALUE_RE = re.compile(
    r"(?:AccountKey=|SharedAccessSignature=|-----BEGIN [A-Z ]+PRIVATE KEY-----|"
    r"https://[^/\s]+\.documents\.azure\.com(?::\d+)?/|"
    r"(?:Bearer|Basic)\s+[A-Za-z0-9._~+/=-]{8,})",
    re.IGNORECASE,
)
JWT_RE = re.compile(r"^[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}$")
OPAQUE_RE = re.compile(r"^[A-Za-z0-9_+/=-]{32,}$")
IDENTIFIER_KEYS = frozenset({
    "id", "account_id", "security_id", "position_id", "session_id", "batch_id",
    "ca_group_id", "transfer_group_id", "transfer_pair_id", "movement_id",
    "corrects_movement_id", "superseded_by", "paired_movement_id",
    "option_position_id", "isin", "cusip", "sedol", "idempotency_hash",
})
OPTION_POSITION_OPAQUE_IDENTIFIER_PATHS = frozenset({"$.source.activity_id"})

RUNTIME_FIELDS = COSMOS_SYSTEM_KEYS | {
    "_active_positions", "_auto_enrolled", "_auto_enrolled_at",
    "_auto_enrolled_source",
    "last_run", "last_runs", "enrichment", "price", "pricing",
    "pricing_cache", "total_shares", "portfolio_shares", "computed", "cache",
}

ACCOUNT_FIELDS = frozenset({
    "id", "account_id", "doc_type", "broker", "name", "currency",
    "base_currency", "description", "status", "lifecycle_status",
    "deleted_at", "created_at", "updated_at",
})
SECURITY_FIELDS = frozenset({
    "id", "symbol", "doc_type", "security_id", "legacy_symbol", "ticker",
    "company_name", "display_name", "name", "exchange_mic", "mic",
    "asset_class", "listing_currency", "currency", "status", "aliases",
    "isin", "cusip", "sedol", "broker_ids", "provider_symbols",
    "created_by_migration", "migrated_from", "migration_note",
    "created_at", "updated_at", "deleted_at",
})
SYMBOL_CONFIG_FIELDS = frozenset({
    "id", "symbol", "doc_type", "security_id", "legacy_symbol", "ticker",
    "display_name", "name", "watchlist", "is_watchlisted", "watchlist_pause",
    "exchange", "telegram_enabled", "telegram_notifications_enabled",
    "telegram", "agents", "agent_settings",
    "notifications", "enabled_agents", "paused", "status", "notes",
    "created_at", "updated_at", "deleted_at",
})
POSITION_FIELDS = frozenset({
    "security_id", "symbol", "position_id", "type", "option_type", "strike",
    "expiration", "expiry", "contracts", "quantity", "open_contracts",
    "contracts_open", "status", "opened_at",
    "closed_at", "close_reason", "rolled_from", "rolled_to", "notes",
    "source", "closing_source", "premium", "buyback_cost", "position_kind",
    "is_paper", "position_schema_version", "created_at", "updated_at",
})
ACTION_PLAN_FIELDS = frozenset({
    "id", "doc_type", "symbol", "security_id", "title", "objective",
    "plan_type", "type", "status", "priority", "conditions", "notes",
    "created_at", "updated_at", "deleted_at",
})
LEDGER_FIELDS = frozenset({
    "id", "account_id", "doc_type", "txn_type", "security_id", "ticker",
    "symbol", "trade_date", "settlement_date", "quantity", "gross", "fees",
    "net", "withholding", "fx", "cost_basis_status",
    "import_source", "batch_id", "session_id", "idempotency_hash",
    "source_row_index", "source_row", "correction_status", "corrects_movement_id",
    "superseded_by", "reassigned_from", "reassigned_to", "transfer_group_id",
    "transfer_pair_id", "paired_movement_id", "source_account_id",
    "destination_account_id", "ca_group_id", "ca_leg_type", "ca_event_type",
    "ca_group_seq", "replacement_group_id", "replaces_ca_group_id",
    "replaced_by_ca_group_id", "option_position_id", "option_link_kind",
    "option_type", "option_strike", "option_expiration", "option_symbol",
    "option_close_date", "notes", "created_at", "updated_at", "deleted_at",
    "original_account_id", "reassignment_reason", "transfer_reason",
    "transfer_source_account_id", "transfer_dest_account_id",
    "transfer_cost_basis_derived_eur", "transfer_cost_basis_eur",
    "transfer_cost_basis_overridden", "transfer_fee", "transfer_peer_id",
    "correction_note", "void_reason", "superseded_by_ca_group_id",
    "company_name", "warnings",
    "_repair_buy_fields_v1", "_repair_buy_fields_v2",
})
APP_SETTING_PATHS = frozenset({
    "scheduler", "summary_agent", "plan_monitor", "dps_scorer",
    "options_chain_scheduler", "portfolio_enrichment", "symbol_pricing",
    "price_forecast", "best_options_scheduler", "dgi_screener",
    "calendar_sync", "agent_trace", "ai_function_overrides", "context",
    "preferences", "telegram",
})
APP_SETTING_NESTED_ALLOW = {
    "telegram": frozenset({"enabled"}),
    "agent_trace": frozenset({"enabled_types"}),
}

FIELDS = {
    "accounts": ACCOUNT_FIELDS,
    "securities": SECURITY_FIELDS,
    "symbol_configs": SYMBOL_CONFIG_FIELDS,
    "option_positions": POSITION_FIELDS,
    "ledger_movements": LEDGER_FIELDS,
    "action_plans": ACTION_PLAN_FIELDS,
    "app_settings": frozenset({"path", "value"}),
}


class SchemaError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        section: str | None = None,
        logical_identity: str | None = None,
        issue: str | None = None,
        fields: list[str] | set[str] | tuple[str, ...] = (),
    ) -> None:
        super().__init__(message)
        self.section = section
        self.identity_hash = (
            sha256(str(logical_identity).encode("utf-8")).hexdigest()[:12]
            if logical_identity is not None else None
        )
        self.issue = issue
        self.fields = tuple(sorted({_sanitize_field_name(field) for field in fields}))

    def safe_detail(self) -> str:
        parts = []
        if self.section:
            parts.append(f"section={self.section}")
        if self.identity_hash:
            parts.append(f"identity_hash={self.identity_hash}")
        if self.issue:
            parts.append(f"issue={self.issue}")
        if self.fields:
            parts.append(f"fields={list(self.fields)!r}")
        return (
            f"SchemaError: {' '.join(parts)}"
            if parts else "SchemaError: backup operation failed"
        )


def _sanitize_field_name(field: str) -> str:
    value = str(field)
    if len(value) <= 100 and re.fullmatch(r"[A-Za-z0-9_$.\[\]<>-]+", value):
        return value
    return f"<field-hash:{sha256(value.encode('utf-8')).hexdigest()[:12]}>"


def scan_for_secrets(
    value: Any,
    path: str = "$",
    *,
    allowed_opaque_identifier_paths: frozenset[str] = frozenset(),
) -> list[str]:
    findings: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{path}.{key}"
            if SECRET_KEY_RE.search(str(key)):
                findings.append(child)
            findings.extend(scan_for_secrets(
                item,
                child,
                allowed_opaque_identifier_paths=allowed_opaque_identifier_paths,
            ))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            findings.extend(scan_for_secrets(
                item,
                f"{path}[{index}]",
                allowed_opaque_identifier_paths=allowed_opaque_identifier_paths,
            ))
    elif isinstance(value, str):
        if SECRET_VALUE_RE.search(value):
            findings.append(path)
        stripped = value.strip()
        if stripped.startswith(("{", "[")):
            if len(stripped) > 1_000_000:
                findings.append(f"{path}<unscannable-json>")
            else:
                try:
                    findings.extend(scan_for_secrets(
                        json.loads(stripped),
                        f"{path}<json>",
                        allowed_opaque_identifier_paths=allowed_opaque_identifier_paths,
                    ))
                except (json.JSONDecodeError, TypeError):
                    findings.append(f"{path}<malformed-json>")
        leaf = re.split(r"[.\[]", path.rstrip("]"))[-1].casefold()
        is_jwt = JWT_RE.fullmatch(stripped)
        is_opaque = (
            OPAQUE_RE.fullmatch(stripped)
            and any(char.islower() for char in stripped)
            and any(char.isupper() for char in stripped)
            and any(char.isdigit() for char in stripped)
        )
        if leaf not in IDENTIFIER_KEYS and (
            is_jwt
            or (
                is_opaque
                and path not in allowed_opaque_identifier_paths
            )
        ):
            findings.append(f"{path}<opaque-token>")
    return findings


def _allowed_opaque_identifier_paths(section: str) -> frozenset[str]:
    if section == "option_positions":
        return OPTION_POSITION_OPAQUE_IDENTIFIER_PATHS
    return frozenset()


def _project(
    doc: dict[str, Any],
    allowed: frozenset[str],
    *,
    section: str,
    logical_identity: str | None = None,
) -> dict[str, Any]:
    unknown = set(doc) - allowed - RUNTIME_FIELDS
    if unknown:
        raise SchemaError(
            f"{section} contains unknown fields: {sorted(unknown)}",
            section=section,
            logical_identity=logical_identity,
            issue="unknown_fields",
            fields=unknown,
        )
    projected = {key: deepcopy(value) for key, value in doc.items() if key in allowed}
    findings = scan_for_secrets(
        projected,
        allowed_opaque_identifier_paths=_allowed_opaque_identifier_paths(section),
    )
    if findings:
        raise SchemaError(
            f"{section} contains secret-like data at {findings[:5]}",
            section=section,
            logical_identity=logical_identity,
            issue="secret_like_data",
            fields=findings[:5],
        )
    return normalize(projected)


def project_account(doc: dict[str, Any]) -> dict[str, Any]:
    identity = doc.get("account_id") or doc.get("id")
    result = _project(
        doc, ACCOUNT_FIELDS, section="accounts", logical_identity=identity
    )
    if result.get("doc_type") != "account" or not result.get("account_id"):
        raise SchemaError(
            "Invalid account identity",
            section="accounts",
            logical_identity=identity,
            issue="invalid_identity",
            fields=("doc_type", "account_id"),
        )
    return result


def project_security(doc: dict[str, Any]) -> dict[str, Any]:
    identity = doc.get("security_id") or doc.get("id")
    result = _project(
        doc, SECURITY_FIELDS, section="securities", logical_identity=identity
    )
    if result.get("doc_type") != "security_master" or not result.get("security_id"):
        raise SchemaError(
            "Invalid security identity",
            section="securities",
            logical_identity=identity,
            issue="invalid_identity",
            fields=("doc_type", "security_id"),
        )
    return result


def project_symbol_config(doc: dict[str, Any]) -> dict[str, Any]:
    source = dict(doc)
    source.pop("positions", None)
    identity = doc.get("security_id") or doc.get("symbol") or doc.get("id")
    result = _project(
        source,
        SYMBOL_CONFIG_FIELDS,
        section="symbol_configs",
        logical_identity=identity,
    )
    if result.get("doc_type") != "symbol_config" or not (
        result.get("security_id") or result.get("symbol")
    ):
        raise SchemaError(
            "Invalid symbol config identity",
            section="symbol_configs",
            logical_identity=identity,
            issue="invalid_identity",
            fields=("doc_type", "security_id", "symbol"),
        )
    return result


def project_positions(doc: dict[str, Any], include_paper: bool) -> list[dict[str, Any]]:
    records = []
    for position in doc.get("positions") or []:
        is_paper = bool(position.get("is_paper"))
        if is_paper and not include_paper:
            continue
        projected = {
            "security_id": doc.get("security_id"),
            "symbol": doc.get("symbol"),
            **deepcopy(position),
            "position_kind": "paper" if is_paper else "real",
        }
        projected.pop("is_paper", None)
        identity = (
            f"{doc.get('security_id') or doc.get('symbol')}|"
            f"{position.get('position_id')}"
        )
        records.append(_project(
            projected,
            POSITION_FIELDS,
            section="option_positions",
            logical_identity=identity,
        ))
    return records


def project_ledger(doc: dict[str, Any], include_source_row: bool) -> dict[str, Any]:
    identity = f"{doc.get('account_id')}|{doc.get('id')}"
    if contains_legacy_rights_data(doc):
        raise SchemaError(
            RIGHTS_UNSUPPORTED_MESSAGE,
            section="ledger_movements",
            logical_identity=identity,
            issue="unsupported_rights_movement",
        )
    source = sanitize_legacy_movement(doc)
    if source is None:  # Defensive: the strict check above already rejects this.
        raise SchemaError(
            RIGHTS_UNSUPPORTED_MESSAGE,
            section="ledger_movements",
            logical_identity=identity,
            issue="unsupported_rights_movement",
        )
    if not include_source_row:
        source.pop("source_row", None)
    result = _project(
        source,
        LEDGER_FIELDS,
        section="ledger_movements",
        logical_identity=identity,
    )
    if result.get("doc_type") != "ledger_txn" or not result.get("account_id") or not result.get("id"):
        raise SchemaError(
            "Invalid ledger identity",
            section="ledger_movements",
            logical_identity=identity,
            issue="invalid_identity",
            fields=("doc_type", "account_id", "id"),
        )
    return result


def project_action_plan(doc: dict[str, Any]) -> dict[str, Any]:
    source = dict(doc)
    source.pop("agent_notes", None)
    identity = (
        f"{doc.get('security_id') or doc.get('symbol')}|{doc.get('id')}"
    )
    result = _project(
        source,
        ACTION_PLAN_FIELDS,
        section="action_plans",
        logical_identity=identity,
    )
    if result.get("doc_type") != "action_plan" or not result.get("id"):
        raise SchemaError(
            "Invalid action plan identity",
            section="action_plans",
            logical_identity=identity,
            issue="invalid_identity",
            fields=("doc_type", "id"),
        )
    return result


def project_settings(settings: dict[str, Any]) -> list[dict[str, Any]]:
    records = []
    for path in sorted(APP_SETTING_PATHS):
        if path not in settings:
            continue
        value = deepcopy(settings[path])
        nested = APP_SETTING_NESTED_ALLOW.get(path)
        if nested is not None:
            if not isinstance(value, dict):
                raise SchemaError(
                    f"Setting {path} must be an object",
                    section="app_settings",
                    logical_identity=path,
                    issue="invalid_shape",
                    fields=(path,),
                )
            value = {key: value[key] for key in nested if key in value}
        findings = scan_for_secrets(value, f"$.{path}")
        if findings:
            raise SchemaError(
                f"Setting {path} contains secret-like data at {findings[:5]}",
                section="app_settings",
                logical_identity=path,
                issue="secret_like_data",
                fields=findings[:5],
            )
        records.append({"path": path, "value": normalize(value)})
    return records


PROJECTORS: dict[str, Callable[..., Any]] = {
    "accounts": project_account,
    "securities": project_security,
    "symbol_configs": project_symbol_config,
    "ledger_movements": project_ledger,
    "action_plans": project_action_plan,
}


def logical_key(section: str, record: dict[str, Any]) -> str:
    if section == "accounts":
        return str(record["account_id"])
    if section in {"securities", "symbol_configs"}:
        return str(record.get("security_id") or record.get("symbol"))
    if section == "option_positions":
        return f"{record.get('security_id') or record.get('symbol')}|{record['position_id']}"
    if section == "ledger_movements":
        return f"{record['account_id']}|{record['id']}"
    if section == "action_plans":
        return f"{record.get('security_id') or record.get('symbol')}|{record['id']}"
    if section == "app_settings":
        return str(record["path"])
    raise SchemaError(f"Unknown section {section}")


def partition_key(section: str, record: dict[str, Any]) -> str:
    if section in {"accounts", "ledger_movements"}:
        return str(record["account_id"])
    if section in {"securities", "symbol_configs", "option_positions", "action_plans"}:
        return str(record.get("symbol") or str(record.get("security_id", "")).split(":")[-1])
    if section == "app_settings":
        return "app-config"
    raise SchemaError(f"Unknown section {section}")


def validate_record(section: str, record: dict[str, Any]) -> None:
    if section not in SECTION_NAMES:
        raise SchemaError(f"Unknown section {section}")
    if not isinstance(record, dict):
        raise SchemaError(f"{section} record must be an object")
    if section == "ledger_movements" and contains_legacy_rights_data(record):
        raise SchemaError(
            RIGHTS_UNSUPPORTED_MESSAGE,
            section=section,
            logical_identity=f"{record.get('account_id')}|{record.get('id')}",
            issue="unsupported_rights_movement",
        )
    unknown = set(record) - FIELDS[section]
    if unknown:
        raise SchemaError(f"{section} contains unknown fields: {sorted(unknown)}")
    logical_key(section, record)
    findings = scan_for_secrets(
        record,
        allowed_opaque_identifier_paths=_allowed_opaque_identifier_paths(section),
    )
    if findings:
        raise SchemaError(f"{section} contains secret-like data at {findings[:5]}")
