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
import threading
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_DOC_TYPE = "options_chain"
_SCHEMA_VERSION = 3
_DEFAULT_EXPIRED_SHARD_GRACE_DAYS = 7
_DEFAULT_MAX_SHARD_BYTES = 1_600_000
_MAX_CAS_ATTEMPTS = 3
# Contracts eligible for the size escape valve must additionally be unseen
# for at least this many days (Danny's design §4.5).
_SIZE_VALVE_UNSEEN_DAYS = 30


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
        """Persistence health, merged into ``OptionsChainCache.stats()``."""
        with self._lock:
            return {
                "available": self.is_available(),
                "persist_errors": self._persist_errors,
                "last_persist_error_at": self._last_persist_error_at,
                "last_persist_error": self._last_persist_error,
                "expired_shard_grace_days": self.expired_shard_grace_days,
                "max_shard_bytes": self.max_shard_bytes,
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
            except Exception as exc:
                result["errors"] += 1
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


# ======================================================================
# Module-level singleton — lazily built from config.yaml / environment
# ======================================================================

_shared_store: Optional[OptionsChainStore] = None
_shared_store_lock = threading.Lock()


def _build_store_from_config() -> OptionsChainStore:
    """Best-effort construction from ``config.yaml`` + environment.

    Any failure (missing config, missing/invalid Cosmos credentials,
    connectivity error) degrades to a disabled (memory-only) store — logged
    once, never raised. Mirrors the existing optional-container pattern in
    ``src/cosmos_db.py``.
    """
    try:
        from src.config import Config
        cfg = Config()
        options_cfg = cfg.config.get("options_chain_cache") or {}
    except Exception as exc:
        logger.warning(
            "OptionsChainStore: could not load config.yaml — persistence "
            "disabled, falling back to memory-only chain caching: %s", exc
        )
        return OptionsChainStore(enabled=False)

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
        return OptionsChainStore(enabled=False)

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
        logger.warning(
            "OptionsChainStore: CosmosDB unavailable at startup — persistence "
            "disabled, falling back to memory-only chain caching: %s", exc
        )
        return OptionsChainStore(enabled=False)

    return OptionsChainStore(
        container=container,
        enabled=True,
        expired_shard_grace_days=grace_days,
        max_shard_bytes=max_bytes,
    )


def get_options_chain_store() -> OptionsChainStore:
    """Process-wide shared store instance, lazily constructed on first use."""
    global _shared_store
    if _shared_store is None:
        with _shared_store_lock:
            if _shared_store is None:
                _shared_store = _build_store_from_config()
    return _shared_store


def set_options_chain_store(store: Optional[OptionsChainStore]) -> None:
    """Override the process-wide shared store (tests / explicit wiring)."""
    global _shared_store
    with _shared_store_lock:
        _shared_store = store
