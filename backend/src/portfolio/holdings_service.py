"""Derived holdings computation from ledger movements.

Cost method: chronological moving weighted average (CMP).
- BUY COMPLETE: adds shares to pool; pool_cost += gross_eur (gross already includes commission).
- BUY ZERO_COST: adds shares to pool at cost 0 (scrip dividends, rights); dilutes avg naturally.
- BUY INCOMPLETE: genuinely unknown cost; adds unpaid_shares (no pool entry, warning emitted).
- SELL ACCIONES: removes shares; assigns cost = sold_qty × current avg (pool_cost/pool_shares).
- SELL DERECHOS: no share/pool change; net proceeds counted in sales and rights.
- TRANSFER_IN: adds qty to pool at carried_cost_basis_eur; not counted in purchase_outflow.
- TRANSFER_OUT: removes qty at current CMP avg; not counted in sale_proceeds.
- DIVIDEND: net_eur accumulated separately.
- Superseded, voided, deleted movements are excluded before reaching this function.
- Negative inventory: pool capped at 0; excess quantity sold at cost 0; warning emitted.

All arithmetic in Decimal for precision.
"""

from __future__ import annotations

import logging
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional

from .cosmos_portfolio import CosmosPortfolioService
from .cosmos_securities import CosmosSecuritiesService
from .symbol_config_sync import ensure_symbol_config

logger = logging.getLogger(__name__)

_TWO_PLACES = Decimal("0.01")
_SIX_PLACES = Decimal("0.000001")
_ZERO = Decimal("0")


def _d(v: Any) -> Decimal:
    if isinstance(v, Decimal):
        return v
    if v is None:
        return _ZERO
    try:
        return Decimal(str(v))
    except Exception:
        return _ZERO


def _fmt2(v: Decimal) -> str:
    return str(v.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP))


def _fmt6(v: Decimal) -> str:
    return str(v.quantize(_SIX_PLACES, rounding=ROUND_HALF_UP))


class HoldingsService:
    """Compute derived holdings from the portfolio ledger using CMP cost basis."""

    def __init__(
        self,
        portfolio_svc: CosmosPortfolioService,
        securities_svc: CosmosSecuritiesService,
    ) -> None:
        self.portfolio_svc = portfolio_svc
        self.securities_svc = securities_svc

    def compute_holdings(
        self, account_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Compute holdings from all non-deleted ledger movements.

        Args:
            account_id: Filter to specific account; None = all accounts.

        Returns:
            Dict with 'holdings' list and 'summary' dict.
        """
        movements = self.portfolio_svc.get_all_movements_for_holdings()

        # Filter by account if requested
        if account_id:
            movements = [
                m for m in movements if m.get("account_id") == account_id
            ]

        # Chronological ordering is required for CMP correctness.
        # Tie-break on id for determinism when trade_date is identical.
        movements.sort(key=lambda m: (m.get("trade_date") or "", m.get("id") or ""))

        # Per-security CMP state and accumulators
        per_security: Dict[str, Dict[str, Any]] = {}

        for m in movements:
            security_id = m.get("security_id", "")
            if not security_id:
                continue
            if security_id not in per_security:
                per_security[security_id] = {
                    "security_id": security_id,
                    "ticker": m.get("ticker", security_id.split(":")[-1]),
                    # CMP pool state
                    "pool_shares": _ZERO,    # shares with known cost (COMPLETE + ZERO_COST)
                    "pool_cost": _ZERO,      # EUR cost of pool shares
                    "unpaid_shares": _ZERO,  # genuinely INCOMPLETE BUY shares (no cost)
                    "total_shares": _ZERO,   # all shares (pool + unpaid)
                    # Accumulators
                    "total_purchase_outflow_eur": _ZERO,  # Σ gross_eur BUY COMPLETE
                    "cost_basis_sold_eur": _ZERO,         # Σ CMP cost → SELL ACCIONES
                    "total_sale_proceeds_eur": _ZERO,     # Σ(gross-fee) all SELL types
                    "rights_proceeds_eur": _ZERO,         # Σ(gross-fee) SELL DERECHOS
                    "total_dividends_eur": _ZERO,
                    "buy_count": 0,
                    "zero_cost_count": 0,    # ZERO_COST acquisitions (informational)
                    "incomplete_count": 0,   # genuinely INCOMPLETE acquisitions (warning)
                    "accounts": set(),
                    "movement_warnings": [],
                }
            agg = per_security[security_id]
            agg["accounts"].add(m.get("account_id", "_unassigned"))

            qty = _d(m.get("quantity", "0"))
            gross_eur = _d((m.get("gross") or {}).get("eur_amount", "0"))
            commission_eur = _d((m.get("fees") or {}).get("total_eur", "0"))
            cost_basis_status = m.get("cost_basis_status", "COMPLETE")
            txn_type = m.get("txn_type", "")

            if txn_type == "BUY":
                agg["total_shares"] += qty
                agg["buy_count"] += 1
                if cost_basis_status == "INCOMPLETE":
                    # Genuinely unknown cost — stays out of pool; warning emitted later.
                    agg["unpaid_shares"] += qty
                    agg["incomplete_count"] += 1
                else:
                    # COMPLETE or ZERO_COST — enters pool.
                    # gross_eur already includes commission (corrected import mapping).
                    # For ZERO_COST, gross_eur == 0; pool_cost unchanged.
                    cost = gross_eur
                    agg["pool_shares"] += qty
                    agg["pool_cost"] += cost
                    if cost_basis_status != "ZERO_COST":
                        agg["total_purchase_outflow_eur"] += cost
                    else:
                        agg["zero_cost_count"] += 1

            elif txn_type == "SELL":
                # DERECHOS sales contribute to proceeds but do NOT decrement shares.
                # Movements without sales_type default to ACCIONES behaviour.
                sale_type = m.get("sales_type") or "ACCIONES"
                net_proceeds = gross_eur - commission_eur
                agg["total_sale_proceeds_eur"] += net_proceeds

                if sale_type == "ACCIONES":
                    agg["total_shares"] -= qty
                    if agg["pool_shares"] > _ZERO:
                        avg_cost = agg["pool_cost"] / agg["pool_shares"]
                        sold_from_pool = min(qty, agg["pool_shares"])
                        cost_sold = sold_from_pool * avg_cost
                        agg["pool_shares"] -= sold_from_pool
                        agg["pool_cost"] -= cost_sold
                        agg["cost_basis_sold_eur"] += cost_sold
                    # Excess qty beyond pool (unpaid shares or negative inventory) → cost 0.
                else:
                    # DERECHOS: no pool or share count change.
                    agg["rights_proceeds_eur"] += net_proceeds

            elif txn_type == "DIVIDEND":
                net_eur = _d((m.get("net") or {}).get("eur_amount", "0"))
                agg["total_dividends_eur"] += net_eur

            elif txn_type == "TRANSFER_IN":
                # Carries shares at explicitly stored cost basis; not a purchase outflow.
                agg["total_shares"] += qty
                carried_cost = _d(m.get("transfer_cost_basis_eur", "0"))
                if carried_cost > _ZERO:
                    agg["pool_shares"] += qty
                    agg["pool_cost"] += carried_cost

            elif txn_type == "TRANSFER_OUT":
                # Removes shares proportionally at CMP; not counted in sale proceeds.
                agg["total_shares"] -= qty
                if agg["pool_shares"] > _ZERO:
                    avg_cost = agg["pool_cost"] / agg["pool_shares"]
                    transferred_from_pool = min(qty, agg["pool_shares"])
                    cost_removed = transferred_from_pool * avg_cost
                    agg["pool_shares"] -= transferred_from_pool
                    agg["pool_cost"] -= cost_removed

            for w in m.get("warnings", []):
                agg["movement_warnings"].append(w)

        security_names = _resolve_security_names(
            list(per_security.keys()), self.securities_svc
        )

        # ── Read-repair: ensure symbol_config exists for every security in holdings ──
        # This catches any enrollment failures from §2.1–2.3.  Only calls ensure
        # when the config is genuinely missing (pre-check to avoid unnecessary
        # Cosmos writes and to allow tests to verify the pre-check is honoured).
        try:
            symbols_container = self.securities_svc.container
            for security_id in per_security:
                parts = security_id.split(":", 1)
                ticker = parts[1].upper() if len(parts) == 2 else security_id.upper()
                config_id = f"config_{ticker}"
                # Pre-check: skip if config already exists
                config_exists = False
                try:
                    symbols_container.read_item(item=config_id, partition_key=ticker)
                    config_exists = True
                except Exception:
                    pass
                if not config_exists:
                    try:
                        ensure_symbol_config(
                            symbols_container, security_id, source="read_repair"
                        )
                    except Exception as exc:
                        logger.warning(
                            "holdings read-repair: ensure_symbol_config failed for %s: %s",
                            security_id,
                            exc,
                        )
        except Exception as exc:
            logger.warning("holdings read-repair: skipped due to error: %s", exc)

        # Build holdings list and portfolio-wide summary accumulators
        holdings_list = []
        summary_purchase_outflow = _ZERO
        summary_cost_basis_sold = _ZERO
        summary_remaining = _ZERO
        summary_sale_proceeds = _ZERO
        summary_rights_proceeds = _ZERO
        summary_dividends = _ZERO
        global_has_incomplete = False

        for security_id, agg in per_security.items():
            pool_shares = agg["pool_shares"]
            pool_cost = agg["pool_cost"]
            total_shares = agg["total_shares"]

            # CMP average: pool_cost / pool_shares (null when pool is empty)
            avg_cost: Optional[Decimal] = None
            if pool_shares > _ZERO:
                avg_cost = (pool_cost / pool_shares).quantize(
                    _TWO_PLACES, rounding=ROUND_HALF_UP
                )

            zero_cost_count = agg["zero_cost_count"]
            incomplete_count = agg["incomplete_count"]
            # Holding status: INCOMPLETE only when there are genuinely unknown-cost shares.
            # ZERO_COST shares are fully resolved; they do not cause INCOMPLETE status.
            holding_cost_basis_status = "INCOMPLETE" if incomplete_count > 0 else "COMPLETE"
            if incomplete_count > 0:
                global_has_incomplete = True

            purchase_outflow = agg["total_purchase_outflow_eur"]
            cost_sold = agg["cost_basis_sold_eur"]
            remaining = pool_cost          # remaining_cost_basis = pool_cost residual
            sale_proceeds = agg["total_sale_proceeds_eur"]
            rights_proceeds = agg["rights_proceeds_eur"]
            realized = sale_proceeds - cost_sold
            dividends = agg["total_dividends_eur"]

            summary_purchase_outflow += purchase_outflow
            summary_cost_basis_sold += cost_sold
            summary_remaining += remaining
            summary_sale_proceeds += sale_proceeds
            summary_rights_proceeds += rights_proceeds
            summary_dividends += dividends

            item_warnings = []
            if total_shares < _ZERO:
                item_warnings.append({
                    "type": "NEGATIVE_INVENTORY",
                    "message": (
                        "Negative holdings — earlier purchases may not yet be imported"
                    ),
                })
            if incomplete_count > 0:
                item_warnings.append({
                    "type": "INCOMPLETE_COST_BASIS",
                    "count": incomplete_count,
                    "message": (
                        f"{incomplete_count} acquisition(s) with genuinely unknown cost basis"
                    ),
                })

            company_name = security_names.get(security_id, "")

            holdings_list.append({
                "security_id": security_id,
                "ticker": agg["ticker"],
                "company_name": company_name,
                "total_shares": _fmt6(total_shares),
                "avg_cost_basis_eur": (
                    str(avg_cost.quantize(_TWO_PLACES)) if avg_cost is not None else None
                ),
                "cost_basis_status": holding_cost_basis_status,
                # CMP cost basis fields
                "total_purchase_outflow_eur": _fmt2(purchase_outflow),
                "cost_basis_sold_eur": _fmt2(cost_sold),
                "remaining_cost_basis_eur": _fmt2(remaining),
                "total_sale_proceeds_eur": _fmt2(sale_proceeds),
                "rights_proceeds_eur": _fmt2(rights_proceeds),
                "realized_result_eur": _fmt2(realized),
                # Backward-compatible aliases
                "total_invested_eur": _fmt2(purchase_outflow),
                "total_purchases_eur": _fmt2(purchase_outflow),
                "total_sales_eur": _fmt2(sale_proceeds),
                "current_invested_eur": _fmt2(remaining),
                "total_dividends_eur": _fmt2(dividends),
                "accounts": sorted(agg["accounts"]),
                "warnings": item_warnings,
            })

        # Sort: negative holdings last, then by security_id for stable output
        holdings_list.sort(
            key=lambda h: (
                Decimal(h["total_shares"]) < _ZERO,
                h["security_id"],
            )
        )

        summary_realized = summary_sale_proceeds - summary_cost_basis_sold

        return {
            "holdings": holdings_list,
            "summary": {
                "total_securities": len(holdings_list),
                # CMP cost basis fields
                "total_purchase_outflow_eur": _fmt2(summary_purchase_outflow),
                "cost_basis_sold_eur": _fmt2(summary_cost_basis_sold),
                "remaining_cost_basis_eur": _fmt2(summary_remaining),
                "total_sale_proceeds_eur": _fmt2(summary_sale_proceeds),
                "rights_proceeds_eur": _fmt2(summary_rights_proceeds),
                "realized_result_eur": _fmt2(summary_realized),
                "has_incomplete_cost_basis": global_has_incomplete,
                # Backward-compatible aliases
                "total_invested_eur": _fmt2(summary_purchase_outflow),
                "total_purchases_eur": _fmt2(summary_purchase_outflow),
                "total_sales_eur": _fmt2(summary_sale_proceeds),
                "current_invested_eur": _fmt2(summary_remaining),
                "total_dividends_eur": _fmt2(summary_dividends),
            },
        }


def _resolve_security_names(
    security_ids: List[str],
    securities_svc: CosmosSecuritiesService,
) -> Dict[str, str]:
    """Batch-resolve company names for a list of security IDs."""
    result: Dict[str, str] = {}
    for sid in security_ids:
        try:
            sec = securities_svc.get_security(sid)
            if sec:
                result[sid] = sec.get("company_name", "")
        except Exception as exc:
            logger.debug("Could not resolve security name for %s: %s", sid, exc)
    return result
