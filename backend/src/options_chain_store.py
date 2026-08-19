"""Sharded CosmosDB persistence for the options chain cache.

One document per ``(symbol, expiration)`` shard: ``id = optchain_{SYMBOL}_{YYYYMMDD}``.
Storage follows the repository's existing ``symbols`` container / partition
key ``/symbol`` / ``doc_type`` hybrid-document pattern already used for
``enrichment_history``, ``price_forecast``, etc. (see ``src/cosmos_db.py``) —
no new container is provisioned.

Shard body::

    {
      "id": "optchain_AAPL_20260821",
      "symbol": "AAPL",
      "doc_type": "options_chain",
      "expiration": "20260821",
      "schema_version": 3,
      "calls": {"<strike>": {contract, ...}, ...},
      "puts":  {"<strike>": {contract, ...}, ...},
      "underlying_price": 185.32,
      "contract_count": 42,
      "updated_at": "<ISO 8601>",
      "_content_hash": "<sha256 of the market-observable calls+puts payload>",
    }

``schema_version`` 3 (Livingston, 2026-08-18 D1/D3 revision) adds
``underlying_price`` to every shard so a cold ``hydrate()`` can restore the
top-level ``underlying_price``/``timestamp`` the schema documents (D3) and so
a subsequent refresh's ``_extract_underlying_price`` fallback has a real
value even before any live source responds this cycle. No migration is
required — ``hydrate()`` never gates on ``schema_version`` being present or
matching, so v2 shards (missing ``underlying_price``) still read back fine,
just without that one field.

``schema_version`` 4 (Livingston, 2026-08-19, Danny's "Zero-Free Agent-
Facing Option Chains" decision, §4.4) marks shards written *after*
``options_chain_merge.recompute_derived`` stopped fabricating a ``mid: 0.0``
mark or intrinsic-only Greeks for invalid inputs (Rule Z3). Shards written
before that fix (any ``schema_version < 4``, including absent) may still
carry those fabricated derived values. Rather than gate on the version
number, ``hydrate()`` unconditionally passes every contract it reads through
``normalize_persisted_v1_to_v2()`` — a no-op on an already-correct contract,
so no un-normalized shard is *ever* served, even before the one-time batch
repair script (``scripts/repair_options_chain_shards.py``) has run. Rule
Z11 (no destructive migration) applies: only the derived fields ``mid`` and
the five Greeks are ever nulled; every observed field (``bid``/``ask``/
``lastPrice``/``iv``/``volume``/``openInterest``/``lastTradeDate``/
``inTheMoney``) is untouched, byte-for-byte, including a legitimate
``bid: 0.0``. The lazy hydrate-time pass never writes anything back — only
the repair script persists a normalized shard, via the same ETag CAS path
``persist()`` already uses.

Write protocol: read -> reconcile -> replace with ETag +
``MatchConditions.IfNotModified``. Reconciliation is a plain, store-owned
contract-level union between whatever is *currently* persisted (which may be
newer than what this refresh cycle hydrated as "prior" at its start — e.g.
another process just wrote it) and what this cycle computed (``chain``,
already fully merged and recomputed by
``OptionsChainCache._refresh_locked`` via ``options_chain_merge``): for a
contract on only one side, keep it verbatim; for a contract on both sides,
keep whichever has the more recent ``_meta.last_seen``/``quote_asof``
(ties favour this cycle's own result). Deliberately **not**
``options_chain_merge.merge_prior``: that function's contract is "apply this
cycle's *live source* observations onto a prior accumulated state" and
manufactures fresh ``_meta`` / drops derived fields accordingly (`mid` and
the five greeks) — correct for its own call site in
``OptionsChainCache._refresh_locked``, wrong here where *both* sides passed
to the store are already fully-formed, already-recomputed accumulated
contracts (the store's D1/D2 fix: this module never imports or calls
``options_chain_merge`` at all). Reconciliation here only ever adds
contracts and only ever prefers the more-recent side, so it is monotone and
safe to retry. On a 409/412 the shard is re-read and re-reconciled up to
``_MAX_CAS_ATTEMPTS`` times; if it still conflicts the shard is skipped for
this cycle with a WARNING — no in-memory data is ever lost because of a
persistence conflict. Only shards whose *market-observable* content actually
changed are written (D5): the content hash deliberately excludes
``_meta.last_seen``/``_meta.quote_asof`` (which the cache's merge cycle
advances on every observation regardless of whether any field's value
actually changed), so a refresh that observes byte-identical quotes is a
true no-op write, bounding RU cost the way the hash was always meant to.

Persistence is always best-effort: every public method catches its own
exceptions, logs, and returns a safe/empty result rather than raising, so a
Cosmos outage never blocks a refresh or destroys good in-memory data. When
constructed without an available container (``enabled=False`` or no
container reachable), every public method is a cheap no-op.

Retention: ``prune_expired`` deletes a shard only once its expiration date
(America/New_York) is more than ``expired_shard_grace_days`` (default 7) in
the past — a separate, longer horizon than the cache's serving-side prune
(``options_chain_merge.prune_by_expiration``, same-day cutoff), so post-expiry
assignment reconciliation can still read the final quotes for a while. Cache
TTL never drives deletion here; only real contract expiration does.
"""

import copy
import hashlib
import json
import logging
import math
import threading
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_DOC_TYPE = "options_chain"
_SCHEMA_VERSION = 4
_DEFAULT_EXPIRED_SHARD_GRACE_DAYS = 7
_DEFAULT_MAX_SHARD_BYTES = 1_600_000
_MAX_CAS_ATTEMPTS = 3
# Contracts eligible for the size escape valve must additionally be unseen
# for at least this many days (Danny's design §4.5).
_SIZE_VALVE_UNSEEN_DAYS = 30
# P0 retry/backoff for the shared store singleton's construction (Danny's
# zero-free decision §4.1) — default `options_chain_cache.persistence_retry_
# seconds`, capped exponential backoff so a persistently-down Cosmos doesn't
# retry every call.
_DEFAULT_PERSISTENCE_RETRY_SECONDS = 300
_MAX_RETRY_BACKOFF_SECONDS = 3600
_GREEK_FIELDS = ("delta", "gamma", "theta", "vega", "rho")


def _shard_id(symbol: str, exp_key: str) -> str:
    return f"optchain_{symbol}_{exp_key}"


# Provenance timestamp fields that the cache's merge cycle advances on every
# *observation* of a contract, regardless of whether any of its actual field
# values changed (see ``options_chain_merge._merge_prior_contract``:
# ``last_seen`` always advances for a still-listed contract, and
# ``quote_asof`` advances whenever the quote group is merely re-supplied,
# not only when it differs). Excluded from the content hash (D5) so the
# write-skip guard reflects *market-observable* change, not "did a refresh
# cycle run" — otherwise it can never fire in production.
_VOLATILE_META_FIELDS = ("last_seen", "quote_asof")


def _hashable_contract(contract: Any) -> Any:
    """Strip the always-advancing provenance timestamps from one contract
    before hashing, so two cycles that observed identical market data hash
    identically even though ``_meta`` legitimately records that a fresh
    observation happened."""
    if not isinstance(contract, dict):
        return contract
    meta = contract.get("_meta")
    if not isinstance(meta, dict):
        return contract
    stripped = dict(contract)
    stripped_meta = dict(meta)
    for field in _VOLATILE_META_FIELDS:
        stripped_meta.pop(field, None)
    stripped["_meta"] = stripped_meta
    return stripped


def _content_hash(calls: dict, puts: dict, underlying_price: Optional[float] = None) -> str:
    """Deterministic hash of a shard's *market-observable* payload, used to
    skip writes when nothing actually changed (bounds RU cost — D5). Strips
    the volatile ``_meta`` timestamps (see ``_VOLATILE_META_FIELDS``) from
    every contract first so an unchanged refresh cycle (same bid/ask/iv/...,
    same recomputed greeks, same underlying price) produces the same hash
    as the previous cycle even though provenance timestamps legitimately
    advanced."""
    hashable_calls = {
        strike_key: _hashable_contract(contract) for strike_key, contract in (calls or {}).items()
    }
    hashable_puts = {
        strike_key: _hashable_contract(contract) for strike_key, contract in (puts or {}).items()
    }
    payload = json.dumps(
        {"calls": hashable_calls, "puts": hashable_puts, "underlying_price": underlying_price},
        sort_keys=True, default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _contract_last_touch(contract: Any) -> str:
    """Best-effort recency key for reconciliation: prefer
    ``_meta.last_seen``, fall back to ``_meta.quote_asof``, else the empty
    string (treated as "oldest" — a contract with no provenance timestamp
    never outranks one that has one)."""
    if not isinstance(contract, dict):
        return ""
    meta = contract.get("_meta")
    if not isinstance(meta, dict):
        return ""
    return str(meta.get("last_seen") or meta.get("quote_asof") or "")


def _reconcile_bucket(stored_bucket: dict, want_bucket: dict) -> dict:
    """Contract-level union between what is *currently* persisted
    (``stored_bucket``) and what this cycle's caller wants written
    (``want_bucket``), for one side (calls/puts) of one shard.

    Both sides are already fully-formed, already-recomputed accumulated
    contracts (mid/greeks baked in, ``_meta`` already correct) — this is a
    verbatim pick, never a field-by-field remerge, so it can never drop a
    derived field (D1) or manufacture provenance (D2). A contract present on
    only one side is kept as-is; a contract present on both is taken from
    whichever side's ``_meta`` shows the more recent observation (ties
    favour ``want_bucket``, i.e. this cycle's own result). Never drops a
    contract from either side, so this is monotone/safe under CAS retry."""
    stored_bucket = stored_bucket or {}
    want_bucket = want_bucket or {}
    merged = dict(stored_bucket)
    for strike_key, want_contract in want_bucket.items():
        stored_contract = stored_bucket.get(strike_key)
        if stored_contract is None or _contract_last_touch(want_contract) >= _contract_last_touch(stored_contract):
            merged[strike_key] = want_contract
        else:
            merged[strike_key] = stored_contract
    return merged


def _is_usable_price(value: Any) -> bool:
    """Local, minimal finite-and-positive check. Deliberately not imported
    from ``options_chain_view.py`` (its internals are Linus's — this module
    only ever calls that module's five frozen public functions, and this
    predicate isn't one of them)."""
    if value is None or isinstance(value, bool):
        return False
    if not isinstance(value, (int, float)):
        return False
    return math.isfinite(value) and value > 0


def normalize_persisted_v1_to_v2(contract: Any) -> Any:
    """Mandatory, lazy, read-time normalization (Danny's zero-free decision
    §4.4.1, Rule Z3/Z11) applied to every contract ``hydrate()`` returns,
    regardless of the shard's stored ``schema_version`` — so no
    un-normalized shard is ever served, even before
    ``scripts/repair_options_chain_shards.py`` has had a chance to run
    against it.

    Nulls *only* the two derived-field defects the pre-fix
    ``options_chain_merge.recompute_derived`` could fabricate:

    * All five Greeks together, whenever they are not genuinely valid —
      either an explicit ``_meta.greeks_valid is False`` (the common case:
      the old code still set this flag honestly, it was just ignored
      downstream), or a contract with no ``_meta`` at all whose ``iv`` is
      itself missing/invalid (a shard old enough to predate
      ``greeks_valid`` tracking entirely — the decision's explicit
      migration-only rule; note this is *stricter* than
      ``options_chain_view.usable_greek``'s "no meta trusts the raw value"
      rule, which is about a hand-built fixture, not real legacy data).
    * ``mid``, whenever neither ``bid`` nor ``ask`` was a usable (finite,
      positive) quote — the exact "nothing usable -> 0.0" fabrication
      ``robust_mid`` documented and ``robust_mid_optional`` fixed going
      forward.

    Every observed field (``bid``/``ask``/``lastPrice``/``iv``/``volume``/
    ``openInterest``/``lastTradeDate``/``inTheMoney``) is untouched,
    byte-for-byte — including a legitimate ``bid: 0.0`` (Z11: no
    destructive migration). Pure: never mutates ``contract``, returns a new
    dict. Total: never raises; a malformed/non-dict input is returned
    unchanged. Idempotent: re-applying to an already-normalized (or
    already-correct) contract is a no-op.
    """
    if not isinstance(contract, dict):
        return contract
    try:
        meta_raw = contract.get("_meta")
        has_meta = isinstance(meta_raw, dict)
        meta = dict(meta_raw) if has_meta else {}

        if has_meta:
            needs_null_greeks = meta.get("greeks_valid") is False
        else:
            needs_null_greeks = not _is_usable_price(contract.get("iv"))

        result = dict(contract)
        if needs_null_greeks:
            for greek in _GREEK_FIELDS:
                result[greek] = None
            meta["greeks_valid"] = False
            meta["greeks_asof"] = None
            result["_meta"] = meta

        if not _is_usable_price(contract.get("bid")) and not _is_usable_price(contract.get("ask")):
            result["mid"] = None

        return result
    except Exception:
        return contract


class OptionsChainStore:
    """CosmosDB-backed sharded persistence for one symbol's options chain.

    Degrades to a fully inert (memory-only) no-op when Cosmos is
    unavailable or ``enabled=False`` — callers never need to branch on
    availability; every method returns a safe/empty result.
    """

    def __init__(
        self,
        container: Any = None,
        *,
        enabled: bool = True,
        expired_shard_grace_days: int = _DEFAULT_EXPIRED_SHARD_GRACE_DAYS,
        max_shard_bytes: int = _DEFAULT_MAX_SHARD_BYTES,
    ) -> None:
        self._enabled = enabled
        self._container = container if enabled else None
        self.expired_shard_grace_days = expired_shard_grace_days
        self.max_shard_bytes = max_shard_bytes
        self._lock = threading.Lock()
        self._persist_errors = 0
        self._last_persist_error_at: Optional[str] = None
        self._last_persist_error: Optional[str] = None
        self._writes_ok = 0
        self._writes_failed = 0
        if enabled and container is None:
            logger.info(
                "OptionsChainStore: constructed without a Cosmos container — "
                "persistence disabled, memory-only chain caching in effect."
            )

    # ------------------------------------------------------------------
    def is_available(self) -> bool:
        """True if a usable Cosmos container is wired up."""
        return self._enabled and self._container is not None

    def _record_error(self, symbol: str, exc: Exception) -> None:
        with self._lock:
            self._persist_errors += 1
            self._last_persist_error_at = datetime.now(timezone.utc).isoformat()
            self._last_persist_error = f"{symbol}: {exc}"
        logger.warning("%s: options chain persistence error: %s", symbol, exc)

    def stats(self) -> dict:
        """This instance's own persistence health — write outcomes, errors,
        and configuration. Merged into ``OptionsChainCache.stats()`` /
        ``GET /api/health/options-chain`` alongside the shared singleton's
        construction/retry health (``get_persistence_health()``, §4.1/§4.2 —
        a *different* concern: this instance already exists; that tracks
        whether/when the process-wide singleton could be built at all)."""
        with self._lock:
            return {
                "available": self.is_available(),
                # `enabled`: alias of `available` under the exact key name
                # Danny's zero-free decision §4.2 specifies for the merged
                # health block (`GET /api/health/options-chain`).
                "enabled": self.is_available(),
                # `configured`: persistence was turned ON in config,
                # independent of whether a container is currently reachable
                # (that's `available`) — lets an operator distinguish
                # "deliberately memory-only" from "should be connected but
                # isn't right now".
                "configured": self._enabled,
                "persist_errors": self._persist_errors,
                "last_persist_error_at": self._last_persist_error_at,
                "last_persist_error": self._last_persist_error,
                "expired_shard_grace_days": self.expired_shard_grace_days,
                "max_shard_bytes": self.max_shard_bytes,
                "writes_ok": self._writes_ok,
                "writes_failed": self._writes_failed,
            }

    # ------------------------------------------------------------------
    # Hydrate
    # ------------------------------------------------------------------
    def hydrate(self, symbol: str) -> Optional[dict]:
        """Read all persisted shards for ``symbol`` and reassemble a chain
        dict ``{symbol, timestamp, underlying_price, calls: {...}, puts:
        {...}}`` — the same top-level shape ``OPTIONS_CHAIN_SCHEMA_DESCRIPTION``
        documents (D3). ``timestamp``/``underlying_price`` are reconstructed
        from the most-recently-``updated_at`` shard (schema_version >= 3);
        legacy shards that predate ``underlying_price`` simply leave it
        absent rather than failing — hydrate never gates on schema_version.

        Returns ``None`` when unavailable, when there are no persisted
        shards, or on any read failure — never raises.
        """
        if not self.is_available():
            return None
        try:
            items = list(self._container.query_items(
                query="SELECT * FROM c WHERE c.symbol=@s AND c.doc_type=@t",
                parameters=[
                    {"name": "@s", "value": symbol},
                    {"name": "@t", "value": _DOC_TYPE},
                ],
                partition_key=symbol,
            ))
        except Exception as exc:
            self._record_error(symbol, exc)
            return None

        if not items:
            return None

        chain: Dict[str, Any] = {"symbol": symbol, "calls": {}, "puts": {}}
        latest_updated_at: Optional[str] = None
        underlying_price: Optional[float] = None
        for shard in items:
            exp_key = shard.get("expiration")
            if not exp_key:
                continue
            calls = shard.get("calls") or {}
            puts = shard.get("puts") or {}
            # Mandatory lazy migration (Rule Z3/Z11, §4.4.1): every contract
            # is normalized on the way out regardless of the shard's stored
            # schema_version, so a chain read back is never stale on the
            # derived-field-fabrication defect even before the batch repair
            # script has visited this shard.
            calls = {
                strike: normalize_persisted_v1_to_v2(contract)
                for strike, contract in calls.items()
            }
            puts = {
                strike: normalize_persisted_v1_to_v2(contract)
                for strike, contract in puts.items()
            }
            if calls:
                chain["calls"][exp_key] = calls
            if puts:
                chain["puts"][exp_key] = puts
            updated_at = shard.get("updated_at")
            if updated_at and (latest_updated_at is None or updated_at > latest_updated_at):
                latest_updated_at = updated_at
                if shard.get("underlying_price") is not None:
                    underlying_price = shard.get("underlying_price")
        if latest_updated_at is not None:
            chain["timestamp"] = latest_updated_at
        if underlying_price is not None:
            chain["underlying_price"] = underlying_price
        return chain

    # ------------------------------------------------------------------
    # Persist
    # ------------------------------------------------------------------
    def persist(self, symbol: str, chain: dict, *, now: Optional[datetime] = None) -> dict:
        """Persist ``chain``, one document per expiration ("shard").

        Only shards whose market-observable content actually changed are
        written (D5). Cross-process conflicts (ETag 409/412) are resolved by
        re-reading the shard and re-reconciling (``_reconcile_bucket`` — a
        store-owned, monotone verbatim union; see the module docstring for
        why this is deliberately not ``options_chain_merge.merge_prior``),
        retried up to ``_MAX_CAS_ATTEMPTS`` times before the shard is skipped
        with a WARNING.

        Never raises: every failure is caught, logged, and counted. The
        in-memory chain the caller already holds is never touched by this
        call — persistence is purely a side effect.
        """
        result = {"written": 0, "unchanged": 0, "conflicts_skipped": 0, "errors": 0}
        if not self.is_available():
            return result

        exp_keys = set(chain.get("calls", {}) or {}) | set(chain.get("puts", {}) or {})
        now = now or datetime.now(timezone.utc)
        underlying_price = chain.get("underlying_price")

        for exp_key in sorted(exp_keys):
            calls = (chain.get("calls", {}) or {}).get(exp_key, {})
            puts = (chain.get("puts", {}) or {}).get(exp_key, {})
            try:
                outcome = self._write_shard(symbol, exp_key, calls, puts, now, underlying_price)
                result[outcome] = result.get(outcome, 0) + 1
                if outcome == "written":
                    with self._lock:
                        self._writes_ok += 1
            except Exception as exc:
                result["errors"] += 1
                with self._lock:
                    self._writes_failed += 1
                self._record_error(symbol, exc)

        return result

    def _write_shard(self, symbol: str, exp_key: str, calls: dict, puts: dict,
                      now: datetime, underlying_price: Optional[float] = None) -> str:
        """Read-reconcile-write one shard. Returns one of "written",
        "unchanged", "conflicts_skipped". Raises only on a non-CAS Cosmos
        error (caught by the caller, ``persist()``, and counted as an
        error)."""
        from azure.core import MatchConditions
        from azure.cosmos.exceptions import (
            CosmosHttpResponseError,
            CosmosResourceNotFoundError,
        )

        shard_id = _shard_id(symbol, exp_key)
        want_calls, want_puts = calls, puts

        for attempt in range(_MAX_CAS_ATTEMPTS):
            try:
                stored = self._container.read_item(item=shard_id, partition_key=symbol)
                exists = True
            except CosmosResourceNotFoundError:
                stored = None
                exists = False

            if stored is not None:
                # Reconcile against whatever is *currently* persisted — it
                # may be newer than what this refresh cycle hydrated as
                # "prior" at its start (e.g. another process just wrote it).
                # `_reconcile_bucket` is a plain, store-owned verbatim union
                # (see module docstring): it never drops a derived field
                # (D1) and never manufactures `_meta` (D2), unlike calling
                # `options_chain_merge.merge_prior` here would.
                merged_calls = _reconcile_bucket(stored.get("calls") or {}, want_calls)
                merged_puts = _reconcile_bucket(stored.get("puts") or {}, want_puts)
                merged_price = underlying_price if underlying_price is not None else stored.get("underlying_price")
            else:
                merged_calls = want_calls
                merged_puts = want_puts
                merged_price = underlying_price

            merged_calls, merged_puts = self._apply_size_valve(
                symbol, exp_key, merged_calls, merged_puts
            )

            new_hash = _content_hash(merged_calls, merged_puts, merged_price)
            if exists and stored.get("_content_hash") == new_hash:
                return "unchanged"

            body = {
                "id": shard_id,
                "symbol": symbol,
                "doc_type": _DOC_TYPE,
                "expiration": exp_key,
                "schema_version": _SCHEMA_VERSION,
                "calls": merged_calls,
                "puts": merged_puts,
                "underlying_price": merged_price,
                "contract_count": len(merged_calls) + len(merged_puts),
                "updated_at": now.isoformat(),
                "_content_hash": new_hash,
            }

            try:
                if exists:
                    self._container.replace_item(
                        item=shard_id,
                        body=body,
                        etag=stored.get("_etag"),
                        match_condition=MatchConditions.IfNotModified,
                    )
                else:
                    self._container.create_item(body=body)
                return "written"
            except CosmosHttpResponseError as exc:
                if exc.status_code not in (409, 412):
                    raise
                if attempt + 1 < _MAX_CAS_ATTEMPTS:
                    logger.info(
                        "%s shard %s: CAS conflict (attempt %d/%d) — "
                        "re-reading and re-reconciling",
                        symbol, exp_key, attempt + 1, _MAX_CAS_ATTEMPTS,
                    )
                    continue
                logger.warning(
                    "%s shard %s: CAS conflict persisted after %d attempts — "
                    "skipping this shard for this cycle (no data lost, will "
                    "retry next refresh)",
                    symbol, exp_key, _MAX_CAS_ATTEMPTS,
                )
                return "conflicts_skipped"

        return "conflicts_skipped"

    def _apply_size_valve(self, symbol: str, exp_key: str, calls: dict, puts: dict):
        """Escape valve: if a shard would exceed ``max_shard_bytes``, evict
        the oldest-``_meta.last_seen`` contracts that are ``carried==true``
        AND ``openInterest in (0, None)`` AND unseen for
        ``_SIZE_VALVE_UNSEEN_DAYS`` or more, until under the limit. Explicit,
        logged, never silent (Danny's design §4.5)."""
        estimate = len(json.dumps({"calls": calls, "puts": puts}, default=str).encode("utf-8"))
        if estimate <= self.max_shard_bytes:
            return calls, puts

        logger.error(
            "%s shard %s: exceeds max_shard_bytes (%d > %d) — evicting oldest "
            "dead carried-forward contracts to fit",
            symbol, exp_key, estimate, self.max_shard_bytes,
        )

        calls = copy.deepcopy(calls)
        puts = copy.deepcopy(puts)

        cutoff = datetime.now(timezone.utc) - timedelta(days=_SIZE_VALVE_UNSEEN_DAYS)
        candidates = []
        for side_name, bucket in (("calls", calls), ("puts", puts)):
            for strike_key, contract in bucket.items():
                meta = contract.get("_meta") or {}
                if not meta.get("carried"):
                    continue
                if contract.get("openInterest") not in (0, None):
                    continue
                last_seen = meta.get("last_seen")
                try:
                    last_seen_dt = datetime.fromisoformat(str(last_seen).replace("Z", "+00:00"))
                except (TypeError, ValueError):
                    continue
                if last_seen_dt > cutoff:
                    continue
                candidates.append((last_seen_dt, side_name, strike_key))

        candidates.sort(key=lambda c: c[0])  # oldest last_seen first

        for _, side_name, strike_key in candidates:
            bucket = calls if side_name == "calls" else puts
            bucket.pop(strike_key, None)
            estimate = len(json.dumps({"calls": calls, "puts": puts}, default=str).encode("utf-8"))
            if estimate <= self.max_shard_bytes:
                break

        if estimate > self.max_shard_bytes:
            logger.error(
                "%s shard %s: still exceeds max_shard_bytes (%d) after evicting "
                "all eligible dead carried-forward contracts — writing "
                "oversized shard as-is (no live/observed data was evicted)",
                symbol, exp_key, estimate,
            )
        return calls, puts

    # ------------------------------------------------------------------
    # Retention
    # ------------------------------------------------------------------
    def prune_expired(self, symbol: str, *, today_et: date,
                       grace_days: Optional[int] = None) -> int:
        """Delete shards whose expiration date (America/New_York) is more
        than ``grace_days`` (default: ``self.expired_shard_grace_days``) in
        the past.

        Queries Cosmos directly rather than relying on the in-memory/serving
        chain, so shards already dropped from serving (same-day cutoff) but
        still within their persistence grace window are correctly left
        alone. Never raises; returns the count actually deleted (0 on any
        failure or when unavailable).
        """
        if not self.is_available():
            return 0
        grace_days = self.expired_shard_grace_days if grace_days is None else grace_days
        cutoff = today_et - timedelta(days=grace_days)

        from azure.cosmos.exceptions import CosmosResourceNotFoundError

        try:
            items = list(self._container.query_items(
                query="SELECT c.id, c.expiration FROM c WHERE c.symbol=@s AND c.doc_type=@t",
                parameters=[
                    {"name": "@s", "value": symbol},
                    {"name": "@t", "value": _DOC_TYPE},
                ],
                partition_key=symbol,
            ))
        except Exception as exc:
            self._record_error(symbol, exc)
            return 0

        deleted = 0
        for item in items:
            exp_key = item.get("expiration") or ""
            if not (len(exp_key) == 8 and exp_key.isdigit()):
                continue
            try:
                exp_date = datetime.strptime(exp_key, "%Y%m%d").date()
            except ValueError:
                continue
            if exp_date >= cutoff:
                continue
            try:
                self._container.delete_item(item=item["id"], partition_key=symbol)
                deleted += 1
            except CosmosResourceNotFoundError:
                pass
            except Exception as exc:
                self._record_error(symbol, exc)

        if deleted:
            logger.info(
                "%s: pruned %d expired options chain shard(s) past the %d-day "
                "grace window", symbol, deleted, grace_days,
            )
        return deleted

    def purge(self, symbol: str) -> int:
        """Explicit destructive admin operation: delete ALL persisted shards
        for ``symbol`` immediately, ignoring the expiration grace window.

        Not wired to any scheduled path. ``invalidate()``/``invalidate_all()``
        on ``OptionsChainCache`` drop only the in-memory entry and must never
        call this — only an explicit admin action should.
        """
        if not self.is_available():
            return 0

        from azure.cosmos.exceptions import CosmosResourceNotFoundError

        try:
            items = list(self._container.query_items(
                query="SELECT c.id FROM c WHERE c.symbol=@s AND c.doc_type=@t",
                parameters=[
                    {"name": "@s", "value": symbol},
                    {"name": "@t", "value": _DOC_TYPE},
                ],
                partition_key=symbol,
            ))
        except Exception as exc:
            self._record_error(symbol, exc)
            return 0

        deleted = 0
        for item in items:
            try:
                self._container.delete_item(item=item["id"], partition_key=symbol)
                deleted += 1
            except CosmosResourceNotFoundError:
                pass
            except Exception as exc:
                self._record_error(symbol, exc)
        return deleted

    # ------------------------------------------------------------------
    # Repair / migration support (scripts/repair_options_chain_shards.py)
    # ------------------------------------------------------------------
    def list_symbols_with_shards(self) -> List[str]:
        """Distinct symbols with at least one persisted options-chain
        shard — cross-partition query (mirrors the existing cross-
        partition pattern in ``src/cosmos_db.py``). Empty on any failure
        or when unavailable; never raises. Handles both a real driver's
        ``SELECT DISTINCT VALUE c.symbol`` flat-string rows and a test
        double that returns full documents."""
        if not self.is_available():
            return []
        try:
            rows = list(self._container.query_items(
                query="SELECT DISTINCT VALUE c.symbol FROM c WHERE c.doc_type=@t",
                parameters=[{"name": "@t", "value": _DOC_TYPE}],
                enable_cross_partition_query=True,
            ))
        except Exception as exc:
            self._record_error("*", exc)
            return []
        symbols = set()
        for row in rows:
            if isinstance(row, str):
                symbols.add(row)
            elif isinstance(row, dict):
                sym = row.get("symbol")
                if sym:
                    symbols.add(sym)
        return sorted(symbols)

    def list_shard_expirations(self, symbol: str) -> List[str]:
        """All persisted shard expiration keys (``YYYYMMDD``) for
        ``symbol``. Empty on any failure/unavailability; never raises."""
        if not self.is_available():
            return []
        try:
            rows = list(self._container.query_items(
                query="SELECT c.expiration FROM c WHERE c.symbol=@s AND c.doc_type=@t",
                parameters=[
                    {"name": "@s", "value": symbol},
                    {"name": "@t", "value": _DOC_TYPE},
                ],
                partition_key=symbol,
            ))
        except Exception as exc:
            self._record_error(symbol, exc)
            return []
        return sorted({r.get("expiration") for r in rows if r.get("expiration")})

    def repair_shard(self, symbol: str, exp_key: str, *, dry_run: bool = True) -> dict:
        """Idempotent, ETag-CAS single-shard repair for
        ``scripts/repair_options_chain_shards.py``: read the shard, run
        every contract through ``normalize_persisted_v1_to_v2``, and —
        unless ``dry_run`` — write it back only if normalization actually
        changed something (Rule Z11: no destructive migration, and no
        unnecessary write/RU cost when a shard is already clean, matching
        ``persist()``'s own write-skip discipline).

        Returns ``{"changed": bool, "written": bool, "error": str|None}``.
        Never raises.
        """
        result: Dict[str, Any] = {"changed": False, "written": False, "error": None}
        if not self.is_available():
            result["error"] = "store unavailable"
            return result

        from azure.core import MatchConditions
        from azure.cosmos.exceptions import (
            CosmosHttpResponseError,
            CosmosResourceNotFoundError,
        )

        shard_id = _shard_id(symbol, exp_key)
        for attempt in range(_MAX_CAS_ATTEMPTS):
            try:
                stored = self._container.read_item(item=shard_id, partition_key=symbol)
            except CosmosResourceNotFoundError:
                result["error"] = "shard not found"
                return result
            except Exception as exc:
                result["error"] = str(exc)
                self._record_error(symbol, exc)
                return result

            calls = stored.get("calls") or {}
            puts = stored.get("puts") or {}
            new_calls = {k: normalize_persisted_v1_to_v2(v) for k, v in calls.items()}
            new_puts = {k: normalize_persisted_v1_to_v2(v) for k, v in puts.items()}

            if new_calls == calls and new_puts == puts:
                result["changed"] = False
                return result

            result["changed"] = True
            if dry_run:
                return result

            underlying_price = stored.get("underlying_price")
            new_hash = _content_hash(new_calls, new_puts, underlying_price)
            body = dict(stored)
            body.update({
                "calls": new_calls,
                "puts": new_puts,
                "schema_version": _SCHEMA_VERSION,
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "_content_hash": new_hash,
            })
            # Cosmos system properties must not be sent back in the body —
            # `etag=` (below) is the CAS token; the rest are server-owned.
            for sys_prop in ("_etag", "_rid", "_self", "_attachments", "_ts"):
                body.pop(sys_prop, None)

            try:
                self._container.replace_item(
                    item=shard_id,
                    body=body,
                    etag=stored.get("_etag"),
                    match_condition=MatchConditions.IfNotModified,
                )
                result["written"] = True
                with self._lock:
                    self._writes_ok += 1
                return result
            except CosmosHttpResponseError as exc:
                if exc.status_code not in (409, 412):
                    result["error"] = str(exc)
                    with self._lock:
                        self._writes_failed += 1
                    self._record_error(symbol, exc)
                    return result
                if attempt + 1 >= _MAX_CAS_ATTEMPTS:
                    result["error"] = "CAS conflict persisted after max attempts"
                    return result
                continue  # re-read (fresh etag) and re-check next iteration

        result["error"] = "CAS conflict persisted after max attempts"
        return result


# ======================================================================
# Module-level singleton — lazily built from config.yaml / environment
# ======================================================================

_shared_store: Optional[OptionsChainStore] = None
_shared_store_lock = threading.Lock()
# P0 construction-retry bookkeeping (Danny's zero-free decision §4.1): a
# *transient* failure (unreachable Cosmos, a config-load hiccup) must not
# be memoized into `_shared_store` forever — only an explicit
# `persistence_enabled: false`, or a successful connection, is permanent.
_last_failure_at: Optional[datetime] = None
_last_failure_error: Optional[str] = None
_failure_count: int = 0
_last_success_at: Optional[datetime] = None


class _ConstructionOutcome:
    """Result of one ``_build_store_from_config()`` attempt.

    ``terminal=True`` means the result should be memoized into
    ``_shared_store`` permanently — either a deliberate
    ``persistence_enabled: false`` or a successful connection.
    ``terminal=False`` means a transient failure: the disabled store
    returned is thrown away, and the *next* call to
    ``get_options_chain_store()`` (once the backoff window elapses) will
    retry construction from scratch.
    """

    __slots__ = ("store", "terminal", "reason")

    def __init__(self, store: "OptionsChainStore", *, terminal: bool, reason: str = "") -> None:
        self.store = store
        self.terminal = terminal
        self.reason = reason


def _resolve_persistence_retry_seconds() -> float:
    """Reads ``options_chain_cache.persistence_retry_seconds`` (default
    ``_DEFAULT_PERSISTENCE_RETRY_SECONDS``). Mirrors the
    ``_resolve_ttl_from_config()`` pattern in ``options_chain_cache.py``.
    Never raises — falls back to the default on any config problem
    (including the exact "config itself is unavailable" case this backoff
    exists to survive)."""
    try:
        from src.config import Config
        cfg = Config()
        options_cfg = cfg.config.get("options_chain_cache") or {}
        return float(options_cfg.get("persistence_retry_seconds", _DEFAULT_PERSISTENCE_RETRY_SECONDS))
    except Exception:
        return float(_DEFAULT_PERSISTENCE_RETRY_SECONDS)


def _retry_backoff_seconds(failure_count: int) -> float:
    """Capped exponential backoff: ``base * 2**(n-1)``, capped at
    ``_MAX_RETRY_BACKOFF_SECONDS`` — so a persistently-down Cosmos doesn't
    get hammered on every single ``get_options_chain_store()`` call, but a
    momentary blip recovers within one ``persistence_retry_seconds``
    window."""
    if failure_count <= 0:
        return 0.0
    base = _resolve_persistence_retry_seconds()
    return min(base * (2 ** (failure_count - 1)), _MAX_RETRY_BACKOFF_SECONDS)


def get_persistence_health() -> dict:
    """Process-wide store-*construction* health — distinct from any one
    instance's own ``OptionsChainStore.stats()`` (which reports whether
    *that already-built* container is currently reachable). This reports
    whether the shared singleton itself could be built at all, so an
    operator/health-endpoint can see a stuck transient failure and when
    the next retry is due. Merged into ``OptionsChainCache.stats()``
    /``GET /api/health/options-chain``."""
    with _shared_store_lock:
        retry_in: Optional[float] = None
        if _shared_store is None and _last_failure_at is not None:
            backoff = _retry_backoff_seconds(_failure_count)
            elapsed = (datetime.now(timezone.utc) - _last_failure_at).total_seconds()
            retry_in = max(0.0, backoff - elapsed)
        return {
            "constructed": _shared_store is not None,
            "last_error": _last_failure_error,
            "last_error_at": _last_failure_at.isoformat() if _last_failure_at else None,
            "last_success_at": _last_success_at.isoformat() if _last_success_at else None,
            "failure_count": _failure_count,
            "retry_in_seconds": retry_in,
        }


def _build_store_from_config() -> _ConstructionOutcome:
    """Best-effort construction from ``config.yaml`` + environment.

    Any failure (missing config, missing/invalid Cosmos credentials,
    connectivity error) degrades to a disabled (memory-only) store, tagged
    as *transient* (``terminal=False``) so it is never memoized forever —
    logged at ERROR (visible, not silently swallowed) rather than WARNING,
    since an operator needs to notice a persistently-failing connection.
    An explicit ``persistence_enabled: false`` is the one *deliberate*
    disabled outcome (``terminal=True``, logged at INFO, memoized
    permanently — today's existing, intentional behavior). Mirrors the
    existing optional-container pattern in ``src/cosmos_db.py``.
    """
    try:
        from src.config import Config
        cfg = Config()
        options_cfg = cfg.config.get("options_chain_cache") or {}
    except Exception as exc:
        logger.error(
            "OptionsChainStore: could not load config.yaml — persistence "
            "disabled for now (will retry with backoff), falling back to "
            "memory-only chain caching in the meantime: %s", exc
        )
        return _ConstructionOutcome(OptionsChainStore(enabled=False), terminal=False, reason=str(exc))

    persistence_enabled = options_cfg.get("persistence_enabled", True)
    grace_days = options_cfg.get(
        "expired_shard_grace_days", _DEFAULT_EXPIRED_SHARD_GRACE_DAYS
    )
    max_bytes = options_cfg.get("max_shard_bytes", _DEFAULT_MAX_SHARD_BYTES)

    if not persistence_enabled:
        logger.info(
            "OptionsChainStore: options_chain_cache.persistence_enabled=false "
            "— memory-only mode (today's existing behavior)."
        )
        return _ConstructionOutcome(OptionsChainStore(enabled=False), terminal=True, reason="persistence_enabled=false")

    try:
        endpoint = cfg.cosmosdb_endpoint
        key = cfg.cosmosdb_key
        database = cfg.cosmosdb_database
        if not endpoint or not key:
            raise RuntimeError("cosmosdb endpoint/key not configured")
        from src.cosmos_db import CosmosDBService
        cosmos = CosmosDBService(endpoint=endpoint, key=key, database_name=database)
        container = cosmos.container
    except Exception as exc:
        logger.error(
            "OptionsChainStore: CosmosDB unavailable — persistence disabled "
            "for now (will retry with backoff), falling back to memory-only "
            "chain caching in the meantime: %s", exc
        )
        return _ConstructionOutcome(OptionsChainStore(enabled=False), terminal=False, reason=str(exc))

    logger.info(
        "OptionsChainStore: connected to Cosmos (database=%s) — persistence "
        "enabled.", database,
    )
    return _ConstructionOutcome(
        OptionsChainStore(
            container=container,
            enabled=True,
            expired_shard_grace_days=grace_days,
            max_shard_bytes=max_bytes,
        ),
        terminal=True,
        reason="",
    )


def get_options_chain_store(*, now: Optional[datetime] = None) -> OptionsChainStore:
    """Process-wide shared store instance, lazily constructed on first use.

    A transient construction failure is *not* memoized forever (the P0 bug
    Danny's zero-free decision §4.1 called out — a momentary Cosmos outage
    at process start previously disabled persistence for the rest of the
    process's life): outside the current capped-exponential backoff
    window, every call retries construction from scratch. Only an
    explicit ``persistence_enabled: false``, or a successful connection,
    is memoized permanently.

    ``now`` is an injectable clock for deterministic tests (time is
    injected, never slept) — production callers never pass it.
    """
    global _shared_store, _last_failure_at, _last_failure_error, _failure_count, _last_success_at

    if _shared_store is not None:
        return _shared_store

    with _shared_store_lock:
        if _shared_store is not None:
            return _shared_store

        moment = now or datetime.now(timezone.utc)

        if _last_failure_at is not None:
            backoff = _retry_backoff_seconds(_failure_count)
            elapsed = (moment - _last_failure_at).total_seconds()
            if elapsed < backoff:
                # Still within the backoff window: return a cheap,
                # unmemoized disabled placeholder rather than hammering
                # Cosmos (or the config loader) on every single call.
                return OptionsChainStore(enabled=False)

        outcome = _build_store_from_config()
        if outcome.terminal:
            _shared_store = outcome.store
            _last_failure_at = None
            _last_failure_error = None
            _failure_count = 0
            if outcome.store.is_available():
                _last_success_at = moment
            return _shared_store

        _failure_count += 1
        _last_failure_at = moment
        _last_failure_error = outcome.reason
        return outcome.store


def set_options_chain_store(store: Optional[OptionsChainStore]) -> None:
    """Override the process-wide shared store (tests / explicit wiring).

    Also clears any pending construction-retry backoff bookkeeping so a
    test that resets the singleton with ``set_options_chain_store(None)``
    never leaks a backoff window into the next test."""
    global _shared_store, _last_failure_at, _last_failure_error, _failure_count, _last_success_at
    with _shared_store_lock:
        _shared_store = store
        _last_failure_at = None
        _last_failure_error = None
        _failure_count = 0
        if store is not None and store.is_available():
            _last_success_at = datetime.now(timezone.utc)
