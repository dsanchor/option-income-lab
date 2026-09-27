#!/usr/bin/env python3
"""Backfill Dividend · Buy share FMV from Yahoo's unadjusted daily Open.

The default mode is a read-only audit. Apply requires the exact audit
fingerprint plus explicit confirmation; overwrites require a second,
independent confirmation. No gross/net/cost-basis field is read as valuation
evidence or modified.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, uuid5

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.portfolio.cosmos_securities import (
    security_id_to_doc_id,
    security_id_to_ticker,
)
from src.portfolio.fx_service import (
    FxRateNotFoundError,
    FxUnavailableError,
    get_historical_fx_rate,
)
from src.portfolio.provider_symbols import resolve_yfinance_symbol
from src.yfinance_fetcher import YFinanceFetcher

logger = logging.getLogger("backfill_dividend_buy_share_fmv")

SCRIPT_VERSION = "dividend-buy-fmv-v1"
APPLY_CONFIRMATION = "BACKFILL_DIVIDEND_BUY_FMV"
OVERWRITE_CONFIRMATION = "OVERWRITE_EXISTING_FMV"
DEFAULT_BACKUP_DIR = Path(__file__).parent / "migration_backups"
_SYSTEM_KEYS = frozenset({"_rid", "_self", "_etag", "_attachments", "_ts"})
_EVENT_TYPES = frozenset({"SCRIP_DIVIDEND", "DIVIDEND_WITH_SCRIP"})
_Q6 = Decimal("0.000001")
_Q9 = Decimal("0.000000001")
_OUTCOME_KEYS = (
    "updated",
    "already_set",
    "unresolved_symbol",
    "no_market_session",
    "invalid_open",
    "currency_mismatch",
    "fx_unavailable",
    "cas_conflict",
    "failed",
)


class BackfillError(RuntimeError):
    def __init__(self, message: str, exit_code: int = 1) -> None:
        super().__init__(message)
        self.exit_code = exit_code


@dataclass(frozen=True)
class TargetIdentity:
    endpoint: str
    database: str
    portfolio_container: str
    symbols_container: str

    def as_dict(self) -> dict[str, str]:
        return {
            "endpoint": self.endpoint,
            "database": self.database,
            "portfolio_container": self.portfolio_container,
            "symbols_container": self.symbols_container,
        }


def _clean(doc: dict) -> dict:
    return {key: value for key, value in doc.items() if key not in _SYSTEM_KEYS}


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _safe_endpoint(endpoint: str) -> str:
    parsed = urlsplit(endpoint)
    if not parsed.scheme or not parsed.hostname:
        raise BackfillError("COSMOSDB_ENDPOINT is invalid", 2)
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{parsed.hostname}{port}"


def _decimal(value: Any) -> Decimal | None:
    try:
        result = Decimal(str(value).strip())
    except (InvalidOperation, AttributeError, ValueError):
        return None
    return result if result.is_finite() else None


def _iso_date(value: Any) -> str | None:
    try:
        return date.fromisoformat(str(value)).isoformat()
    except (TypeError, ValueError):
        return None


def _eligible(doc: dict) -> bool:
    quantity = _decimal(doc.get("quantity"))
    return bool(
        doc.get("doc_type") == "ledger_txn"
        and doc.get("txn_type") == "BUY"
        and doc.get("ca_leg_type") == "SHARE_ACQUISITION"
        and doc.get("ca_event_type") in _EVENT_TYPES
        and doc.get("correction_status", "ACTIVE") == "ACTIVE"
        and not doc.get("deleted_at")
        and quantity is not None
        and quantity > 0
        and _iso_date(doc.get("trade_date"))
    )


def _matches_filters(doc: dict, filters: dict[str, Any]) -> bool:
    if filters.get("account_id") and doc.get("account_id") != filters["account_id"]:
        return False
    if filters.get("security_id") and doc.get("security_id") != filters["security_id"]:
        return False
    if filters.get("ca_group_id") and doc.get("ca_group_id") != filters["ca_group_id"]:
        return False
    trade_date = _iso_date(doc.get("trade_date"))
    if filters.get("date_from") and (not trade_date or trade_date < filters["date_from"]):
        return False
    return not (
        filters.get("date_to")
        and (not trade_date or trade_date > filters["date_to"])
    )


def _query_ledger(container) -> list[dict]:
    query = (
        "SELECT * FROM c WHERE c.doc_type='ledger_txn' AND c.txn_type='BUY' "
        "AND c.ca_leg_type='SHARE_ACQUISITION' "
        "AND (c.ca_event_type='SCRIP_DIVIDEND' "
        "OR c.ca_event_type='DIVIDEND_WITH_SCRIP')"
    )
    return list(container.query_items(query=query, enable_cross_partition_query=True))


def _read_security(symbols_container, security_id: str) -> dict | None:
    if not security_id:
        return None
    try:
        return symbols_container.read_item(
            item=security_id_to_doc_id(security_id),
            partition_key=security_id_to_ticker(security_id),
        )
    except Exception as exc:
        if exc.__class__.__name__ == "CosmosResourceNotFoundError":
            return None
        raise


def _normalise_observation(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {"status": "provider_error"}
    status = raw.get("status")
    if status:
        return raw
    if raw.get("open") is not None and raw.get("market_session_date"):
        return {**raw, "status": "ok"}
    return {"status": "provider_error"}


def _observe_open(fetcher: Any, symbol: str, requested_date: str) -> dict[str, Any]:
    method = getattr(fetcher, "get_daily_open", None) or getattr(
        fetcher, "get_historical_open", None
    )
    if method is None:
        raise BackfillError("Yahoo fetcher does not support directed daily Open", 2)
    try:
        return _normalise_observation(
            method(symbol, requested_date, max_calendar_days=7)
        )
    except TypeError:
        return _normalise_observation(method(symbol, requested_date, 7))
    except Exception:  # noqa: BLE001
        return {"status": "provider_error"}


def _proposal(
    doc: dict,
    security: dict,
    provider_symbol: str,
    observation: dict,
    fx_getter: Callable[..., tuple[str, str]],
) -> tuple[dict | None, str | None]:
    requested_date = _iso_date(doc.get("trade_date"))
    status = observation.get("status")
    if status == "no_market_session":
        return None, "no_market_session"
    if status == "invalid_open":
        return None, "invalid_open"
    if status == "currency_unavailable":
        return None, "currency_mismatch"
    if status != "ok":
        return None, "failed"

    session_date = _iso_date(observation.get("market_session_date"))
    if not session_date:
        return None, "no_market_session"
    delta = (date.fromisoformat(session_date) - date.fromisoformat(requested_date)).days
    if delta < 0 or delta > 7:
        return None, "no_market_session"

    listing_currency = str(security.get("listing_currency") or "").strip().upper()
    observed_currency = str(observation.get("currency") or "").strip().upper()
    if not listing_currency or observed_currency != listing_currency:
        return None, "currency_mismatch"

    price = _decimal(observation.get("open"))
    quantity = _decimal(doc.get("quantity"))
    if price is None or price <= 0 or quantity is None or quantity <= 0:
        return None, "invalid_open"

    try:
        if listing_currency == "EUR":
            fx_rate, fx_date, fx_source = (
                Decimal(1),
                requested_date,
                "IDENTITY",
            )
        else:
            try:
                raw_rate, fx_date = fx_getter(
                    listing_currency, "EUR", rate_date=requested_date
                )
            except TypeError:
                raw_rate, fx_date = fx_getter(
                    listing_currency, "EUR", requested_date
                )
            fx_rate = _decimal(raw_rate)
            fx_date = _iso_date(fx_date)
            fx_source = "ECB"
            if fx_rate is None or fx_rate <= 0 or not fx_date:
                return None, "fx_unavailable"
            fx_age = (date.fromisoformat(requested_date) - date.fromisoformat(fx_date)).days
            if fx_age < 0 or fx_age > 5:
                return None, "fx_unavailable"
    except (FxUnavailableError, FxRateNotFoundError, ValueError, TypeError):
        return None, "fx_unavailable"

    price_q = price.quantize(_Q6, rounding=ROUND_HALF_UP)
    amount_q = (price_q * quantity).quantize(_Q6, rounding=ROUND_HALF_UP)
    rate_q = fx_rate.quantize(_Q9, rounding=ROUND_HALF_UP)
    price_eur_q = (price_q * rate_q).quantize(_Q6, rounding=ROUND_HALF_UP)
    amount_eur_q = (amount_q * rate_q).quantize(_Q6, rounding=ROUND_HALF_UP)
    if min(price_q, amount_q, price_eur_q, amount_eur_q, rate_q) <= 0:
        return None, "invalid_open"

    share_fmv = {
        "valuation_date": requested_date,
        "amount": format(amount_q, "f"),
        "currency": listing_currency,
        "eur_amount": format(amount_eur_q, "f"),
        "price_per_share": format(price_q, "f"),
        "price_per_share_eur": format(price_eur_q, "f"),
        "source": "YAHOO_OPEN",
        "confidence": "MARKET_ESTIMATE",
        "fx": {
            "rate": format(rate_q, "f"),
            "date": fx_date,
            "source": fx_source,
        },
        "provenance": {
            "provider": "yfinance",
            "provider_symbol": provider_symbol,
            "price_field": "OPEN",
            "requested_date": requested_date,
            "market_session_date": session_date,
            "script_version": SCRIPT_VERSION,
        },
    }
    return share_fmv, None


def build_plan(
    portfolio_container,
    symbols_container,
    *,
    target: TargetIdentity,
    filters: dict[str, Any] | None = None,
    force: bool = False,
    limit: int | None = None,
    fetcher: Any | None = None,
    fx_getter: Callable[..., tuple[str, str]] = get_historical_fx_rate,
) -> dict:
    """Build a deterministic, read-only plan suitable for fingerprinting."""
    filters = {key: value for key, value in (filters or {}).items() if value}
    fetcher = fetcher or YFinanceFetcher()
    documents = sorted(
        (
            doc
            for doc in _query_ledger(portfolio_container)
            if _eligible(doc) and _matches_filters(doc, filters)
        ),
        key=lambda d: (str(d.get("account_id", "")), str(d.get("id", ""))),
    )
    if limit is not None:
        documents = documents[:limit]

    actions: list[dict] = []
    skips: list[dict] = []
    securities: dict[str, dict | None] = {}
    for doc in documents:
        if doc.get("share_fmv") is not None and not force:
            skips.append({"id": doc.get("id"), "reason": "already_set"})
            continue

        security_id = str(doc.get("security_id") or "")
        if security_id not in securities:
            securities[security_id] = _read_security(symbols_container, security_id)
        security = securities[security_id]
        if not security:
            skips.append({"id": doc.get("id"), "reason": "unresolved_symbol"})
            continue

        ticker = str(security.get("ticker") or security_id_to_ticker(security_id))
        mic = security.get("exchange_mic")
        provider_symbol = resolve_yfinance_symbol(ticker, mic, security)
        if not provider_symbol:
            skips.append({"id": doc.get("id"), "reason": "unresolved_symbol"})
            continue

        observation = _observe_open(fetcher, provider_symbol, doc["trade_date"])
        proposed, reason = _proposal(
            doc, security, provider_symbol, observation, fx_getter
        )
        if reason:
            skips.append({"id": doc.get("id"), "reason": reason})
            continue
        actions.append(
            {
                "id": doc["id"],
                "account_id": doc["account_id"],
                "_etag": doc.get("_etag", ""),
                "security_id": security_id,
                "ca_group_id": doc.get("ca_group_id"),
                "trade_date": doc["trade_date"],
                "previous_share_fmv": doc.get("share_fmv"),
                "share_fmv": proposed,
            }
        )

    counts = {key: 0 for key in _OUTCOME_KEYS}
    for skip in skips:
        counts[skip["reason"]] += 1
    fingerprint_payload = {
        "schema_version": 1,
        "script_version": SCRIPT_VERSION,
        "target": target.as_dict(),
        "filters": filters,
        "force": force,
        "limit": limit,
        "actions": actions,
        "skips": skips,
    }
    return {
        **fingerprint_payload,
        "counts": counts,
        "sha256": _sha256(fingerprint_payload),
    }


def _backup_checksum(payload: dict) -> str:
    return _sha256({key: value for key, value in payload.items() if key != "sha256"})


def write_backup(
    portfolio_container,
    plan: dict,
    backup_dir: Path = DEFAULT_BACKUP_DIR,
) -> tuple[Path, dict]:
    run_id = str(uuid5(NAMESPACE_URL, f"{SCRIPT_VERSION}:{plan['sha256']}"))
    documents = []
    for action in plan["actions"]:
        raw = portfolio_container.read_item(
            item=action["id"], partition_key=action["account_id"]
        )
        if raw.get("_etag", "") != action.get("_etag", ""):
            raise BackfillError("Plan changed before backup; no writes performed", 1)
        documents.append(
            {
                "id": raw["id"],
                "partition_key": raw["account_id"],
                "_etag": raw.get("_etag", ""),
                "body": _clean(raw),
                "proposed_share_fmv": action["share_fmv"],
            }
        )
    payload = {
        "schema_version": 1,
        "script_version": SCRIPT_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "plan_sha256": plan["sha256"],
        "target": plan["target"],
        "documents": documents,
    }
    payload["sha256"] = _backup_checksum(payload)
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = backup_dir / f"dividend_buy_fmv_{stamp}_{plan['sha256'][:12]}.json"
    try:
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    except Exception as exc:
        raise BackfillError("Backup could not be written; no writes performed", 2) from exc
    return path, payload


def _etag_replace(container, raw: dict, body: dict) -> bool:
    from azure.core import MatchConditions
    from azure.cosmos.exceptions import CosmosHttpResponseError

    try:
        container.replace_item(
            item=raw["id"],
            body=_clean(body),
            etag=raw.get("_etag", ""),
            match_condition=MatchConditions.IfNotModified,
        )
        return True
    except CosmosHttpResponseError as exc:
        if exc.status_code in (409, 412):
            return False
        raise


def apply_plan(
    portfolio_container,
    plan: dict,
    *,
    backup_dir: Path = DEFAULT_BACKUP_DIR,
    now: Callable[[], datetime] | None = None,
) -> tuple[dict, Path | None]:
    """Back up, CAS-apply and verify a previously validated plan."""
    results = dict(plan["counts"])
    if not plan["actions"]:
        return results, None
    backup_path, backup = write_backup(portfolio_container, plan, backup_dir)
    clock = now or (lambda: datetime.now(timezone.utc))
    for action in plan["actions"]:
        try:
            raw = portfolio_container.read_item(
                item=action["id"], partition_key=action["account_id"]
            )
            if raw.get("_etag", "") != action.get("_etag", ""):
                results["cas_conflict"] += 1
                continue
            if not _eligible(raw):
                results["failed"] += 1
                continue
            if raw.get("share_fmv") is not None and not plan["force"]:
                results["already_set"] += 1
                continue

            share_fmv = json.loads(json.dumps(action["share_fmv"]))
            share_fmv["provenance"]["fetched_at"] = clock().isoformat()
            share_fmv["provenance"]["run_id"] = backup["run_id"]
            body = _clean(raw)
            body["share_fmv"] = share_fmv
            body["updated_at"] = clock().isoformat()
            if not _etag_replace(portfolio_container, raw, body):
                results["cas_conflict"] += 1
                continue
            persisted = portfolio_container.read_item(
                item=action["id"], partition_key=action["account_id"]
            )
            if persisted.get("share_fmv") != share_fmv:
                results["failed"] += 1
            else:
                results["updated"] += 1
        except Exception:  # noqa: BLE001
            results["failed"] += 1
    return results, backup_path


def read_backup(path: Path, target: TargetIdentity) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise BackfillError("Backup cannot be read", 2) from exc
    if payload.get("sha256") != _backup_checksum(payload):
        raise BackfillError("Backup checksum mismatch", 2)
    if payload.get("target") != target.as_dict():
        raise BackfillError("Backup target does not match configured Cosmos target", 2)
    if payload.get("script_version") != SCRIPT_VERSION:
        raise BackfillError("Backup script version is unsupported", 2)
    return payload


def restore_backup(portfolio_container, path: Path, target: TargetIdentity) -> dict:
    """CAS-restore only documents whose live FMV was written by this run."""
    backup = read_backup(path, target)
    result = {"restored": 0, "already_restored": 0, "cas_conflict": 0, "failed": 0}
    for entry in backup.get("documents", []):
        try:
            live = portfolio_container.read_item(
                item=entry["id"], partition_key=entry["partition_key"]
            )
            original = entry["body"]
            if live.get("share_fmv") == original.get("share_fmv"):
                result["already_restored"] += 1
                continue
            provenance = (live.get("share_fmv") or {}).get("provenance") or {}
            if provenance.get("run_id") != backup["run_id"]:
                result["cas_conflict"] += 1
                continue
            live_untouched = {
                key: value
                for key, value in _clean(live).items()
                if key not in {"share_fmv", "updated_at"}
            }
            original_untouched = {
                key: value
                for key, value in original.items()
                if key not in {"share_fmv", "updated_at"}
            }
            if live_untouched != original_untouched:
                result["cas_conflict"] += 1
                continue
            restored = original
            if _etag_replace(portfolio_container, live, restored):
                result["restored"] += 1
            else:
                result["cas_conflict"] += 1
        except Exception:  # noqa: BLE001
            result["failed"] += 1
    return result


def _build_containers(database: str, portfolio_name: str, symbols_name: str):
    from azure.cosmos import CosmosClient

    endpoint = os.environ.get("COSMOSDB_ENDPOINT", "")
    key = os.environ.get("COSMOSDB_KEY", "")
    if not endpoint or not key:
        raise BackfillError("COSMOSDB_ENDPOINT and COSMOSDB_KEY are required", 2)
    client = CosmosClient(endpoint, credential=key)
    db = client.get_database_client(database)
    return (
        db.get_container_client(portfolio_name),
        db.get_container_client(symbols_name),
        TargetIdentity(_safe_endpoint(endpoint), database, portfolio_name, symbols_name),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit/apply Yahoo Open FMV for eligible Dividend · Buy legs."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--restore", metavar="BACKUP")
    parser.add_argument("--plan-sha256")
    parser.add_argument("--confirm")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--confirm-overwrite")
    parser.add_argument("--all-active", action="store_true")
    parser.add_argument("--account-id")
    parser.add_argument("--security-id")
    parser.add_argument("--ca-group-id")
    parser.add_argument("--date-from")
    parser.add_argument("--date-to")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--database", default="stock-options-manager")
    parser.add_argument("--portfolio-container", default="portfolio")
    parser.add_argument("--symbols-container", default="symbols")
    parser.add_argument("--backup-dir", default=str(DEFAULT_BACKUP_DIR))
    return parser


def _validate_args(args: argparse.Namespace) -> None:
    for field in ("date_from", "date_to"):
        value = getattr(args, field)
        if value and not _iso_date(value):
            raise BackfillError(f"--{field.replace('_', '-')} must be YYYY-MM-DD")
    if args.date_from and args.date_to and args.date_from > args.date_to:
        raise BackfillError("--date-from must not be after --date-to")
    if args.limit is not None and args.limit <= 0:
        raise BackfillError("--limit must be positive")
    if args.plan_sha256 and (
        len(args.plan_sha256) != 64
        or any(ch not in "0123456789abcdefABCDEF" for ch in args.plan_sha256)
    ):
        raise BackfillError("--plan-sha256 must be a 64-character hexadecimal SHA-256")
    if args.restore:
        if any(
            (
                args.force,
                args.account_id,
                args.security_id,
                args.ca_group_id,
                args.date_from,
                args.date_to,
                args.limit,
                args.all_active,
                args.plan_sha256,
                args.confirm,
                args.confirm_overwrite,
            )
        ):
            raise BackfillError("--restore cannot be combined with plan/apply filters")
        return
    if args.apply:
        if args.confirm != APPLY_CONFIRMATION or not args.plan_sha256:
            raise BackfillError(
                f"--apply requires --plan-sha256 and --confirm {APPLY_CONFIRMATION}"
            )
        filtered = any(
            (args.account_id, args.security_id, args.ca_group_id,
             args.date_from, args.date_to, args.limit)
        )
        if not filtered and not args.all_active:
            raise BackfillError("Unfiltered apply requires --all-active")
        if args.force and args.confirm_overwrite != OVERWRITE_CONFIRMATION:
            raise BackfillError(
                f"--force requires --confirm-overwrite {OVERWRITE_CONFIRMATION}"
            )
    elif args.plan_sha256 or args.confirm or args.confirm_overwrite:
        raise BackfillError("Apply confirmation flags are valid only with --apply")


def _print_json(value: dict) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


def main(argv: Iterable[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    try:
        args = _parser().parse_args(argv)
        _validate_args(args)
        portfolio, symbols, target = _build_containers(
            args.database, args.portfolio_container, args.symbols_container
        )
        if args.restore:
            result = restore_backup(portfolio, Path(args.restore), target)
            _print_json({"mode": "restore", "target": target.as_dict(), **result})
            return 3 if result["cas_conflict"] or result["failed"] else 0

        filters = {
            "account_id": args.account_id,
            "security_id": args.security_id,
            "ca_group_id": args.ca_group_id,
            "date_from": args.date_from,
            "date_to": args.date_to,
        }
        plan = build_plan(
            portfolio,
            symbols,
            target=target,
            filters=filters,
            force=args.force,
            limit=args.limit,
        )
        if not args.apply:
            _print_json({"mode": "dry-run", **plan})
            unavailable = plan["counts"]["failed"] + plan["counts"]["fx_unavailable"]
            return 2 if unavailable and not plan["actions"] else 0
        if plan["sha256"].lower() != args.plan_sha256.lower():
            raise BackfillError("Plan fingerprint changed; rerun dry-run", 1)
        results, backup_path = apply_plan(
            portfolio, plan, backup_dir=Path(args.backup_dir)
        )
        _print_json(
            {
                "mode": "apply",
                "target": target.as_dict(),
                "plan_sha256": plan["sha256"],
                "backup": str(backup_path) if backup_path else None,
                "counts": results,
            }
        )
        unavailable = results["failed"] + results["fx_unavailable"]
        if unavailable and not results["updated"]:
            return 2
        return 3 if results["cas_conflict"] or results["failed"] else 0
    except BackfillError as exc:
        _print_json({"error": str(exc), "exit_code": exc.exit_code})
        return exc.exit_code
    except Exception:  # noqa: BLE001
        _print_json({"error": "Backfill failed closed", "exit_code": 2})
        return 2


if __name__ == "__main__":
    sys.exit(main())
