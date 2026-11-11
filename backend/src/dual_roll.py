"""Additive, advisory dual-roll contract. The legacy primary is never replaced."""

from __future__ import annotations

import json
import math
from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Literal, TypedDict
from zoneinfo import ZoneInfo

from .options_chain_filters import canonical_strike, get_contract, parse_strike_decimal
from .options_chain_view import is_candidate_eligible, usable_greek, usable_quote
from .position_contracts import PositionContractCountError, resolve_open_contract_count

AlternativeId = Literal["DEFENSIVE", "CREDIT_EXTENDED"]


class RollExpiryCheck(TypedDict):
    expiration: str
    status: Literal["available", "no_candidate", "incomplete_data"]
    reasons: list[str]
    bid: float | None
    ask: float | None
    buyback_ask: float
    quote_asof: str | None


class RollAlternative(TypedDict, total=False):
    alternative_id: AlternativeId
    status: str
    reason: str
    new_strike: float | str
    new_expiration: str
    dte: int
    roll_economics: dict
    quote: dict
    advisory_only: bool
    expiry_checks: list[RollExpiryCheck]


def _date(value) -> date | None:
    try:
        return date.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None


def _positive(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return float(value) if math.isfinite(value) and value > 0 else None
    except OverflowError:
        return None


def _fresh(contract) -> bool:
    meta = contract.get("_meta") if isinstance(contract, dict) else None
    return isinstance(meta, dict) and meta.get("stale") is False


def roll_source_available(chain) -> bool:
    if not isinstance(chain, dict) or chain.get("source_available") is False:
        return False
    status = chain.get("status")
    if "status" in chain and status not in ("ok", "success", "stale", "carried"):
        return False
    return not chain.get("error") or status in ("stale", "carried")


def _strike(value) -> Decimal | None:
    try:
        return parse_strike_decimal(value)
    except ValueError:
        return None


def _calendar_field(payload, field: str) -> date | None:
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (ValueError, TypeError):
            return None
    if not isinstance(payload, dict):
        return None
    value = payload.get(field)
    if isinstance(value, dict):
        return _date(value.get("formatted")) or _date(value.get("value")) or _epoch_date(value.get("value"))
    return _date(value) or _epoch_date(value)


def _epoch_date(value) -> date | None:
    number = _positive(value)
    if number is None:
        return None
    try:
        return datetime.fromtimestamp(number, tz=timezone.utc).date()
    except (ValueError, OverflowError, OSError):
        return None


def _unavailable(alternative_id: AlternativeId, status: str, reason: str) -> RollAlternative:
    return {"alternative_id": alternative_id, "status": status, "reason": reason}


def build_roll_alternatives(
    activity: dict,
    chain: dict,
    *,
    position: dict,
    assessment: dict,
    now: datetime,
    market_data: dict | None = None,
    source_chain: dict | None = None,
) -> dict | None:
    """Keep the selected defensive destination; search its strike by expiry, not credit.

    ``chain`` must be the agent-view, type/position/delta-filtered chain used by
    the existing roll search. Unknown quote freshness/calendar data fails closed.
    ``source_chain`` is the normalized pre-filter chain, for diagnostics ONLY:
    an excluded contract can explain a rejection but cannot become eligible.
    All credit values are estimates before commissions, not fill guarantees.
    """
    option_type = position.get("type")
    expected_action = {"call": "ROLL_UP_AND_OUT", "put": "ROLL_DOWN_AND_OUT"}.get(option_type)
    action = str(activity.get("activity", "")).upper()
    if not expected_action or not (
        action == expected_action
        or (action in ("WAIT", "CLOSE") and assessment.get("action_needed") == expected_action)
    ):
        return None

    defensive = _unavailable("DEFENSIVE", "no_defensive", "No defensive destination selected.")
    extended = _unavailable(
        "CREDIT_EXTENDED", "no_defensive", "No defensive strike; no alternative manufactured."
    )
    result = {
        "schema_version": 1,
        "preferred_alternative_id": "DEFENSIVE",
        "preference_reason": "Defensive remains the primary recommendation; extended credit is advisory only.",
        "action_target_id": "DEFENSIVE",
        "alternatives": {"DEFENSIVE": defensive, "CREDIT_EXTENDED": extended},
    }

    strike = activity.get("new_strike")
    strike_decimal = _strike(strike)
    expiry = _date(activity.get("new_expiration"))
    held_expiry = _date(position.get("expiration"))
    held_strike = _strike(position.get("strike"))
    if action != expected_action or strike_decimal is None or expiry is None:
        if activity.get("incomplete_data") or activity.get("buyback_available") is False:
            extended.update(status="incomplete_data", reason="Required execution data unavailable; no defensive strike selected.")
        return result
    defensive.update({
        "status": "available",
        "reason": "Existing defensive recommendation (unchanged).",
        "new_strike": strike,
        "new_expiration": expiry.isoformat(),
        "roll_economics": deepcopy(activity.get("roll_economics")),
        "advisory_only": False,
    })
    today = now.astimezone(ZoneInfo("America/New_York")).date()
    defensive["dte"] = (expiry - today).days
    selected = get_contract(chain, strike, expiry.isoformat(), option_type)
    current = get_contract(chain, position.get("strike"), position.get("expiration"), option_type)
    # The held contract may have been removed by candidate delta/position filters.
    if not current:
        current = chain.get("buyback_reference")
    ask = usable_quote(current, "ask")
    if (
        not roll_source_available(chain)
        or not _fresh(selected) or not is_candidate_eligible(selected)
        or usable_quote(selected, "ask") is None
        or usable_greek(selected, "delta") is None
        or not _fresh(current) or ask is None
        or not isinstance(activity.get("roll_economics"), dict)
        or activity.get("incomplete_data") or activity.get("data_error")
    ):
        defensive["status"] = "incomplete_data"
        defensive["reason"] = "Defensive destination/quotes unavailable or stale; legacy recommendation retained."
        result["alternatives"]["CREDIT_EXTENDED"] = _unavailable(
            "CREDIT_EXTENDED", "incomplete_data", "Fresh defensive and buyback quote data required."
        )
        return result
    if usable_quote(selected, "bid") > usable_quote(selected, "ask") or (
        usable_quote(current, "bid") is not None and usable_quote(current, "bid") > ask
    ):
        defensive["status"] = "incomplete_data"
        defensive["reason"] = "Crossed market quotes are not executable evidence."
        extended.update(status="incomplete_data", reason="Non-crossed defensive and buyback quotes required.")
        return result
    defensive["quote"] = deepcopy(selected)
    result["buyback_reference"] = deepcopy(current)
    if held_expiry is not None and not (held_expiry < expiry and 0 < (expiry - today).days <= 45):
        defensive["status"] = "no_candidate"
        defensive["reason"] = "Selected primary violates the existing defensive expiry policy."
        extended.update(status="no_defensive", reason="Defensive destination must satisfy its existing later-expiry and 45-DTE policy.")
        return result
    if held_expiry is None or held_strike is None or not (
        (option_type == "call" and strike_decimal > held_strike)
        or (option_type == "put" and strike_decimal < held_strike)
    ):
        extended.update(status="no_candidate", reason="Strict strike improvement over the holding is required.")
        return result

    try:
        count = resolve_open_contract_count(position)
    except PositionContractCountError as exc:
        extended.update(status="incomplete_data", reason=str(exc))
        return result
    quantity = count.contracts
    multiplier = _positive(position.get("multiplier", 100))
    if multiplier != 100:
        extended.update(status="incomplete_data", reason="Unsupported or invalid contract multiplier; existing economics require 100.")
        return result
    try:
        if not math.isfinite(multiplier * quantity):
            raise OverflowError
    except OverflowError:
        extended.update(status="incomplete_data", reason="Contract quantity cannot support finite economics.")
        return result
    defensive["roll_economics"].update({
        "quantity": quantity,
        "quantity_source": count.quantity_source,
        "quantity_warnings": list(count.warnings),
        "multiplier": multiplier,
        "before_commissions": True,
        "estimated": True,
    })
    defensive_credit = defensive["roll_economics"].get("net_credit")
    if isinstance(defensive_credit, (int, float)) and math.isfinite(defensive_credit):
        defensive["roll_economics"].update({
            "net_credit_per_contract": defensive_credit * multiplier,
            "net_credit_total": defensive_credit * multiplier * quantity,
        })

    earnings = assessment.get("earnings_analysis") or activity.get("earnings_analysis") or {}
    market_data = market_data or {}
    overview = market_data.get("overview") or {}
    if isinstance(overview, str):
        try:
            overview = json.loads(overview)
        except (ValueError, TypeError):
            overview = {}
    fundamentals = overview.get("fundamentals") if isinstance(overview, dict) else {}
    earnings_date = (
        _calendar_field(fundamentals, "earnings_release_next_date_fq")
        or _date(earnings.get("next_earnings_date"))
    )
    flags = set(assessment.get("risk_flags") or []) | set(activity.get("risk_flags") or [])
    if earnings_date is None or earnings_date < today:
        extended.update(status="incomplete_data", reason="Upcoming earnings calendar unavailable.")
        return result
    constraints = assessment.get("profit_optimization_constraints") or {}
    ex_dividend = (
        _calendar_field(market_data.get("dividends"), "ex_dividend_date_recent")
        or _date(constraints.get("next_ex_div_date"))
    )
    result["safety_evidence"] = {
        "next_earnings_date": earnings_date.isoformat(),
        "next_ex_div_date": ex_dividend.isoformat() if ex_dividend else None,
        "risk_flags": sorted(flags),
    }
    if flags & {"catalyst_pending", "fundamental_deterioration", "ex_dividend_risk"}:
        extended.update(status="no_candidate", reason="Existing event/fundamental safety gate blocks extension.")
        return result

    side = "calls" if option_type == "call" else "puts"
    expiry_keys = set(chain.get(side) or {}) | set((source_chain or {}).get(side) or {})
    expirations = sorted({parsed for key in expiry_keys if (parsed := _date(key)) is not None})
    later = [exp for exp in expirations if exp > max(held_expiry, expiry) and 0 < (exp - today).days <= 90]
    if not later:
        extended.update(status="incomplete_data", reason="No later expiry data within 90 DTE; eligibility cannot be established.")
        return result

    missing_data = False
    checks: list[RollExpiryCheck] = []
    extended["expiry_checks"] = checks
    for candidate_expiry in later:
        contract = get_contract(chain, strike, candidate_expiry.isoformat(), option_type)
        observed = contract if contract is not None else get_contract(
            source_chain, strike, candidate_expiry.isoformat(), option_type,
        )
        meta = observed.get("_meta") if isinstance(observed, dict) else None
        quote_asof = meta.get("quote_asof") if isinstance(meta, dict) else None
        check: RollExpiryCheck = {
            "expiration": candidate_expiry.isoformat(),
            "status": "no_candidate",
            "reasons": [],
            "bid": usable_quote(observed, "bid"),
            "ask": usable_quote(observed, "ask"),
            "buyback_ask": ask,
            "quote_asof": quote_asof if isinstance(quote_asof, str) else None,
        }
        checks.append(check)
        gap = (candidate_expiry - earnings_date).days
        # Existing pre-earnings buffer and post-earnings stabilization rules.
        # The conditional 8–13-day exception needs evidence we cannot infer.
        if -2 <= gap <= 7:
            check["reasons"].append("Expiry falls inside the existing earnings safety buffer.")
            continue
        if 8 <= gap <= 13:
            missing_data = True
            check["status"] = "incomplete_data"
            check["reasons"].append("Post-earnings 8–13-day exception requires evidence not established by this search.")
            continue
        if option_type == "call" and ex_dividend and today <= ex_dividend <= candidate_expiry:
            check["reasons"].append("Expiry crosses the existing ex-dividend safety gate.")
            continue
        if observed is None:
            check["status"] = "incomplete_data"
            check["reasons"].append("Exact defensive strike is absent from the source chain for this expiry.")
        else:
            if not _fresh(observed):
                check["reasons"].append("Quote is stale or freshness is unknown.")
            if usable_quote(observed, "bid") is None:
                check["reasons"].append("Executable new bid is missing or invalid.")
            if usable_quote(observed, "ask") is None:
                check["reasons"].append("New ask is missing or invalid.")
            if (
                check["bid"] is not None and check["ask"] is not None
                and check["bid"] > check["ask"]
            ):
                check["reasons"].append("Bid exceeds ask (crossed market).")
            if not is_candidate_eligible(observed):
                check["reasons"].append("Existing bid/open-interest/Greek-validity eligibility gate fails.")
            if usable_greek(observed, "delta") is None:
                check["reasons"].append("Valid delta is unavailable.")
            if check["reasons"]:
                check["status"] = "incomplete_data"
            if contract is None:
                check["reasons"].append("Excluded by the existing position/delta candidate filters; source quote is diagnostic only.")
        if check["reasons"]:
            missing_data |= check["status"] == "incomplete_data"
            continue
        bid = usable_quote(contract, "bid")
        credit = bid - ask
        if not math.isfinite(credit) or credit <= 0:
            check["reasons"].append("New bid minus held ask is not a strictly positive finite credit.")
            continue
        per_contract = credit * multiplier
        total_credit = per_contract * quantity
        if not math.isfinite(per_contract) or not math.isfinite(total_credit):
            missing_data = True
            check["status"] = "incomplete_data"
            check["reasons"].append("Contract quantity cannot support finite credit economics.")
            continue
        check["status"] = "available"
        check["reasons"].append("Nearest eligible same-strike expiry with positive executable estimated credit.")
        extended.update({
            "status": "available",
            "reason": "Nearest later eligible expiry at the defensive strike; estimated credit before commissions, not a guaranteed fill.",
            "new_strike": strike,
            "new_expiration": candidate_expiry.isoformat(),
            "dte": (candidate_expiry - today).days,
            "advisory_only": True,
            "quote": deepcopy(contract),
            "roll_economics": {
                "buyback_cost": ask,
                "new_premium": bid,
                "net_credit": credit,
                "net_credit_per_contract": per_contract,
                "net_credit_total": total_credit,
                "quantity": quantity,
                "quantity_source": count.quantity_source,
                "quantity_warnings": list(count.warnings),
                "multiplier": multiplier,
                "before_commissions": True,
                "estimated": True,
            },
        })
        return result
    rejection_summary = " ".join(dict.fromkeys(
        reason for check in checks for reason in check["reasons"]
    ))
    extended.update(
        status="incomplete_data" if missing_data else "no_candidate",
        reason=f"No eligible executable same-strike credit found within 90 DTE. {rejection_summary}",
    )
    return result


def build_roll_pair_context(
    chain: dict, *, position: dict, assessment: dict, now: datetime, market_data: dict,
    source_chain: dict | None = None,
) -> str:
    """Give the existing principal call deterministic pair metadata before selection.

    This does not select or relax the defensive trade. Each row is keyed by its
    own baseline; the final selection is revalidated by build_roll_alternatives.
    """
    option_type = position.get("type")
    expected = {"call": "ROLL_UP_AND_OUT", "put": "ROLL_DOWN_AND_OUT"}.get(option_type)
    if assessment.get("action_needed") != expected or expected is None:
        return ""
    today = now.astimezone(ZoneInfo("America/New_York")).date()
    current = chain.get("buyback_reference") or get_contract(
        chain, position.get("strike"), position.get("expiration"), option_type,
    )
    ask = usable_quote(current, "ask")
    rows = []
    for expiry_key, strikes in (chain.get("calls" if option_type == "call" else "puts") or {}).items():
        expiration = _date(expiry_key)
        if expiration is None or not 1 <= (expiration - today).days <= 45:
            continue
        for strike_key, contract in strikes.items():
            try:
                strike = canonical_strike(strike_key)
            except ValueError:
                continue
            bid = usable_quote(contract, "bid")
            if strike is None or bid is None or ask is None or not is_candidate_eligible(contract):
                continue
            primary = {
                "activity": expected, "new_strike": strike, "new_expiration": expiration.isoformat(),
                "roll_economics": {"buyback_cost": ask, "new_premium": bid, "net_credit": bid - ask},
            }
            pair = build_roll_alternatives(
                primary, chain, position=position, assessment=assessment, now=now, market_data=market_data,
                source_chain=source_chain,
            )
            alternative = pair["alternatives"]["CREDIT_EXTENDED"]
            rows.append({
                "defensive_baseline": {"new_strike": strike, "new_expiration": expiration.isoformat()},
                "CREDIT_EXTENDED": {
                    key: value for key, value in alternative.items() if key not in ("quote", "expiry_checks")
                },
            })
    return (
        "\n\nDUAL-ROLL PAIRS (deterministic advisory metadata, keyed by DEFENSIVE baseline)\n"
        "Select DEFENSIVE under the unchanged existing tier/DTE/event policy FIRST. "
        "Do not use these extensions to change that selection. Principal preference: DEFENSIVE. "
        "Application code revalidates and persists the matching CREDIT_EXTENDED afterward.\n"
        + json.dumps(rows, default=str)
    )


def available_alternative_ids(activity: dict) -> list[str]:
    block = activity.get("roll_alternatives") or {}
    return [
        key for key in ("DEFENSIVE", "CREDIT_EXTENDED")
        if (block.get("alternatives", {}).get(key) or {}).get("status") == "available"
    ]


def scope_supervisor_review(review: dict, activity: dict) -> dict:
    """Never fan out a legacy unscoped verdict to multiple destinations."""
    if not activity.get("roll_alternatives"):
        return review
    supplied = review.get("alternative_reviews")
    supplied = supplied if isinstance(supplied, dict) else {}
    scoped = {}
    for key in available_alternative_ids(activity):
        item = supplied.get(key)
        if (
            isinstance(item, dict)
            and item.get("net_assessment") in ("ORIGINAL_HOLDS", "RECONSIDER")
            and item.get("challenge_strength") in ("WEAK", "MODERATE", "STRONG")
            and isinstance(item.get("counter_arguments"), list)
            and all(
                isinstance(argument, dict)
                and isinstance(argument.get("point"), str)
                and isinstance(argument.get("data_support"), str)
                for argument in item["counter_arguments"]
            )
            and isinstance(item.get("one_liner"), str)
            and bool(item["one_liner"].strip())
        ):
            scoped[key] = {**item, "review_status": "complete"}
        else:
            scoped[key] = {"review_status": "incomplete", "one_liner": "Scoped audit unavailable."}
    review["alternative_reviews"] = scoped
    review["legacy_scope"] = "DEFENSIVE"
    if scoped.get("DEFENSIVE", {}).get("review_status") == "complete":
        for field in ("challenge_strength", "counter_arguments", "net_assessment", "one_liner"):
            review[field] = scoped["DEFENSIVE"][field]
    else:
        review.update({
            "challenge_strength": "MODERATE",
            "counter_arguments": [],
            "net_assessment": "REVIEW_INCOMPLETE",
            "one_liner": "Scoped defensive audit unavailable; no approval inferred.",
        })
    return review


def _validate_additional_alpha(review: dict, activity: dict, chain: dict | None) -> tuple[dict | None, str | None]:
    from .alpha_instructions import RELAXABLE_PARAMETERS

    block = activity["roll_alternatives"]
    baseline_id = review.get("baseline_alternative_id")
    baseline = (block.get("alternatives") or {}).get(baseline_id) if isinstance(baseline_id, str) else None
    if not baseline or baseline.get("status") != "available":
        return None, "Additional proposal must identify an available baseline alternative ID."
    alternative = review["alternative"]
    if (
        review.get("relaxed_parameter") not in RELAXABLE_PARAMETERS
        or not isinstance(alternative, dict)
        or not all(isinstance(value, str) and value.strip() for value in (
            review.get("parameter_detail"), alternative.get("rationale"), alternative.get("trade_off"),
        ))
    ):
        return None, "Additional proposal requires exactly one existing soft parameter and explicit trade-off."
    evidence = block.get("safety_evidence") or {}
    if not chain or not evidence.get("next_earnings_date"):
        return None, "Additional proposal lacks authoritative chain/calendar evidence."
    if set(evidence.get("risk_flags") or []) & {"catalyst_pending", "fundamental_deterioration", "ex_dividend_risk"}:
        return None, "Existing event/fundamental safety gates block the additional proposal."
    try:
        now = datetime.fromisoformat(str(activity.get("timestamp")).replace("Z", "+00:00"))
        today = now.astimezone(ZoneInfo("America/New_York")).date()
        expiration = _date(alternative.get("expiration"))
        held_expiry = _date(activity.get("current_expiration"))
        strike = _strike(alternative.get("strike"))
        held_strike = _strike(activity.get("current_strike"))
        call = activity.get("activity") == "ROLL_UP_AND_OUT"
        if (
            expiration is None or held_expiry is None or expiration <= held_expiry
            or not 1 <= (expiration - today).days <= 45
            or strike is None or held_strike is None
            or not (strike > held_strike if call else strike < held_strike)
        ):
            return None, "Additional proposal violates direction/expiry or the general 45-DTE hard cap."
        ex_dividend = _date(evidence.get("next_ex_div_date"))
        if call and ex_dividend and today <= ex_dividend <= expiration:
            return None, "Additional proposal crosses an ex-dividend safety gate."
        underlying = _positive(activity.get("underlying_price"))
        if underlying is None:
            return None, "Underlying price unavailable for additional proposal validation."
        baseline_strike = _strike(baseline["new_strike"])
        if _strike(float(strike)) != strike or _strike(float(baseline_strike)) != baseline_strike:
            return None, "Additional proposal cannot preserve exact strike identity through legacy safety validation."
        from .contract_validation_integration import _validate_alpha_alternative
        valid, reason, contract = _validate_alpha_alternative(
            chain, "call" if call else "put",
            float(baseline_strike), baseline["new_expiration"],
            alternative, activity.get("symbol_category") or "balanced",
            underlying, evidence["next_earnings_date"], now,
        )
        if not valid:
            return None, reason
        ask = usable_quote(block.get("buyback_reference"), "ask")
        bid = usable_quote(contract, "bid")
        new_ask = usable_quote(contract, "ask")
        economics = baseline.get("roll_economics") or {}
        quantity = _positive(economics.get("quantity"))
        multiplier = _positive(economics.get("multiplier"))
        if (
            not _fresh(contract) or not is_candidate_eligible(contract)
            or ask is None or bid is None or new_ask is None or bid > new_ask
            or quantity is None or multiplier != 100
        ):
            return None, "Additional proposal requires fresh executable quotes, liquidity and exact quantity."
        credit = bid - ask
        total = credit * multiplier * quantity
        if credit < -1 or not math.isfinite(total):
            return None, "Additional proposal violates the existing defensive debit tier."
        return {
            **alternative, "baseline_alternative_id": baseline_id,
            "advisory_only": True, "status": "available",
            "strike": alternative["strike"], "expiration": expiration.isoformat(),
            "dte": (expiration - today).days, "premium": bid,
            "delta": usable_greek(contract, "delta"),
            "roll_economics": {
                "buyback_cost": ask, "new_premium": bid, "net_credit": credit,
                "net_credit_total": total, "quantity": quantity, "multiplier": multiplier,
                "before_commissions": True, "estimated": True,
            },
        }, None
    except (ValueError, TypeError, KeyError, OverflowError):
        return None, "Additional proposal could not be validated."


def scope_alpha_review(review: dict, activity: dict, *, chain: dict | None = None) -> dict:
    if not activity.get("roll_alternatives"):
        return review
    for field in ("additional_alternative", "additional_proposal_status", "additional_proposal_reason"):
        review.pop(field, None)
    ids = available_alternative_ids(activity)
    preferred = review.get("preferred_alternative_id")
    comparison = review.get("comparison_rationale")
    complete = preferred in ids and isinstance(comparison, str) and bool(comparison.strip())
    review["comparison_status"] = "complete" if complete else "incomplete"
    review["preferred_alternative_id"] = preferred if complete else None
    alternative = review.get("alternative")
    if alternative:
        additional, rejection = _validate_additional_alpha(review, activity, chain)
        review["additional_proposal_status"] = "available" if additional else "rejected"
        if additional:
            review["additional_alternative"] = additional
        else:
            review["additional_proposal_reason"] = rejection
    review["alternative"] = {}
    if not alternative:
        review["relaxed_parameter"] = "none"
    review["legacy_scope"] = "COMPARISON"
    review.setdefault("opportunity_strength", "NONE")
    if complete:
        review["one_liner"] = f"Prefers {preferred}: {comparison}"
    else:
        review["opportunity_strength"] = "NONE"
        review["one_liner"] = "Scoped dual-roll comparison unavailable; no preference inferred."
    return review


def primary_roll_target(activity: dict) -> tuple:
    """Dual-roll apply actions always target the original defensive contract."""
    if activity.get("roll_alternatives"):
        block = activity["roll_alternatives"]
        defensive = (block.get("alternatives") or {}).get("DEFENSIVE") or {}
        if (
            block.get("action_target_id") != "DEFENSIVE"
            or defensive.get("status") != "available"
            or activity.get("new_strike") != defensive.get("new_strike")
            or activity.get("new_expiration") != defensive.get("new_expiration")
        ):
            return None, None
        # The existing position writer accepts floats; never round a dual-roll
        # destination into a different exact chain strike on its way there.
        try:
            if _strike(float(activity["new_strike"])) != _strike(activity["new_strike"]):
                return None, None
        except (ValueError, TypeError, OverflowError):
            return None, None
        return activity.get("new_strike"), activity.get("new_expiration")
    return (
        activity.get("strike") or activity.get("new_strike") or activity.get("current_strike"),
        activity.get("expiration") or activity.get("new_expiration") or activity.get("current_expiration"),
    )


def is_designated_extended_roll(activity: dict) -> bool:
    block = activity.get("roll_alternatives") or {}
    alt = (block.get("alternatives") or {}).get("CREDIT_EXTENDED") or {}
    defensive = (block.get("alternatives") or {}).get("DEFENSIVE") or {}
    expiration = _date(alt.get("new_expiration"))
    defensive_expiration = _date(defensive.get("new_expiration"))
    held_expiration = _date(activity.get("current_expiration"))
    strike = _strike(alt.get("new_strike"))
    held_strike = _strike(activity.get("current_strike"))
    call = activity.get("activity") == "ROLL_UP_AND_OUT"
    return (
        activity.get("roll_alternative_id") == "CREDIT_EXTENDED"
        and activity.get("activity") in ("ROLL_UP_AND_OUT", "ROLL_DOWN_AND_OUT")
        and block.get("action_target_id") == "DEFENSIVE"
        and alt.get("status") == "available"
        and defensive.get("status") == "available"
        and strike == _strike(defensive.get("new_strike"))
        and strike is not None and held_strike is not None
        and (strike > held_strike if call else strike < held_strike)
        and expiration is not None and defensive_expiration is not None and held_expiration is not None
        and expiration > max(defensive_expiration, held_expiration)
        and activity.get("new_strike") == alt.get("new_strike")
        and activity.get("new_expiration") == alt.get("new_expiration")
        and activity.get("roll_economics") == alt.get("roll_economics")
    )
