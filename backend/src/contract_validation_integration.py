"""Contract validation integration layer.

Livingston's ownership: API/persistence seam between the frontend and Rusty's
`run_contract_validation` engine (backend/src/agent_runner.py).

Responsibilities:
- POST /api/best-options/validate → 202 + run_id
- GET /api/best-options/validate/{run_id} → status polling
- Force chain refresh before validation
- Exact contract lookup (no fallback)
- Build immutable evaluated_snapshot
- Persist validation activity with run_id
- In-flight deduplication + bounded concurrency (max 4)
- Status durability through activity upsert

Design reference: `.squad/decisions/inbox/copilot-best-option-contract-validation-approved.md`
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from uuid import uuid4

from src.cosmos_db import CosmosDBService
from src.options_chain_cache import get_options_chain_cache, apply_agent_view
from src.options_chain_view import usable_quote, usable_greek, contract_view
from src.options_chain_filters import filter_options_chain_by_type, filter_options_chain_by_delta, get_contract
from src.greeks_calculator import _fetch_risk_free_rate
from src.best_options import DEFAULT_DTE_MIN, DEFAULT_DTE_MAX, _atm_iv
from src.yfinance_data_provider import OPTIONS_CHAIN_SCHEMA_DESCRIPTION

logger = logging.getLogger(__name__)

# Module-level in-flight registry and concurrency control
_validation_lock = asyncio.Lock()
_in_flight_validations: Dict[str, Dict[str, Any]] = {}  # key -> {run_id, started_at, task}
_MAX_CONCURRENT_VALIDATIONS = 4


def _validation_key(symbol: str, side: str, strike: float, expiration: str) -> str:
    """Normalize validation request to a deduplication key."""
    return f"{symbol.upper()}_{side.lower()}_{strike}_{expiration}"


async def _force_chain_refresh(symbol: str) -> bool:
    """Force a targeted chain refresh for the symbol.

    Returns:
        bool: True if refresh succeeded, False otherwise
    """
    try:
        chain_cache = get_options_chain_cache()
        await chain_cache.refresh(symbol)
        return True
    except Exception as e:
        logger.error(f"Chain refresh failed for {symbol}: {e}", exc_info=True)
        return False


def _find_exact_contract(
    chain: dict,
    side: str,
    strike: float,
    expiration: str,
    now: datetime,
) -> Optional[dict]:
    """Locate the exact contract (side, strike, expiration) in the chain.

    Args:
        chain: Full options chain (post-refresh)
        side: "call" or "put"
        strike: Exact strike price
        expiration: ISO expiration date (YYYY-MM-DD)
        now: Current timestamp for staleness check

    Returns:
        Contract view dict if found, None if absent/expired/invalid
    """
    bucket = chain.get("calls" if side == "call" else "puts", {})
    exp_bucket = bucket.get(expiration, {})

    strike_key = str(strike)
    contract = exp_bucket.get(strike_key)

    if not contract:
        return None

    # Convert to agent view (apply options_chain_view normalization)
    # Use same stale_after_seconds as Best Options (7200)
    contract_normalized = contract_view(contract, now=now, stale_after_seconds=7200)

    return contract_normalized


def _build_market_data_text(
    symbol: str,
    side: str,
    strike: float,
    expiration: str,
    underlying_price: float,
    contract: dict,
    chain_timestamp: str,
    next_earnings_date: Optional[str],
    ex_dividend_date: Optional[str],
) -> str:
    """Build formatted market data text for agent consumption."""
    bid = usable_quote(contract, "bid")
    ask = usable_quote(contract, "ask")
    mid = usable_quote(contract, "mid") or (
        (bid + ask) / 2.0 if bid and ask else None
    )
    iv = usable_quote(contract, "iv")
    delta = usable_greek(contract, "delta")
    gamma = usable_greek(contract, "gamma")
    theta = usable_greek(contract, "theta")
    vega = usable_greek(contract, "vega")

    volume = contract.get("volume", 0)
    oi = contract.get("openInterest", 0)

    lines = [
        f"Contract: {symbol} {expiration} {strike} {side.upper()}",
        f"Underlying: ${underlying_price:.2f}",
        f"Quote: Bid ${bid:.2f} / Ask ${ask:.2f} / Mid ${mid:.2f}" if bid and ask and mid else "Quote: N/A",
        f"IV: {iv:.1%}" if iv else "IV: N/A",
        f"Greeks: Δ={delta:.3f}, Γ={gamma:.4f}, Θ={theta:.3f}, V={vega:.3f}" if all([delta, gamma, theta, vega]) else "Greeks: N/A",
        f"Volume: {volume}, Open Interest: {oi}",
        f"Chain Timestamp: {chain_timestamp}",
    ]

    if next_earnings_date:
        lines.append(f"Next Earnings: {next_earnings_date}")
    if ex_dividend_date:
        lines.append(f"Ex-Dividend: {ex_dividend_date}")

    return "\n".join(lines)


def _build_validation_chain_context(chain: dict, side: str) -> str:
    """Build the same options chain text Alpha sees in normal CC/CSP runs.

    Applies the identical pipeline: apply_agent_view → filter_by_type →
    filter_by_delta. No Best Options scoring/ranking. This is byte-semantically
    identical to what `_build_alpha_options_chain` produces for CC/CSP watchlist
    agents (no current_strike exclusion, no position reference block).

    Args:
        chain: Full options chain (raw, will be normalized)
        side: "call" or "put"

    Returns:
        Formatted chain text for Alpha (OPTIONS_CHAIN_SCHEMA_DESCRIPTION + JSON),
        or empty string if no contracts survive filtering
    """
    # Apply agent view (zero-free normalization)
    structured = apply_agent_view(chain)

    # Filter by type (calls-only or puts-only)
    option_type = "call" if side == "call" else "put"
    structured = filter_options_chain_by_type(structured, option_type)

    # Filter by delta (default ranges: calls 0.15-0.90, puts -0.60 to -0.15)
    structured = filter_options_chain_by_delta(structured)

    # Check if any contracts survive filtering
    bucket = structured.get("calls" if side == "call" else "puts", {})
    if not bucket:
        return ""

    # Return schema description + JSON (same format as normal Alpha chains)
    return OPTIONS_CHAIN_SCHEMA_DESCRIPTION + "\n" + json.dumps(structured, indent=2)


def _build_chain_snapshot_summary(chain: dict, side: str, chain_timestamp: str) -> dict:
    """Build compact chain snapshot summary for audit trail.

    Does NOT duplicate the full chain JSON — just metadata for tracing.

    Args:
        chain: Full options chain
        side: "call" or "put"
        chain_timestamp: ISO timestamp

    Returns:
        Compact summary dict
    """
    underlying_price = chain.get("underlying_price")
    bucket = chain.get("calls" if side == "call" else "puts", {})

    # Count total contracts
    contract_count = sum(len(strikes) for strikes in bucket.values())

    # Get expiration range
    expirations = sorted(bucket.keys())
    exp_range = None
    if expirations:
        # Convert YYYYMMDD to YYYY-MM-DD
        def fmt_exp(exp: str) -> str:
            if len(exp) == 8 and exp.isdigit():
                return f"{exp[:4]}-{exp[4:6]}-{exp[6:]}"
            return exp
        exp_range = [fmt_exp(expirations[0]), fmt_exp(expirations[-1])]

    return {
        "chain_timestamp": chain_timestamp,
        "underlying_price": underlying_price,
        "contract_count": contract_count,
        "expiration_range": exp_range,
        "side": side,
    }


def _validate_alpha_alternative(
    chain: dict,
    side: str,
    requested_strike: float,
    requested_expiration: str,
    alternative: dict,
    category: str,
    underlying_price: float,
    next_earnings_date: Optional[str],
    now: datetime,
) -> tuple[bool, Optional[str], Optional[dict]]:
    """Validate Alpha's proposed alternative against the real chain.

    Implements D4 programmatic validation gates from the design. All gates must
    pass for the alternative to be accepted.

    Args:
        chain: Full options chain (normalized)
        side: "call" or "put"
        requested_strike: Strike of the originally requested contract
        requested_expiration: Expiration of the originally requested contract (YYYY-MM-DD)
        alternative: Alpha's alternative dict (strike/expiration/premium/delta fields)
        category: Symbol category (for premium floor calculation)
        underlying_price: Current underlying price
        next_earnings_date: Next earnings date (YYYY-MM-DD) or None
        now: Current datetime

    Returns:
        (is_valid, rejection_reason, normalized_contract)
    """
    # Extract alternative coordinates
    alt_strike = alternative.get("strike")
    alt_expiration = alternative.get("expiration")

    # G0: Alternative must have complete coordinates
    if alt_strike is None or alt_expiration is None:
        return False, "Alternative missing strike or expiration", None

    # Normalize types
    try:
        alt_strike = float(alt_strike)
    except (TypeError, ValueError):
        return False, f"Alternative strike invalid: {alt_strike}", None

    alt_expiration = str(alt_expiration)

    # G1: Contract must exist in chain
    option_type = "call" if side == "call" else "put"
    alt_contract = get_contract(chain, alt_strike, alt_expiration, option_type)
    if alt_contract is None:
        return False, f"Alternative contract not found in chain: {alt_strike} {alt_expiration}", None

    # G3: Must NOT be identical to requested contract
    if alt_strike == requested_strike and alt_expiration == requested_expiration:
        return False, "Alternative identical to requested contract", None

    # G4: Single-parameter relaxation (exactly one of strike OR expiration differs)
    strike_differs = alt_strike != requested_strike
    expiration_differs = alt_expiration != requested_expiration
    if strike_differs and expiration_differs:
        return False, "Alternative changes both strike AND expiration (two parameters)", None
    if not strike_differs and not expiration_differs:
        # Already caught by G3, but defensive
        return False, "Alternative identical to requested contract", None

    # G5: Proximity checks
    # Strike proximity: within ±20% or ≤5 strikes away in chain
    if strike_differs:
        strike_pct_diff = abs((alt_strike - requested_strike) / requested_strike) if requested_strike else 1.0
        if strike_pct_diff > 0.20:
            # Check strike distance in chain
            bucket = chain.get("calls" if side == "call" else "puts", {})
            all_strikes = set()
            for exp_bucket in bucket.values():
                all_strikes.update(float(s) for s in exp_bucket.keys())
            sorted_strikes = sorted(all_strikes)
            try:
                req_idx = sorted_strikes.index(requested_strike)
                alt_idx = sorted_strikes.index(alt_strike)
                strike_distance = abs(alt_idx - req_idx)
                if strike_distance > 5:
                    return False, f"Alternative strike too far: {strike_distance} strikes away (>5)", None
            except ValueError:
                # One of the strikes not in the chain, already failed G1 for alt, should not happen
                pass

    # Expiration proximity: within ±14 calendar days
    if expiration_differs:
        try:
            req_date = datetime.strptime(requested_expiration, "%Y-%m-%d").date()
            alt_date = datetime.strptime(alt_expiration, "%Y-%m-%d").date()
            day_diff = abs((alt_date - req_date).days)
            if day_diff > 14:
                return False, f"Alternative expiration too far: {day_diff} days away (>14)", None
        except ValueError:
            return False, f"Invalid expiration format: {alt_expiration}", None

    # G6: DTE ≤ 45 (hard cap)
    try:
        alt_exp_date = datetime.strptime(alt_expiration, "%Y-%m-%d").date()
        dte = (alt_exp_date - now.date()).days
        if dte > 45:
            return False, f"Alternative DTE too high: {dte} days (>45)", None
        if dte < 0:
            return False, f"Alternative already expired: {dte} days", None
    except ValueError:
        return False, f"Invalid expiration format: {alt_expiration}", None

    # G7: No spanned earnings
    if next_earnings_date:
        try:
            earnings_date = datetime.strptime(next_earnings_date, "%Y-%m-%d").date()
            if now.date() < earnings_date <= alt_exp_date:
                return False, f"Alternative spans earnings date: {next_earnings_date}", None
        except ValueError:
            pass  # Invalid earnings date, skip check

    # G8: Delta in band (tighter than chain filter: 0.15-0.50 abs for both calls/puts)
    alt_delta = usable_greek(alt_contract, "delta")
    if alt_delta is None:
        return False, "Alternative delta unavailable", None

    abs_delta = abs(alt_delta)
    if abs_delta < 0.15 or abs_delta > 0.50:
        return False, f"Alternative delta out of band: {alt_delta:.3f} (abs must be 0.15-0.50)", None

    # G9: Complete quote (usable bid and delta)
    alt_bid = usable_quote(alt_contract, "bid")
    if alt_bid is None:
        return False, "Alternative bid unavailable", None

    # G10: Premium floor (DTE-scaled, per best-options design)
    # category_premium_min defaults (from best_options.py design):
    # aggressive: 0.0040, balanced: 0.0050, conservative: 0.0060
    category_premium_min = {
        "aggressive": 0.0040,
        "balanced": 0.0050,
        "conservative": 0.0060,
    }.get(category, 0.0050)

    dte_scaled_min = category_premium_min * (dte / 30.0) if dte > 0 else category_premium_min
    bid_basis_ratio = alt_bid / underlying_price if underlying_price > 0 else 0
    if bid_basis_ratio < dte_scaled_min:
        return False, f"Alternative premium too low: {bid_basis_ratio:.4f} < {dte_scaled_min:.4f}", None

    # All gates passed!
    return True, None, alt_contract


def _validate_contract_evidence(contract: dict) -> tuple[bool, Optional[str]]:
    """Validate contract has usable market evidence.

    Returns:
        (is_valid, error_message)
    """
    bid = usable_quote(contract, "bid")
    ask = usable_quote(contract, "ask")

    # Check for zero/crossed/non-finite market
    if bid is None and ask is None:
        return False, "No usable market: both bid and ask are unavailable"

    if bid and ask and bid > ask:
        return False, f"Crossed market: bid ${bid:.2f} > ask ${ask:.2f}"

    if bid and bid <= 0:
        return False, f"Invalid bid: ${bid:.2f}"

    if ask and ask <= 0:
        return False, f"Invalid ask: ${ask:.2f}"

    # Check IV availability
    iv = usable_quote(contract, "iv")
    if iv is None:
        return False, "IV unavailable"

    # Check delta availability
    delta = usable_greek(contract, "delta")
    if delta is None:
        return False, "Delta unavailable"

    return True, None


async def _build_evaluated_snapshot(
    symbol: str,
    side: str,
    strike: float,
    expiration: str,
    contract: dict,
    chain: dict,
    cosmos: CosmosDBService,
) -> Dict[str, Any]:
    """Build immutable evaluated snapshot for the engine.

    Args:
        symbol: Ticker symbol
        side: "call" or "put"
        strike: Strike price
        expiration: Expiration date (YYYY-MM-DD)
        contract: Normalized contract view (from _find_exact_contract)
        chain: Full options chain (source of canonical underlying_price)
        cosmos: CosmosDB service

    Returns:
        Evidence snapshot dict with all required fields for run_contract_validation
    """
    # Get symbol document for category and holdings
    sym_doc = cosmos.get_symbol(symbol)
    if not sym_doc:
        raise ValueError(f"Symbol {symbol} not found in Cosmos")

    enrichment = sym_doc.get("enrichment", {})
    category = enrichment.get("category", "balanced")
    total_shares = sym_doc.get("total_shares", 0)

    # Get calendar events
    next_earnings = cosmos.get_next_earnings_date(symbol)
    ex_dividend = cosmos.get_next_calendar_event_date(symbol, "ex_dividend")

    # Extract underlying price from chain (canonical source per best_options.py:720)
    # This is the chain-level field, not contract-level
    underlying_price = chain.get("underlying_price")
    if not underlying_price:
        raise ValueError("Underlying price not available in chain")

    chain_timestamp = contract.get("_meta", {}).get("chain_timestamp") or datetime.now(timezone.utc).isoformat()

    # Build contract_data dict
    contract_data = {
        "strike": strike,
        "bid": usable_quote(contract, "bid"),
        "ask": usable_quote(contract, "ask"),
        "mid": usable_quote(contract, "mid"),
        "delta": usable_greek(contract, "delta"),
        "gamma": usable_greek(contract, "gamma"),
        "theta": usable_greek(contract, "theta"),
        "vega": usable_greek(contract, "vega"),
        "rho": usable_greek(contract, "rho"),
        "iv": usable_quote(contract, "iv"),
        "volume": contract.get("volume", 0),
        "open_interest": contract.get("openInterest", 0),
    }

    # Calculate ATM IV (reuse _atm_iv from best_options.py)
    # Chain is already available from caller
    now = datetime.now(timezone.utc)
    today_et = now.date()

    # Get all calls and puts for ATM IV calculation
    calls_bucket = chain.get("calls", {})
    puts_bucket = chain.get("puts", {})

    atm_iv_value = _atm_iv(calls_bucket, puts_bucket, underlying_price, now, today_et)

    # Calculate IV rank (placeholder - not enforced per best_options.py line 54-56)
    iv_rank_value = None  # Not calculated/enforced

    # Build market data text
    market_data_text = _build_market_data_text(
        symbol=symbol,
        side=side,
        strike=strike,
        expiration=expiration,
        underlying_price=underlying_price,
        contract=contract,
        chain_timestamp=chain_timestamp,
        next_earnings_date=next_earnings,
        ex_dividend_date=ex_dividend,
    )

    # Build final snapshot
    snapshot = {
        "category": category,
        "underlying_price": underlying_price,
        "contract_data": contract_data,
        "market_data_text": market_data_text,
        "chain_timestamp": chain_timestamp,
        "next_earnings_date": next_earnings,
        "ex_dividend_date": ex_dividend,
        "atm_iv": atm_iv_value,
        "iv_rank": iv_rank_value,
    }

    # Add total_shares for calls
    if side == "call":
        snapshot["total_shares"] = total_shares

    return snapshot


async def start_validation(
    symbol: str,
    side: str,
    strike: float,
    expiration: str,
    source: str,
    displayed_snapshot: Optional[dict],
    cosmos: CosmosDBService,
    agent_runner: Any,
    context_provider: Any,
) -> Dict[str, Any]:
    """Start contract validation (POST /api/best-options/validate).

    Returns:
        dict: {status: "accepted"|"duplicate"|"max_concurrency"|"error",
               run_id: str, message: str}
    """
    # Normalize inputs
    symbol = symbol.upper()
    side = side.lower()

    # Validate inputs
    if side not in ("call", "put"):
        return {
            "status": "error",
            "message": f"Invalid side: {side}. Must be 'call' or 'put'.",
        }

    if source not in ("best_options", "options_screener"):
        return {
            "status": "error",
            "message": f"Invalid source: {source}. Must be 'best_options' or 'options_screener'.",
        }

    # Build dedup key
    val_key = _validation_key(symbol, side, strike, expiration)

    async with _validation_lock:
        # Check for duplicate in-flight
        if val_key in _in_flight_validations:
            existing = _in_flight_validations[val_key]
            return {
                "status": "duplicate",
                "run_id": existing["run_id"],
                "message": f"Validation already in progress for this contract",
                "started_at": existing["started_at"],
            }

        # Check concurrency limit
        active_count = sum(1 for v in _in_flight_validations.values() if not v.get("task").done())
        if active_count >= _MAX_CONCURRENT_VALIDATIONS:
            return {
                "status": "max_concurrency",
                "message": f"Maximum concurrent validations ({_MAX_CONCURRENT_VALIDATIONS}) reached. Please try again later.",
                "retry_after": 30,
            }

        # Mint run_id
        run_id = str(uuid4())
        started_at = datetime.now(timezone.utc).isoformat()

        # Start background task
        task = asyncio.create_task(
            _execute_validation(
                run_id=run_id,
                val_key=val_key,
                symbol=symbol,
                side=side,
                strike=strike,
                expiration=expiration,
                source=source,
                displayed_snapshot=displayed_snapshot,
                cosmos=cosmos,
                agent_runner=agent_runner,
                context_provider=context_provider,
            )
        )

        # Register in-flight
        _in_flight_validations[val_key] = {
            "run_id": run_id,
            "started_at": started_at,
            "task": task,
            "symbol": symbol,
            "side": side,
            "strike": strike,
            "expiration": expiration,
        }

        logger.info(
            f"Validation started: run_id={run_id}, symbol={symbol}, side={side}, "
            f"strike={strike}, exp={expiration}, source={source}"
        )

        return {
            "status": "accepted",
            "run_id": run_id,
            "started_at": started_at,
            "message": f"Contract validation started for {symbol} {expiration} {strike} {side.upper()}",
            "status_url": f"/api/best-options/validate/{run_id}",
        }


async def _execute_validation(
    run_id: str,
    val_key: str,
    symbol: str,
    side: str,
    strike: float,
    expiration: str,
    source: str,
    displayed_snapshot: Optional[dict],
    cosmos: CosmosDBService,
    agent_runner: Any,
    context_provider: Any,
):
    """Execute validation in background."""
    try:
        # Step 1: Force chain refresh
        logger.info(f"[{run_id}] Forcing chain refresh for {symbol}")
        refresh_success = await _force_chain_refresh(symbol)

        if not refresh_success:
            logger.warning(f"[{run_id}] Chain refresh failed (best-effort), continuing")

        # Step 2: Locate exact contract
        logger.info(f"[{run_id}] Locating exact contract: {side} {strike} {expiration}")
        chain_cache = get_options_chain_cache()
        chain_json = chain_cache.get_or_hydrate(symbol, trigger_swr=False)

        if not chain_json:
            await _persist_validation_activity(
                cosmos=cosmos,
                run_id=run_id,
                symbol=symbol,
                side=side,
                strike=strike,
                expiration=expiration,
                source=source,
                displayed_snapshot=displayed_snapshot,
                evaluated_snapshot=None,
                result={
                    "activity": "WAIT",
                    "is_alert": False,
                    "validation_status": "error",
                    "note": "Chain not available for symbol",
                    "error": "chain_unavailable",
                },
            )
            return

        chain = json.loads(chain_json)
        now = datetime.now(timezone.utc)
        contract = _find_exact_contract(chain, side, strike, expiration, now)

        if not contract:
            await _persist_validation_activity(
                cosmos=cosmos,
                run_id=run_id,
                symbol=symbol,
                side=side,
                strike=strike,
                expiration=expiration,
                source=source,
                displayed_snapshot=displayed_snapshot,
                evaluated_snapshot=None,
                result={
                    "activity": "WAIT",
                    "is_alert": False,
                    "validation_status": "error",
                    "note": f"Contract not found: {symbol} {expiration} {strike} {side.upper()}",
                    "error": "contract_not_found",
                },
            )
            return

        # Step 3: Validate contract evidence
        is_valid, error_msg = _validate_contract_evidence(contract)
        if not is_valid:
            await _persist_validation_activity(
                cosmos=cosmos,
                run_id=run_id,
                symbol=symbol,
                side=side,
                strike=strike,
                expiration=expiration,
                source=source,
                displayed_snapshot=displayed_snapshot,
                evaluated_snapshot=None,
                result={
                    "activity": "WAIT",
                    "is_alert": False,
                    "validation_status": "error",
                    "note": f"Invalid market data: {error_msg}",
                    "error": "invalid_market_data",
                },
            )
            return

        # Step 4: Build immutable evaluated snapshot
        logger.info(f"[{run_id}] Building evaluated snapshot")
        evaluated_snapshot = await _build_evaluated_snapshot(
            symbol=symbol,
            side=side,
            strike=strike,
            expiration=expiration,
            contract=contract,
            chain=chain,
            cosmos=cosmos,
        )

        # Step 4.5: Build validation chain context for Alpha (NEW)
        logger.info(f"[{run_id}] Building validation chain context")
        chain_context_text = _build_validation_chain_context(chain, side)

        # Build D4 validation callback that closes over the refreshed chain
        now = datetime.now(timezone.utc)

        def validated_alternative_callback_impl(**kwargs):
            """D4 validation callback for Alpha's alternative.

            Signature matches agent_runner expectation:
                (side, requested_strike, requested_expiration, alternative,
                 category, underlying_price, next_earnings_date)
            """
            return _validate_alpha_alternative(
                chain=chain,  # Closed over from outer scope
                side=kwargs["side"],
                requested_strike=kwargs["requested_strike"],
                requested_expiration=kwargs["requested_expiration"],
                alternative=kwargs["alternative"],
                category=kwargs["category"],
                underlying_price=kwargs["underlying_price"],
                next_earnings_date=kwargs["next_earnings_date"],
                now=now,  # Closed over from outer scope
            )

        # Step 5: Run validation engine
        logger.info(f"[{run_id}] Running validation engine")
        result = await agent_runner.run_contract_validation(
            symbol=symbol,
            side=side,
            strike=strike,
            expiration=expiration,
            evidence_snapshot=evaluated_snapshot,
            cosmos=cosmos,
            context_provider=context_provider,
            chain_context_text=chain_context_text,
            validated_alternative_callback=validated_alternative_callback_impl,
        )

        # Step 5.5: Add chain snapshot summary to result (agent_runner doesn't handle this)
        chain_snapshot_summary = _build_chain_snapshot_summary(
            chain, side, evaluated_snapshot["chain_timestamp"]
        )
        result["chain_snapshot_summary"] = chain_snapshot_summary

        # Step 6: Persist activity with result
        await _persist_validation_activity(
            cosmos=cosmos,
            run_id=run_id,
            symbol=symbol,
            side=side,
            strike=strike,
            expiration=expiration,
            source=source,
            displayed_snapshot=displayed_snapshot,
            evaluated_snapshot=evaluated_snapshot,
            result=result,
        )

        logger.info(
            f"[{run_id}] Validation complete: activity={result['activity']}, "
            f"validation_status={result['validation_status']}"
        )

    except Exception as e:
        logger.error(f"[{run_id}] Validation error: {e}", exc_info=True)
        # Persist error activity
        await _persist_validation_activity(
            cosmos=cosmos,
            run_id=run_id,
            symbol=symbol,
            side=side,
            strike=strike,
            expiration=expiration,
            source=source,
            displayed_snapshot=displayed_snapshot,
            evaluated_snapshot=None,
            result={
                "activity": "WAIT",
                "is_alert": False,
                "validation_status": "error",
                "note": f"Validation error: {str(e)}",
                "error": str(e),
            },
        )
    finally:
        # Clean up in-flight registry
        async with _validation_lock:
            _in_flight_validations.pop(val_key, None)


async def _persist_validation_activity(
    cosmos: CosmosDBService,
    run_id: str,
    symbol: str,
    side: str,
    strike: float,
    expiration: str,
    source: str,
    displayed_snapshot: Optional[dict],
    evaluated_snapshot: Optional[dict],
    result: dict,
):
    """Persist validation activity using canonical agent schema.

    Uses the canonical activity_data from the agent (same schema as normal runs),
    augmented with minimal validation-specific metadata for tracing/status.
    """
    agent_type = "covered_call" if side == "call" else "cash_secured_put"

    # Use canonical agent activity_data as base (same schema as normal agent runs)
    activity_data = result.get("activity_data")

    if activity_data is None:
        # Fallback for error cases where agent didn't return parseable JSON
        activity_data = {
            "symbol": symbol,
            "activity": result.get("activity", "WAIT"),
            "timestamp": result.get("timestamp") or datetime.now(timezone.utc).isoformat(),
        }
        # Include note/reason from result if present
        if result.get("note"):
            activity_data["note"] = result["note"]
        if result.get("reason"):
            activity_data["reason"] = result["reason"]

    # Ensure activity_data is a mutable copy
    activity_data = dict(activity_data)

    # Augment with validation-specific metadata (minimal, non-interfering fields)
    # These are for tracing/debugging and do NOT alter the canonical activity schema
    activity_data["run_id"] = run_id
    activity_data["run_trigger"] = "best_option_validation"
    activity_data["validation_status"] = result.get("validation_status")
    activity_data["is_alert"] = result.get("is_alert", False)

    # Add rule_evaluation if not already in activity_data (include even if empty dict)
    if "rule_evaluation" not in activity_data and result.get("rule_evaluation") is not None:
        activity_data["rule_evaluation"] = result["rule_evaluation"]

    # Add trace IDs and review outputs (same as normal agent runs)
    if result.get("primary_trace_id"):
        activity_data["primary_trace_id"] = result["primary_trace_id"]
    if result.get("supervisor_view") is not None:
        activity_data["supervisor_view"] = result["supervisor_view"]
    if result.get("supervisor_trace_id"):
        activity_data["supervisor_trace_id"] = result["supervisor_trace_id"]
    if result.get("alpha_view") is not None:
        activity_data["alpha_view"] = result["alpha_view"]
    if result.get("alpha_trace_id"):
        activity_data["alpha_trace_id"] = result["alpha_trace_id"]

    # Add error if present
    if result.get("error"):
        activity_data["error"] = result["error"]

    # Add chain-aware validation fields (NEW)
    if result.get("requested_contract"):
        activity_data["requested_contract"] = result["requested_contract"]
    if result.get("selected_contract"):
        activity_data["selected_contract"] = result["selected_contract"]
    if result.get("relaxed_parameter") is not None:
        activity_data["relaxed_parameter"] = result["relaxed_parameter"]
    if result.get("comparison_rationale"):
        activity_data["comparison_rationale"] = result["comparison_rationale"]
    if result.get("selection_source"):
        activity_data["selection_source"] = result["selection_source"]
    if result.get("chain_snapshot_summary"):
        activity_data["chain_snapshot_summary"] = result["chain_snapshot_summary"]

    # Optionally attach evidence snapshots for debugging (non-canonical)
    # Store as metadata, not in the main activity schema
    if displayed_snapshot or evaluated_snapshot:
        activity_data["_validation_meta"] = {
            "source": source,
            "displayed_snapshot": displayed_snapshot,
            "evaluated_snapshot": evaluated_snapshot,
        }

    # Write activity to Cosmos using canonical schema
    cosmos.write_activity(
        symbol=symbol,
        agent_type=agent_type,
        activity_data=activity_data,
        timestamp=activity_data["timestamp"],
    )


async def get_validation_status(run_id: str, cosmos: CosmosDBService) -> Dict[str, Any]:
    """Get validation status (GET /api/best-options/validate/{run_id}).

    Returns:
        dict: {status: "in_progress"|"completed"|"not_found", ...}
    """
    # Check in-flight registry first
    async with _validation_lock:
        for val_key, val_info in _in_flight_validations.items():
            if val_info["run_id"] == run_id:
                task = val_info["task"]
                if not task.done():
                    return {
                        "status": "in_progress",
                        "run_id": run_id,
                        "started_at": val_info["started_at"],
                        "symbol": val_info["symbol"],
                        "side": val_info["side"],
                        "strike": val_info["strike"],
                        "expiration": val_info["expiration"],
                    }
                # Task is done, will be in Cosmos
                break

    # Query Cosmos for completed activity by run_id
    try:
        activity = cosmos.get_activity_by_run_id(run_id)

        if activity:
            # Return canonical activity fields (same schema as normal agent runs)
            return {
                "status": "completed",
                "run_id": run_id,
                "activity_id": activity.get("id"),
                "symbol": activity.get("symbol"),
                "agent_type": activity.get("agent_type"),
                "activity": activity.get("activity"),
                "is_alert": activity.get("is_alert", False),
                "timestamp": activity.get("timestamp"),
                # Canonical agent fields (same as normal runs)
                "reason": activity.get("reason") or activity.get("note"),
                "confidence": activity.get("confidence"),
                "underlying_price": activity.get("underlying_price"),
                "strike": activity.get("strike"),
                "expiration": activity.get("expiration"),
                "premium": activity.get("premium"),
                "iv": activity.get("iv"),
                "risk_rating": activity.get("risk_rating"),
                "risk_flags": activity.get("risk_flags"),
                "assignment_risk": activity.get("assignment_risk"),
                # Validation metadata
                "validation_status": activity.get("validation_status"),
                "run_trigger": activity.get("run_trigger"),
                # Trace/review outputs (same as normal runs)
                "rule_evaluation": activity.get("rule_evaluation"),
                "primary_trace_id": activity.get("primary_trace_id"),
                "supervisor_view": activity.get("supervisor_view"),
                "supervisor_trace_id": activity.get("supervisor_trace_id"),
                "alpha_view": activity.get("alpha_view"),
                "alpha_trace_id": activity.get("alpha_trace_id"),
                # Error if present
                "error": activity.get("error"),
                # Chain-aware validation fields (NEW)
                "requested_contract": activity.get("requested_contract"),
                "selected_contract": activity.get("selected_contract"),
                "relaxed_parameter": activity.get("relaxed_parameter"),
                "comparison_rationale": activity.get("comparison_rationale"),
                "selection_source": activity.get("selection_source"),
                # Backward compatibility
                "note": activity.get("note"),
            }

        return {
            "status": "not_found",
            "run_id": run_id,
            "message": "Validation not found. It may have expired or the run_id is invalid.",
        }

    except Exception as e:
        logger.error(f"Error querying validation status for {run_id}: {e}", exc_info=True)
        return {
            "status": "error",
            "run_id": run_id,
            "message": f"Error querying validation status: {str(e)}",
        }
