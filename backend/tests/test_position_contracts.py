import pytest
from src.position_contracts import (
    PositionContractCountError,
    position_contract_counts,
    resolve_open_contract_count,
)


def _legacy_position(**overrides):
    position = {
        "position_id": "pos_AAPL_call_200_20270115_20260901_120000",
        "type": "call",
        "strike": 200,
        "expiration": "2027-01-15",
        "opened_at": "2026-09-01T12:00:00Z",
        "status": "active",
        "notes": "",
    }
    position.update(overrides)
    return position


@pytest.mark.parametrize(
    ("position", "count", "source"),
    [
        ({"type": "call", "contracts": 3}, 3, "contracts"),
        ({"type": "put", "quantity": "4"}, 4, "quantity"),
        ({"type": "call", "contracts": 5}, 5, "contracts"),
        ({"type": "put", "quantity": -6}, 6, "quantity"),
        ({"type": "call", "open_contracts": "2", "contracts": 7}, 2, "open_contracts"),
        ({"type": "put", "contracts_open": -3, "quantity": -8}, 3, "contracts_open"),
        ({"type": "call", "contracts": 2, "is_paper": True}, 2, "contracts"),
        ({"type": "put", "quantity": "9", "rolled_from": "pos-old"}, 9, "quantity"),
    ],
)
def test_resolves_manual_imported_rolled_paper_and_remaining_shapes(position, count, source):
    resolved = resolve_open_contract_count(position)
    assert resolved.contracts == count
    assert resolved.quantity_source == source


def test_matching_base_aliases_are_accepted():
    resolved = resolve_open_contract_count({"type": "call", "contracts": "-4", "quantity": 4})
    assert resolved.contracts == 4
    assert resolved.quantity_source == "contracts"


def test_proven_repository_legacy_shape_uses_visible_one_contract_fallback():
    resolved = resolve_open_contract_count(_legacy_position())
    assert resolved.contracts == 1
    assert resolved.quantity_source == "legacy_implicit_one"
    assert "historical one-contract default" in resolved.warnings[0]


@pytest.mark.parametrize(
    "position",
    [
        _legacy_position(
            position_id="pos_AAPL_call_200_20270115_20260926_104442",
            opened_at="2026-09-26T10:44:42Z",
        ),
        _legacy_position(position_schema_version=2),
        _legacy_position(
            position_id="pos_AAPL_call_200_20270115_20260901_120001",
        ),
    ],
)
def test_current_version_boundary_and_inconsistent_timestamp_cannot_use_legacy_fallback(position):
    with pytest.raises(PositionContractCountError) as exc:
        resolve_open_contract_count(position)
    assert exc.value.code == "position_quantity_missing"


@pytest.mark.parametrize(
    "position",
    [
        {"type": "call"},
        _legacy_position(position_id="imported-position"),
        _legacy_position(notes=None, opened_at=None),
    ],
)
def test_missing_nonlegacy_or_corrupt_shape_fails_precisely(position):
    with pytest.raises(PositionContractCountError) as exc:
        resolve_open_contract_count(position)
    assert exc.value.code == "position_quantity_missing"
    assert "Set 'contracts'" in str(exc.value)


@pytest.mark.parametrize(
    ("value", "code"),
    [
        (True, "position_quantity_invalid"),
        ("NaN", "position_quantity_invalid"),
        ("Infinity", "position_quantity_invalid"),
        (float("inf"), "position_quantity_invalid"),
        ("1.5", "position_quantity_fractional"),
        (0, "position_quantity_zero"),
        ("-0", "position_quantity_zero"),
        (None, "position_quantity_invalid"),
    ],
)
def test_rejects_boolean_nonfinite_fractional_and_zero(value, code):
    with pytest.raises(PositionContractCountError) as exc:
        resolve_open_contract_count({"contracts": value})
    assert exc.value.code == code


def test_negative_count_requires_short_option_semantics():
    with pytest.raises(PositionContractCountError) as exc:
        resolve_open_contract_count({"contracts": -2})
    assert exc.value.code == "position_quantity_invalid"


def test_conflicting_same_precedence_aliases_fail():
    with pytest.raises(PositionContractCountError) as exc:
        resolve_open_contract_count({"contracts": 2, "quantity": 3})
    assert exc.value.code == "position_quantity_conflict"


def test_never_reads_another_position():
    selected = resolve_open_contract_count({"position_id": "selected", "contracts": 2})
    other = {"position_id": "other", "contracts": 99}
    assert selected.contracts == 2
    assert other["contracts"] == 99


@pytest.mark.parametrize(
    ("position", "total", "remaining"),
    [
        (_legacy_position(), 1, 1),
        (_legacy_position(quantity="-4"), 4, 4),
        (_legacy_position(contracts=7, contracts_open="2"), 7, 2),
        (_legacy_position(contracts=7, status="closed", close_reason="assigned"), 7, 0),
        (_legacy_position(quantity="3", status="rolled", rolled_to="next"), 3, 0),
        (_legacy_position(open_contracts="2"), None, 2),
        (_legacy_position(status="closed"), None, 0),
        (_legacy_position(position_schema_version=2), None, None),
        (_legacy_position(contracts=None), None, None),
        (_legacy_position(contracts=True), None, None),
        (_legacy_position(contracts="1.5"), None, None),
        (_legacy_position(contracts="NaN"), None, None),
        (_legacy_position(contracts=2, quantity=3), None, None),
    ],
)
def test_read_projection_preserves_total_remaining_and_raw_data(position, total, remaining):
    import copy

    before = copy.deepcopy(position)
    counts = position_contract_counts(position)
    assert counts["total"]["count"] == total
    assert counts["remaining"]["count"] == remaining
    if total is None:
        assert counts["total"]["status"] in {"unknown", "error"}
        assert counts["total"]["message"]
    if total == 1:
        assert counts["total"]["source"] == "legacy_implicit_one"
        assert counts["total"]["warnings"]
    assert position == before


def test_remaining_cannot_exceed_valid_original_total_in_reads_or_rolls():
    position = _legacy_position(contracts=2, open_contracts=3)
    with pytest.raises(PositionContractCountError, match="exceed"):
        resolve_open_contract_count(position)
    assert position_contract_counts(position)["remaining"]["status"] == "error"


def test_partial_assignment_requires_remaining_but_preserves_known_total():
    position = _legacy_position(quantity="5", assigned_contracts=2)
    counts = position_contract_counts(position)
    assert counts["total"]["count"] == 5
    assert counts["remaining"]["status"] == "unknown"
    with pytest.raises(PositionContractCountError, match="partial-close"):
        resolve_open_contract_count(position)
    position["open_contracts"] = 3
    assert resolve_open_contract_count(position).contracts == 3


@pytest.mark.parametrize("field", ["closed_contracts", "contracts_closed", "assigned_contracts", "contracts_assigned", "partial_closes", "partial_assignments"])
def test_partial_evidence_without_authoritative_quantity_cannot_default(field):
    position = _legacy_position(**{field: 1})
    with pytest.raises(PositionContractCountError):
        resolve_open_contract_count(position)


class _RepairContainer:
    def __init__(self, positions):
        import copy

        self.doc = {
            "id": "config_AAPL", "symbol": "AAPL", "doc_type": "symbol_config",
            "_etag": "original", "positions": copy.deepcopy(positions),
            "unrelated": {"preserve": True},
        }
        self.writes = []
        self.race = False

    def query_items(self, **kwargs):
        if kwargs.get("enable_cross_partition_query"):
            assert kwargs["query"] == "SELECT * FROM c WHERE c.doc_type = 'symbol_config'"
        else:
            assert kwargs["partition_key"] == "AAPL"
        return [self.read_item(item="config_AAPL", partition_key="AAPL")]

    def read_item(self, **kwargs):
        import copy

        return copy.deepcopy(self.doc)

    def replace_item(self, *, item, body, etag, match_condition):
        import copy

        from azure.core import MatchConditions
        from azure.cosmos.exceptions import CosmosHttpResponseError

        assert match_condition == MatchConditions.IfNotModified
        if self.race:
            self.doc["notes"] = "concurrent user edit"
            self.doc["_etag"] = "concurrent"
        if etag != self.doc["_etag"]:
            raise CosmosHttpResponseError(status_code=412, message="ETag mismatch")
        self.doc = {**copy.deepcopy(body), "_etag": "repaired"}
        self.writes.append(item)
        return self.doc


def _plan(container):
    from scripts.repair_position_contracts import build_plan

    return build_plan(
        container, target={"endpoint": "https://example.invalid", "database": "test", "container": "symbols"},
        symbols=["AAPL"],
    )


@pytest.fixture
def repair_backup_dir():
    import shutil
    from pathlib import Path
    from uuid import uuid4

    path = Path.cwd() / f".position-contract-test-{uuid4().hex}"
    try:
        yield path
    finally:
        if path.exists():
            shutil.rmtree(path)


def test_repair_dry_run_eligible_legacy_aliases_and_explicit_skips():
    import copy

    positions = [
        _legacy_position(),
        _legacy_position(position_id="alias", quantity="-4", open_contracts=2),
        _legacy_position(position_id="closed-alias", quantity="3", status="closed"),
        _legacy_position(position_id="canonical", contracts="5"),
        _legacy_position(position_id="modern-missing", position_schema_version=2),
        _legacy_position(position_id="null", contracts=None),
        _legacy_position(position_id="fractional", quantity=1.5),
        _legacy_position(position_id="conflicting", quantity=2, contracts=3),
        _legacy_position(position_id="remaining-only", open_contracts=2),
        _legacy_position(position_id="closed-missing", status="closed"),
        _legacy_position(position_id="partial-missing", assigned_contracts=1),
        _legacy_position(position_id="invalid-remaining", quantity=3, open_contracts=None),
        _legacy_position(position_id="excess-remaining", quantity=2, open_contracts=3),
        _legacy_position(position_id="duplicate", quantity=2),
        _legacy_position(position_id="duplicate", quantity=2),
        _legacy_position(position_id="provenance", quantity=2, contract_count_repair={"source": "external"}),
    ]
    container = _RepairContainer(positions)
    before = copy.deepcopy(container.doc)
    plan = _plan(container)
    changes = plan["actions"][0]["changes"]
    assert [(c["contracts"], c["source"]) for c in changes] == [
        (1, "legacy_implicit_one"), (4, "quantity"), (3, "quantity"),
    ]
    assert {row["reason"] for row in plan["skips"]} >= {
        "already_canonical", "position_quantity_missing", "position_quantity_invalid",
        "position_quantity_fractional", "position_quantity_conflict", "ambiguous_position_identity",
        "existing_repair_provenance",
    }
    assert container.doc == before
    assert not container.writes
    assert _plan(container) == plan


def test_repair_backup_apply_and_repeat_run_noop(repair_backup_dir):
    import json
    import stat

    from scripts.repair_position_contracts import apply_plan

    container = _RepairContainer([_legacy_position(), _legacy_position(position_id="alias", quantity="-4", open_contracts=2)])
    plan = _plan(container)
    result = apply_plan(container, plan, expected_sha256=plan["sha256"], backup_dir=repair_backup_dir)
    assert result["updated"] == 2
    backup = json.loads(next(repair_backup_dir.iterdir()).read_text())
    assert "contracts" not in backup["documents"][0]["positions"][0]
    assert stat.S_IMODE(next(repair_backup_dir.iterdir()).stat().st_mode) == 0o600
    assert container.doc["unrelated"] == {"preserve": True}
    assert container.doc["positions"][1]["quantity"] == "-4"
    assert container.doc["positions"][1]["open_contracts"] == 2
    assert position_contract_counts(container.doc["positions"][0])["total"]["warnings"]
    assert resolve_open_contract_count(container.doc["positions"][0]).warnings
    again = _plan(container)
    assert again["actions"] == []
    apply_plan(container, again, expected_sha256=again["sha256"], backup_dir=repair_backup_dir)
    assert len(container.writes) == 1
    assert len(list(repair_backup_dir.iterdir())) == 1


def test_repair_fingerprint_and_preflight_snapshot_protect_changes(repair_backup_dir):
    from scripts.repair_position_contracts import apply_plan

    container = _RepairContainer([_legacy_position()])
    plan = _plan(container)
    with pytest.raises(ValueError, match="fingerprint"):
        apply_plan(container, plan, expected_sha256="0" * 64, backup_dir=repair_backup_dir)
    container.doc["positions"][0]["notes"] = "changed"
    with pytest.raises(ValueError, match="Snapshot changed"):
        apply_plan(container, plan, expected_sha256=plan["sha256"], backup_dir=repair_backup_dir)
    assert not container.writes
    assert not repair_backup_dir.exists()


def test_repair_cas_preserves_concurrent_edit_after_backup(repair_backup_dir):
    from scripts.repair_position_contracts import apply_plan

    container = _RepairContainer([_legacy_position()])
    plan = _plan(container)
    container.race = True
    result = apply_plan(container, plan, expected_sha256=plan["sha256"], backup_dir=repair_backup_dir)
    assert result["cas_conflict"] == 1
    assert result["updated"] == 0
    assert container.doc["notes"] == "concurrent user edit"
    assert "contracts" not in container.doc["positions"][0]
    assert not container.writes


def test_repair_backup_failure_and_bounds_never_write(repair_backup_dir, monkeypatch):
    import scripts.repair_position_contracts as repair
    from scripts.repair_position_contracts import apply_plan, build_plan

    container = _RepairContainer([_legacy_position(), _legacy_position(position_id="two", quantity=2)])
    with pytest.raises(ValueError, match="Position bound"):
        build_plan(container, target={}, symbols=["AAPL"], max_positions=1)
    plan = _plan(container)
    monkeypatch.setattr(repair.os, "open", lambda *a, **kw: (_ for _ in ()).throw(OSError("disk unavailable")))
    with pytest.raises(OSError, match="disk unavailable"):
        apply_plan(container, plan, expected_sha256=plan["sha256"], backup_dir=repair_backup_dir)
    assert not container.writes


@pytest.mark.parametrize("missing", ["COSMOSDB_ENDPOINT", "COSMOSDB_KEY"])
def test_repair_cli_requires_environment_credentials_without_revealing_values(monkeypatch, capsys, missing):
    from scripts.repair_position_contracts import main

    monkeypatch.setenv("COSMOSDB_ENDPOINT", "https://private-test.invalid")
    monkeypatch.setenv("COSMOSDB_KEY", "test-secret-do-not-print")
    monkeypatch.delenv(missing)
    assert main(["--symbol", "AAPL"]) == 2
    captured = capsys.readouterr()
    assert "environment variables are required" in captured.err
    assert "private-test" not in captured.out + captured.err
    assert "test-secret" not in captured.out + captured.err


@pytest.mark.parametrize("scope", [["--symbol", "AAPL"], ["--all-symbols"]])
def test_repair_cli_uses_env_credentials_and_explicit_nonsecret_target(monkeypatch, capsys, scope):
    import json

    import azure.cosmos
    from scripts.repair_position_contracts import main

    container = _RepairContainer([_legacy_position()])
    container.read = lambda: {"partitionKey": {"paths": ["/symbol"]}}
    calls = []

    class Client:
        def __init__(self, endpoint, *, credential, logging_enable):
            assert endpoint == "https://private-test.invalid"
            assert credential == "test-secret-do-not-print"
            assert logging_enable is False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get_database_client(self, name):
            calls.append(name)
            return self

        def get_container_client(self, name):
            calls.append(name)
            return container

    monkeypatch.setattr(azure.cosmos, "CosmosClient", Client)
    monkeypatch.setenv("COSMOSDB_ENDPOINT", "https://private-test.invalid")
    monkeypatch.setenv("COSMOSDB_KEY", "test-secret-do-not-print")
    assert main([*scope, "--database", "test-db", "--symbols-container", "test-symbols"]) == 0
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert calls == ["test-db", "test-symbols"]
    assert report["mode"] == "dry-run"
    assert report["target"]["database"] == "test-db"
    assert report["scope"]["all_symbols"] == ("--all-symbols" in scope)
    assert len(report["target"]["endpoint_sha256"]) == 64
    assert "private-test" not in captured.out + captured.err
    assert "test-secret" not in captured.out + captured.err
    assert not container.writes


def test_repair_cli_does_not_echo_sdk_credential_errors(monkeypatch, capsys):
    import azure.cosmos
    from scripts.repair_position_contracts import main

    def broken_client(*args, **kwargs):
        raise ValueError("SDK failure https://private-test.invalid test-secret-do-not-print")

    monkeypatch.setattr(azure.cosmos, "CosmosClient", broken_client)
    monkeypatch.setenv("COSMOSDB_ENDPOINT", "https://private-test.invalid")
    monkeypatch.setenv("COSMOSDB_KEY", "test-secret-do-not-print")
    assert main(["--symbol", "AAPL"]) == 2
    captured = capsys.readouterr()
    assert "ValueError" in captured.err
    assert "private-test" not in captured.out + captured.err
    assert "test-secret" not in captured.out + captured.err


class _AllRepairContainer:
    def __init__(self, docs):
        self.docs = docs
        self.queries = []
        self.writes = []

    def query_items(self, **kwargs):
        import copy

        self.queries.append(kwargs)
        assert kwargs == {
            "query": "SELECT * FROM c WHERE c.doc_type = 'symbol_config'",
            "enable_cross_partition_query": True,
        }
        return iter(copy.deepcopy(self.docs))


def _all_doc(symbol, doc_id=None, positions=None):
    return {
        "id": doc_id or f"config_{symbol}", "symbol": symbol,
        "doc_type": "symbol_config", "_etag": f"etag-{symbol}",
        "positions": positions or [],
    }


def test_all_scope_discovers_exact_case_includes_empty_configs_and_is_deterministic():
    from scripts.repair_position_contracts import build_plan

    docs = [
        _all_doc("aapl"),
        _all_doc("BRK.b"),
        _all_doc("AAPL", positions=[_legacy_position(quantity="2")]),
        _all_doc("AAPL", doc_id="another_AAPL_config"),
    ]
    container = _AllRepairContainer(docs)
    plan = build_plan(container, target={}, all_symbols=True)
    assert plan["scope"]["symbols"] == ["AAPL", "BRK.b", "aapl"]
    assert plan["scope"]["all_symbols"] is True
    assert plan["documents_scanned"] == 4
    assert len(plan["snapshots"]) == 4
    assert len(plan["actions"]) == 1
    assert not container.writes
    container.docs.reverse()
    assert build_plan(container, target={}, all_symbols=True) == plan


@pytest.mark.parametrize(
    ("docs", "limits", "message"),
    [
        ([_all_doc("a"), _all_doc("b")], {"max_documents": 1}, "Document bound exceeded"),
        ([_all_doc("a", positions=[{}, {}])], {"max_positions": 1}, "Position bound exceeded"),
    ],
)
def test_all_scope_discovery_aborts_on_bounds_without_writes(docs, limits, message):
    from scripts.repair_position_contracts import build_plan

    container = _AllRepairContainer(docs)
    with pytest.raises(ValueError, match=message):
        build_plan(container, target={}, all_symbols=True, **limits)
    assert not container.writes


def test_all_scope_empty_container_is_successful_apply_noop(repair_backup_dir):
    from scripts.repair_position_contracts import apply_plan, build_plan

    container = _AllRepairContainer([])
    plan = build_plan(container, target={}, all_symbols=True)
    assert plan["scope"]["symbols"] == []
    assert plan["documents_scanned"] == 0
    result = apply_plan(container, plan, expected_sha256=plan["sha256"], backup_dir=repair_backup_dir)
    assert result["updated"] == 0
    assert result["backup"] is None
    assert not repair_backup_dir.exists()
    assert not container.writes


@pytest.mark.parametrize("changed_docs", [[], [_all_doc("AAPL"), _all_doc("newSymbol")]])
def test_all_scope_rediscovery_changes_invalidate_approved_hash(changed_docs, repair_backup_dir):
    from scripts.repair_position_contracts import apply_plan, build_plan

    container = _AllRepairContainer([_all_doc("AAPL")])
    approved = build_plan(container, target={}, all_symbols=True)
    container.docs = changed_docs
    current = build_plan(container, target={}, all_symbols=True)
    assert approved["sha256"] != current["sha256"]
    with pytest.raises(ValueError, match="fingerprint changed"):
        apply_plan(container, current, expected_sha256=approved["sha256"], backup_dir=repair_backup_dir)
    assert not container.writes
    assert not repair_backup_dir.exists()


@pytest.mark.parametrize("args", [[], ["--all-symbols", "--symbol", "AAPL"]])
def test_repair_cli_requires_exactly_one_scope(args, capsys):
    from scripts.repair_position_contracts import main

    with pytest.raises(SystemExit) as exc:
        main(args)
    assert exc.value.code == 2
    assert "--all-symbols" in capsys.readouterr().err


class _CasRepairContainer(_AllRepairContainer):
    def read_item(self, *, item, partition_key):
        import copy

        return copy.deepcopy(next(
            doc for doc in self.docs
            if doc["id"] == item and doc["symbol"] == partition_key
        ))

    def replace_item(self, *, item, body, etag, match_condition):
        import copy

        from azure.core import MatchConditions
        from azure.cosmos.exceptions import CosmosHttpResponseError

        assert match_condition == MatchConditions.IfNotModified
        for index, doc in enumerate(self.docs):
            if doc["id"] == item and doc["symbol"] == body["symbol"]:
                if etag != doc["_etag"]:
                    raise CosmosHttpResponseError(status_code=412, message="conflict")
                self.docs[index] = {**copy.deepcopy(body), "_etag": f"{etag}-next"}
                self.writes.append((item, body["symbol"]))
                return copy.deepcopy(self.docs[index])
        raise AssertionError("Wrong document or partition")


def test_repair_case_sensitive_round_trip_and_partial_cross_document_failure(repair_backup_dir):
    import copy

    from scripts.repair_position_contracts import apply_plan, build_plan

    first = _all_doc("AAPL", doc_id="shared-id", positions=[
        _legacy_position(
            quantity="-4", open_contracts="2", closed_contracts=1,
            contracts_closed=1, assigned_contracts=1, contracts_assigned=1,
            partial_closes=[{"contracts": 1}], partial_assignments=[{"contracts": 1}],
            custom={"nullable": None, "decimal": "1.234567890123456789"},
        ),
        _legacy_position(position_id="explicit-null", contracts=None),
    ])
    second = _all_doc("aapl", doc_id="shared-id", positions=[_legacy_position(quantity=3)])
    container = _CasRepairContainer([first, second])
    before = copy.deepcopy(container.docs)
    replace = container.replace_item

    def concurrent_replace(**kwargs):
        if kwargs["body"]["symbol"] == "aapl":
            container.docs[1]["notes"] = "concurrent edit"
            container.docs[1]["_etag"] = "concurrent"
        return replace(**kwargs)

    container.replace_item = concurrent_replace
    plan = build_plan(container, target={}, all_symbols=True)
    result = apply_plan(container, plan, expected_sha256=plan["sha256"], backup_dir=repair_backup_dir)
    assert result["updated"] == 1
    assert result["cas_conflict"] == 1
    assert [row["status"] for row in result["outcomes"]] == ["updated", "cas_conflict"]
    assert container.writes == [("shared-id", "AAPL")]
    repaired = container.docs[0]["positions"][0]
    assert repaired["contracts"] == 4
    assert repaired["position_schema_version"] == 2
    assert {key: repaired[key] for key in before[0]["positions"][0]} == before[0]["positions"][0]
    assert container.docs[0]["positions"][1] == before[0]["positions"][1]
    assert "contracts" not in container.docs[1]["positions"][0]
    assert container.docs[1]["notes"] == "concurrent edit"


def test_repair_preflights_every_original_before_backup_or_writes(repair_backup_dir):
    from scripts.repair_position_contracts import apply_plan, build_plan

    container = _CasRepairContainer([
        _all_doc(symbol, positions=[_legacy_position(quantity=2)])
        for symbol in ["AAPL", "aapl"]
    ])
    plan = build_plan(container, target={}, all_symbols=True)
    read = container.read_item

    def changed_read(**kwargs):
        doc = read(**kwargs)
        if kwargs["partition_key"] == "aapl":
            doc["positions"][0]["notes"] = "changed without ETag change"
        return doc

    container.read_item = changed_read
    with pytest.raises(ValueError, match="Snapshot changed"):
        apply_plan(container, plan, expected_sha256=plan["sha256"], backup_dir=repair_backup_dir)
    assert not repair_backup_dir.exists()
    assert not container.writes


@pytest.mark.parametrize("change", ["add", "remove", "modify_skipped"])
def test_repair_apply_rediscovers_even_without_candidates(change, repair_backup_dir):
    from scripts.repair_position_contracts import apply_plan, build_plan

    container = _AllRepairContainer([_all_doc("AAPL")])
    plan = build_plan(container, target={}, all_symbols=True)
    if change == "add":
        container.docs.append(_all_doc("aapl"))
    elif change == "remove":
        container.docs.clear()
    else:
        container.docs[0]["unrelated"] = "changed"
    with pytest.raises(ValueError, match="Snapshot changed"):
        apply_plan(container, plan, expected_sha256=plan["sha256"], backup_dir=repair_backup_dir)
    assert not repair_backup_dir.exists()
    assert not container.writes


@pytest.mark.parametrize("failure", ["symlink", "escape", "fsync", "verification"])
def test_repair_unsafe_or_unverified_backup_never_writes(failure, repair_backup_dir, monkeypatch):
    import os
    from pathlib import Path

    import scripts.repair_position_contracts as repair

    container = _RepairContainer([_legacy_position()])
    plan = _plan(container)
    backup_dir = repair_backup_dir / "backup"
    repair_backup_dir.mkdir()
    if failure == "symlink":
        (repair_backup_dir / "real").mkdir()
        backup_dir.symlink_to(repair_backup_dir / "real", target_is_directory=True)
    elif failure == "escape":
        backup_dir = Path.cwd().parent / "must-not-create-contract-backup"
    elif failure == "fsync":
        monkeypatch.setattr(os, "fsync", lambda fd: (_ for _ in ()).throw(OSError("disk failure")))
    else:
        real_fdopen = os.fdopen

        class BadRead:
            def __init__(self, fd, *args, **kwargs):
                self.handle = real_fdopen(fd, *args, **kwargs)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.handle.close()

            def __getattr__(self, name):
                return getattr(self.handle, name)

            def read(self):
                return b"corrupted"

        monkeypatch.setattr(os, "fdopen", BadRead)
    with pytest.raises((ValueError, OSError)):
        repair.apply_plan(container, plan, expected_sha256=plan["sha256"], backup_dir=backup_dir)
    assert not container.writes


@pytest.mark.parametrize("failure", ["different_body", "sdk_error"])
def test_repair_postwrite_failure_reports_acknowledged_but_unverified_write(
    failure, repair_backup_dir,
):
    from scripts.repair_position_contracts import apply_plan

    container = _RepairContainer([_legacy_position()])
    plan = _plan(container)
    read = container.read_item

    def unverified_read(**kwargs):
        if container.writes:
            if failure == "sdk_error":
                raise RuntimeError("SDK request with secret-do-not-echo")
            return {**read(**kwargs), "unexpected": True}
        return read(**kwargs)

    container.read_item = unverified_read
    result = apply_plan(container, plan, expected_sha256=plan["sha256"], backup_dir=repair_backup_dir)
    assert result["updated"] == 0
    assert result["failed"] == 1
    assert result["outcomes"][0]["write_acknowledged"] is True
    assert result["outcomes"][0]["status"] == "failed"
    assert "secret-do-not-echo" not in str(result)
    assert len(container.writes) == 1


@pytest.mark.parametrize("confirmation", [[], ["--plan-sha256", "0" * 64], ["--confirm", "WRONG"]])
def test_repair_cli_rejects_unreviewed_apply_before_connecting(confirmation, monkeypatch, capsys):
    from unittest.mock import Mock

    import azure.cosmos

    from scripts.repair_position_contracts import main

    client = Mock(side_effect=AssertionError("Must not connect"))
    monkeypatch.setattr(azure.cosmos, "CosmosClient", client)
    assert main(["--symbol", "AAPL", "--apply", *confirmation]) == 2
    assert "--apply requires --plan-sha256 and --confirm" in capsys.readouterr().err
    client.assert_not_called()
