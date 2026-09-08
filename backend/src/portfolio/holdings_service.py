"""Derived holdings computation from ledger movements.

Cost method: FIFO (First-In, First-Out) lot depletion.
- BUY COMPLETE: creates a lot; lot cost = net.eur_amount (total cash outflow incl. commission).
- BUY ZERO_COST: creates a zero-cost lot (scrip dividends); dilutes avg naturally.
- BUY INCOMPLETE: creates an unknown-cost lot; warning emitted; cost treated as 0 when consumed.
- SELL ACCIONES: consumes oldest lots first (FIFO by trade_date, then movement_id).
- SELL DERECHOS: no lot consumption; net proceeds counted in sales and rights.
- TRANSFER_IN: creates a lot at carried_cost_basis_eur; not counted in purchase_outflow.
- TRANSFER_OUT: consumes oldest lots first (like SELL ACCIONES); not counted in sale_proceeds.
- DIVIDEND: net_eur accumulated separately.
- Superseded, voided, deleted movements are excluded before reaching this function.
- Negative inventory: remaining sell qty at cost 0; warning emitted.

Net convention (per danny-fifo-net-accounting-contract.md §1):
  BUY:  net = gross + fees  (total cash outflow; gross = trade consideration)
  SELL: net = gross - fees  (total cash inflow; gross = total proceeds)
  Holdings cost ALWAYS uses net.eur_amount for BUY lots.

All arithmetic in Decimal for precision.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
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


@dataclass
class _Lot:
    """A single FIFO acquisition lot."""
    lot_id: str              # movement_id that created this lot
    trade_date: str          # ISO date (for FIFO ordering)
    quantity: Decimal        # remaining shares (decremented on sells)
    original_quantity: Decimal
    unit_cost_eur: Optional[Decimal]  # None = INCOMPLETE; 0 = ZERO_COST; >0 = COMPLETE
    cost_basis_status: str   # COMPLETE | ZERO_COST | INCOMPLETE


def _consume_lots(lots: List[_Lot], sell_qty: Decimal):
    """Consume sell_qty shares from lots in FIFO order (lots already sorted).

    Returns (cost_consumed: Decimal, negative_inventory: bool).
    Lots are mutated in place.
    """
    remaining = sell_qty
    cost_consumed = _ZERO
    for lot in lots:
        if remaining <= _ZERO:
            break
        if lot.quantity <= _ZERO:
            continue
        take = min(lot.quantity, remaining)
        unit_c = lot.unit_cost_eur if lot.unit_cost_eur is not None else _ZERO
        cost_consumed += take * unit_c
        lot.quantity -= take
        remaining -= take
    negative_inventory = remaining > _ZERO
    return cost_consumed, negative_inventory


class HoldingsService:
    """Compute derived holdings from the portfolio ledger using FIFO cost basis."""

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

        # Chronological ordering required for FIFO correctness.
        # Same-date corporate-action legs must process in ca_group_seq order
        # (e.g. CONSOLIDATION_OUT before CONSOLIDATION_IN before
        # FRACTIONAL_CASH_OUT); id is the final tie-break for determinism.
        movements.sort(key=lambda m: (
            m.get("trade_date") or "",
            m.get("ca_group_id") or "",
            m.get("ca_group_seq") if m.get("ca_group_seq") is not None else 0,
            m.get("id") or "",
        ))

        # Per-security FIFO state and accumulators
        per_security: Dict[str, Dict[str, Any]] = {}

        for m in movements:
            security_id = m.get("security_id", "")
            if not security_id:
                continue
            if security_id not in per_security:
                per_security[security_id] = {
                    "security_id": security_id,
                    "ticker": m.get("ticker", security_id.split(":")[-1]),
                    # FIFO lot list (appended in chronological order; already sorted)
                    "lots": [],
                    # Share counter (all shares including INCOMPLETE)
                    "total_shares": _ZERO,
                    # Accumulators
                    "total_purchase_outflow_eur": _ZERO,  # Σ net_eur BUY COMPLETE only
                    "cost_basis_sold_eur": _ZERO,         # Σ FIFO cost → SELL ACCIONES
                    "total_sale_proceeds_eur": _ZERO,     # Σ net proceeds all SELL types
                    "rights_proceeds_eur": _ZERO,         # Σ net proceeds SELL DERECHOS
                    "total_dividends_eur": _ZERO,
                    "buy_count": 0,
                    "zero_cost_count": 0,    # ZERO_COST acquisitions (informational)
                    "incomplete_count": 0,   # INCOMPLETE acquisitions (warning)
                    "has_negative_inventory": False,
                    "accounts": set(),
                    "movement_warnings": [],
                }
            agg = per_security[security_id]
            agg["accounts"].add(m.get("account_id", "_unassigned"))

            qty = _d(m.get("quantity", "0"))
            gross_eur = _d((m.get("gross") or {}).get("eur_amount", "0"))
            net_eur = _d((m.get("net") or {}).get("eur_amount", "0"))
            commission_eur = _d((m.get("fees") or {}).get("total_eur", "0"))
            cost_basis_status = m.get("cost_basis_status", "COMPLETE")
            txn_type = m.get("txn_type", "")
            movement_id = m.get("id", "")
            trade_date = m.get("trade_date") or ""

            if txn_type == "BUY":
                agg["total_shares"] += qty
                agg["buy_count"] += 1
                if cost_basis_status == "INCOMPLETE":
                    # Genuinely unknown cost — lot created with None unit_cost; warning later.
                    unit_cost = None
                    agg["incomplete_count"] += 1
                    lot = _Lot(
                        lot_id=movement_id,
                        trade_date=trade_date,
                        quantity=qty,
                        original_quantity=qty,
                        unit_cost_eur=None,
                        cost_basis_status="INCOMPLETE",
                    )
                    agg["lots"].append(lot)
                else:
                    # COMPLETE or ZERO_COST — enter pool.
                    # BUY cost = net.eur_amount (total cash outflow = gross + commission).
                    lot_cost = net_eur  # 0 for ZERO_COST
                    if qty > _ZERO:
                        unit_cost = lot_cost / qty
                    else:
                        unit_cost = _ZERO
                    lot = _Lot(
                        lot_id=movement_id,
                        trade_date=trade_date,
                        quantity=qty,
                        original_quantity=qty,
                        unit_cost_eur=unit_cost,
                        cost_basis_status=cost_basis_status,
                    )
                    agg["lots"].append(lot)
                    if cost_basis_status != "ZERO_COST":
                        agg["total_purchase_outflow_eur"] += lot_cost
                    else:
                        agg["zero_cost_count"] += 1

            elif txn_type == "SELL":
                # DERECHOS sales contribute to proceeds but do NOT consume lots.
                sale_type = m.get("sales_type") or "ACCIONES"
                net_proceeds = gross_eur - commission_eur
                agg["total_sale_proceeds_eur"] += net_proceeds

                if sale_type == "ACCIONES":
                    agg["total_shares"] -= qty
                    cost_consumed, neg_inv = _consume_lots(agg["lots"], qty)
                    agg["cost_basis_sold_eur"] += cost_consumed
                    if neg_inv:
                        agg["has_negative_inventory"] = True
                else:
                    # DERECHOS: no lot consumption.
                    agg["rights_proceeds_eur"] += net_proceeds

            elif txn_type == "DIVIDEND":
                agg["total_dividends_eur"] += net_eur

            elif txn_type == "TRANSFER_IN":
                # Creates a lot at the carried cost basis; not a purchase outflow.
                agg["total_shares"] += qty
                carried_cost = _d(m.get("transfer_cost_basis_eur", "0"))
                if qty > _ZERO:
                    unit_cost = carried_cost / qty
                else:
                    unit_cost = _ZERO
                lot = _Lot(
                    lot_id=movement_id,
                    trade_date=trade_date,
                    quantity=qty,
                    original_quantity=qty,
                    unit_cost_eur=unit_cost,
                    cost_basis_status="COMPLETE" if carried_cost >= _ZERO else "INCOMPLETE",
                )
                agg["lots"].append(lot)

            elif txn_type == "TRANSFER_OUT":
                # Consumes lots in FIFO order; not counted in sale proceeds.
                agg["total_shares"] -= qty
                _consume_lots(agg["lots"], qty)

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
            lots: List[_Lot] = agg["lots"]
            total_shares = agg["total_shares"]

            # FIFO remaining cost = sum of (remaining_qty × unit_cost) for non-INCOMPLETE lots.
            # INCOMPLETE lots have None unit_cost and are excluded from pool cost.
            pool_shares = _ZERO
            remaining_cost = _ZERO
            for lot in lots:
                if lot.quantity <= _ZERO:
                    continue
                if lot.unit_cost_eur is not None:
                    pool_shares += lot.quantity
                    remaining_cost += lot.quantity * lot.unit_cost_eur

            # FIFO average: remaining_cost / pool_shares (null when pool is empty)
            avg_cost: Optional[Decimal] = None
            if pool_shares > _ZERO:
                avg_cost = (remaining_cost / pool_shares).quantize(
                    _TWO_PLACES, rounding=ROUND_HALF_UP
                )

            incomplete_count = agg["incomplete_count"]
            # Holding status: INCOMPLETE when any genuinely unknown-cost acquisition exists.
            holding_cost_basis_status = "INCOMPLETE" if incomplete_count > 0 else "COMPLETE"
            if incomplete_count > 0:
                global_has_incomplete = True

            purchase_outflow = agg["total_purchase_outflow_eur"]
            cost_sold = agg["cost_basis_sold_eur"]
            remaining = remaining_cost
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
            if total_shares < _ZERO or agg["has_negative_inventory"]:
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
                # FIFO cost basis fields
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
                # FIFO cost basis fields
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
