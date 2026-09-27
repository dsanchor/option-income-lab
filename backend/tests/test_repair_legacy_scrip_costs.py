import shutil
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts import repair_legacy_scrip_costs as repair
from src.dividends_economics import build_dividends_economics_report
from src.portfolio.holdings_service import acquisition_lot_unit_cost

TARGET = repair.TargetIdentity("https://example.documents.azure.com", "db", "portfolio")
ARTIFACTS = (
    Path(__file__).resolve().parents[1] / ".test-artifacts" / "legacy-scrip-costs"
)


class MemoryContainer:
    def __init__(self, docs):
        self.docs = {doc["id"]: deepcopy(doc) for doc in docs}
        self.replace_calls = []

    def query_items(self, **kwargs):
        return iter(deepcopy(list(self.docs.values())))

    def read_item(self, item, partition_key):
        assert self.docs[item]["account_id"] == partition_key
        return deepcopy(self.docs[item])

    def replace_item(self, item, body, **kwargs):
        self.replace_calls.append((item, deepcopy(body), deepcopy(kwargs)))
        stored = deepcopy(body)
        stored["_etag"] = f"etag-{len(self.replace_calls) + 1}"
        self.docs[item] = stored
        return deepcopy(stored)


def _doc(identifier="share", **overrides):
    doc = {
        "id": identifier,
        "_etag": "etag-1",
        "account_id": "acct",
        "doc_type": "ledger_txn",
        "txn_type": "BUY",
        "security_id": "XLON:ULVR",
        "trade_date": "2024-05-01",
        "quantity": "2",
        "ca_group_id": f"group-{identifier}",
        "ca_leg_type": "SHARE_ACQUISITION",
        "ca_event_type": "SCRIP_DIVIDEND",
        "correction_status": "ACTIVE",
        "gross": {"amount": "0", "currency": "GBP", "eur_amount": None},
        "fees": {"total": "0", "currency": "GBP", "total_eur": "0"},
        "net": {"amount": "0", "currency": "GBP", "eur_amount": "0"},
        "cost_basis_status": "INCOMPLETE",
        "share_fmv": {"eur_amount": "999999"},
    }
    doc.update(overrides)
    return doc


def test_explicit_zero_and_known_fee_restore_fifo_without_using_fmv():
    zero = repair.classify_document(_doc())
    assert zero["classification"] == "AUTO_REPAIRABLE"
    assert zero["reason_code"] == "EXPLICIT_ZERO_NATIVE_CONTRIBUTION"
    assert zero["proposed"]["cost_basis_status"] == "ZERO_COST"
    assert zero["resulting_fifo_lot_cost_eur"] == "0.000000"

    native_zero_fee = _doc(
        gross={"amount": "0", "currency": "GBP", "eur_amount": ""},
        fees={"total": "0", "currency": "GBP", "total_eur": ""},
        net={"amount": "0", "currency": "GBP", "eur_amount": "0"},
    )
    result = repair.classify_document(native_zero_fee)
    assert result["reason_code"] == "EXPLICIT_ZERO_NATIVE_CONTRIBUTION"
    assert result["proposed"]["cost_basis_status"] == "ZERO_COST"

    fee = _doc(
        gross={"amount": "0", "currency": "GBP", "eur_amount": ""},
        fees={"total": "2", "currency": "GBP", "total_eur": "2.40"},
        net={"amount": "2", "currency": "GBP", "eur_amount": "0"},
    )
    result = repair.classify_document(fee)
    assert result["proposed"]["cost_basis_status"] == "COMPLETE"
    assert result["proposed"]["net"]["eur_amount"] == "2.400000"
    assert result["resulting_fifo_lot_cost_eur"] == "2.400000"


def test_persisted_movement_fx_and_canonical_net_are_the_only_derivations():
    fx_doc = _doc(
        gross={"amount": "10", "currency": "GBP", "eur_amount": None},
        fees={"total": "1", "currency": "GBP", "total_eur": None},
        net={"amount": "11", "currency": "GBP", "eur_amount": None},
        fx={"rate": "1.2", "rate_source": "BROKER"},
    )
    result = repair.classify_document(fx_doc)
    assert result["reason_code"] == "DERIVED_FROM_MOVEMENT_FX"
    assert result["proposed"]["gross"]["eur_amount"] == "12.000000"
    assert result["proposed"]["fees"]["total_eur"] == "1.200000"
    assert result["proposed"]["net"]["eur_amount"] == "13.200000"

    net_doc = _doc(
        gross={"amount": "10", "currency": "GBP", "eur_amount": None},
        fees={"total": "1", "currency": "GBP", "total_eur": "1.2"},
        net={"amount": "11", "currency": "GBP", "eur_amount": "13.2"},
        fx={},
    )
    result = repair.classify_document(net_doc)
    assert result["reason_code"] == "DERIVED_FROM_CANONICAL_NET"
    assert result["proposed"]["gross"]["eur_amount"] == "12.000000"


def test_known_eur_and_status_only_cases_recompute_canonically():
    known = _doc(
        gross={"amount": "8", "currency": "EUR", "eur_amount": "8"},
        fees={"total": "2", "currency": "EUR", "total_eur": "2"},
        net={"amount": "10", "currency": "EUR", "eur_amount": "6"},
    )
    result = repair.classify_document(known)
    assert result["classification"] == "AUTO_REPAIRABLE"
    assert result["reason_code"] == "KNOWN_GROSS_EUR"
    assert result["proposed"]["net"]["eur_amount"] == "10.000000"
    assert result["proposed"]["cost_basis_status"] == "COMPLETE"

    status_only = _doc(
        gross={"amount": "0", "currency": "GBP", "eur_amount": "0.000000"},
        fees={"total": "0", "currency": "GBP", "total_eur": "0.000000"},
        net={"amount": "0.000000", "currency": "GBP", "eur_amount": "0.000000"},
    )
    result = repair.classify_document(status_only)
    assert result["reason_code"] == "STATUS_ONLY_RECLASSIFICATION"
    assert result["proposed_diff"] == {
        "cost_basis_status": {"from": "INCOMPLETE", "to": "ZERO_COST"}
    }


def test_share_fmv_never_changes_classification_or_cost_evidence():
    first = repair.classify_document(_doc(share_fmv={"eur_amount": "1"}))
    second = repair.classify_document(
        _doc(
            share_fmv={
                "eur_amount": "999999999",
                "fx": {"rate": "987.65"},
                "secret_token": "must-not-be-evidence",
            }
        )
    )
    for field in (
        "classification",
        "reason_code",
        "evidence",
        "proposed_diff",
        "resulting_fifo_lot_cost_eur",
    ):
        assert first[field] == second[field]
    assert all("share_fmv" not in item for item in second["evidence"])


def test_known_gross_eur_does_not_override_contradictory_native_equation():
    doc = _doc(
        gross={"amount": "8", "currency": "GBP", "eur_amount": "9.6"},
        fees={"total": "2", "currency": "GBP", "total_eur": "2.4"},
        net={"amount": "6", "currency": "GBP", "eur_amount": "7.2"},
    )

    result = repair.classify_document(doc)

    assert result["classification"] == "REVIEW_REQUIRED"
    assert result["reason_code"] == "CONTRADICTORY_AMOUNTS"
    assert result["proposed"] is None


@pytest.mark.parametrize(
    ("patch", "reason"),
    [
        (
            {
                "gross": {"amount": "10", "currency": "GBP", "eur_amount": None},
                "net": {"amount": None, "currency": "GBP", "eur_amount": None},
            },
            "MISSING_MOVEMENT_FX",
        ),
        (
            {
                "gross": {"amount": None, "currency": "GBP", "eur_amount": None},
                "net": {"amount": "0", "currency": "GBP", "eur_amount": "0"},
            },
            "MISSING_CONTRIBUTION",
        ),
        (
            {
                "gross": {"amount": "10", "currency": "GBP", "eur_amount": None},
                "net": {"amount": "9", "currency": "GBP", "eur_amount": "13.2"},
            },
            "CONTRADICTORY_AMOUNTS",
        ),
    ],
)
def test_ambiguous_cases_remain_review_required(patch, reason):
    doc = _doc(**patch)
    result = repair.classify_document(doc)
    assert result["classification"] == "REVIEW_REQUIRED"
    assert result["reason_code"] == reason
    assert result["proposed"] is None


@pytest.mark.parametrize(
    ("doc", "reason"),
    [
        (
            _doc(gross={"amount": "NaN", "currency": "GBP", "eur_amount": None}),
            "INVALID_NUMERIC_VALUE",
        ),
        (
            _doc(
                gross={"amount": "10", "currency": "GBP", "eur_amount": None},
                fees={"total": "1", "currency": "USD", "total_eur": None},
            ),
            "MIXED_FEE_CURRENCY",
        ),
        (
            _doc(
                gross={"amount": "8", "currency": "EUR", "eur_amount": "8"},
                fees={"total": "2", "currency": "EUR", "total_eur": "2"},
                net={"amount": "6", "currency": "EUR", "eur_amount": "10"},
            ),
            "CONTRADICTORY_AMOUNTS",
        ),
        (_doc(ca_group_id=""), "MALFORMED_ACTIVE_GROUP"),
    ],
)
def test_invalid_or_malformed_evidence_fails_closed(doc, reason):
    result = repair.classify_document(doc)
    assert result["classification"] == "REVIEW_REQUIRED"
    assert result["reason_code"] == reason
    assert result["proposed"] is None
    assert doc["cost_basis_status"] == "INCOMPLETE"


def test_ambiguous_correction_chain_fails_closed():
    result = repair.classify_document(_doc(), chain_ambiguous=True)
    assert result["classification"] == "REVIEW_REQUIRED"
    assert result["reason_code"] == "CORRECTION_CHAIN_AMBIGUOUS"
    assert result["proposed"] is None


def test_plan_is_deterministic_read_only_and_reports_chain_context():
    active = _doc()
    superseded = _doc(
        "old",
        ca_group_id=active["ca_group_id"],
        correction_status="SUPERSEDED",
        superseded_by_ca_group_id=active["ca_group_id"],
    )
    container = MemoryContainer([active, superseded])
    first = repair.build_plan(container, target=TARGET)
    second = repair.build_plan(container, target=TARGET)
    assert first == second
    assert len(first["sha256"]) == 64
    assert [action["id"] for action in first["actions"]] == ["share"]
    assert (
        first["rows"][0]["correction_chain"]["related_movements"][0][
            "correction_status"
        ]
        == "SUPERSEDED"
    )
    assert container.replace_calls == []


def test_plan_filters_active_only_and_never_proposes_history_changes():
    active = _doc("active", account_id="wanted", security_id="XLON:ULVR")
    superseded = _doc(
        "superseded",
        account_id="wanted",
        security_id="XLON:ULVR",
        correction_status="SUPERSEDED",
    )
    voided = _doc(
        "voided",
        account_id="wanted",
        security_id="XLON:ULVR",
        correction_status="VOIDED",
    )
    other = _doc("other", account_id="other", security_id="XLON:OTHER")
    container = MemoryContainer([active, superseded, voided, other])

    plan = repair.build_plan(
        container,
        target=TARGET,
        filters={"account_id": "wanted", "security_id": "XLON:ULVR"},
    )

    assert [row["share_leg_id"] for row in plan["rows"]] == ["active"]
    assert [action["id"] for action in plan["actions"]] == ["active"]
    assert container.docs["superseded"]["cost_basis_status"] == "INCOMPLETE"
    assert container.docs["voided"]["cost_basis_status"] == "INCOMPLETE"


def test_duplicate_active_share_legs_with_inconsistent_evidence_are_ambiguous():
    first = _doc("first", ca_group_id="duplicate-group")
    second = _doc(
        "second",
        ca_group_id="duplicate-group",
        gross={"amount": "10", "currency": "GBP", "eur_amount": None},
        fees={"total": "0", "currency": "GBP", "total_eur": "0"},
        net={"amount": "10", "currency": "GBP", "eur_amount": None},
        fx={"rate": "1.2"},
    )

    plan = repair.build_plan(MemoryContainer([first, second]), target=TARGET)

    assert plan["actions"] == []
    assert {row["classification"] for row in plan["rows"]} == {"REVIEW_REQUIRED"}
    assert {row["reason_code"] for row in plan["rows"]} == {
        "CORRECTION_CHAIN_AMBIGUOUS"
    }


def test_apply_backs_up_marks_cas_write_and_restore_is_idempotent(monkeypatch):
    shutil.rmtree(ARTIFACTS, ignore_errors=True)
    container = MemoryContainer([_doc()])
    plan = repair.build_plan(container, target=TARGET)

    def replace(memory, raw, body):
        memory.replace_item(raw["id"], body)
        return True

    monkeypatch.setattr(repair, "_etag_replace", replace)
    clock = lambda: datetime(2026, 9, 27, tzinfo=timezone.utc)
    result, backup_path = repair.apply_plan(
        container, plan, backup_dir=ARTIFACTS, now=clock
    )
    assert result["updated"] == 1
    assert backup_path is not None and backup_path.exists()
    assert container.docs["share"]["share_fmv"] == {"eur_amount": "999999"}
    assert container.docs["share"]["cost_basis_status"] == "ZERO_COST"
    assert container.docs["share"][repair.MARKER_KEY]["plan_sha256"] == plan["sha256"]

    restored = repair.restore_backup(container, backup_path, TARGET)
    assert restored["restored"] == 1
    assert container.docs["share"]["cost_basis_status"] == "INCOMPLETE"
    assert repair.MARKER_KEY not in container.docs["share"]
    assert (
        repair.restore_backup(container, backup_path, TARGET)["already_restored"] == 1
    )
    assert repair.build_plan(container, target=TARGET)["actions"][0]["id"] == "share"
    shutil.rmtree(ARTIFACTS, ignore_errors=True)


def test_second_plan_is_idempotent_and_backup_tamper_or_cas_fails_closed(monkeypatch):
    shutil.rmtree(ARTIFACTS, ignore_errors=True)
    container = MemoryContainer([_doc()])
    plan = repair.build_plan(container, target=TARGET)

    def replace(memory, raw, body):
        memory.replace_item(raw["id"], body)
        return True

    monkeypatch.setattr(repair, "_etag_replace", replace)
    result, backup_path = repair.apply_plan(container, plan, backup_dir=ARTIFACTS)
    assert result["updated"] == 1
    assert repair.build_plan(container, target=TARGET)["actions"] == []

    payload = repair.read_backup(backup_path, TARGET)
    payload["documents"][0]["body"]["cost_basis_status"] = "COMPLETE"
    backup_path.write_text(
        repair.json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    with pytest.raises(repair.RepairError, match="checksum"):
        repair.read_backup(backup_path, TARGET)

    conflict_container = MemoryContainer([_doc()])
    conflict_plan = repair.build_plan(conflict_container, target=TARGET)
    monkeypatch.setattr(repair, "_etag_replace", lambda *args, **kwargs: False)
    conflict, _ = repair.apply_plan(
        conflict_container, conflict_plan, backup_dir=ARTIFACTS
    )
    assert conflict["cas_conflict"] == 1
    assert conflict["updated"] == 0
    assert conflict_container.docs["share"]["cost_basis_status"] == "INCOMPLETE"
    shutil.rmtree(ARTIFACTS, ignore_errors=True)


def test_repair_restores_fifo_and_economics_only_for_complete_evidence():
    incomplete = _doc()
    before = build_dividends_economics_report([incomplete])
    assert acquisition_lot_unit_cost(incomplete) is None
    assert before["summary"]["scrip_valuation_status"] == "UNAVAILABLE"

    repaired = repair.classify_document(incomplete)["proposed"]
    after = build_dividends_economics_report([repaired])
    assert acquisition_lot_unit_cost(repaired) == 0
    assert after["summary"]["scrip_valuation_status"] == "COMPLETE"
    assert after["summary"]["scrip_dividends_eur"] == 999999.0

    ambiguous = _doc(
        "ambiguous",
        gross={"amount": None, "currency": "GBP", "eur_amount": None},
        net={"amount": "0", "currency": "GBP", "eur_amount": "0"},
    )
    assert repair.classify_document(ambiguous)["proposed"] is None
    assert acquisition_lot_unit_cost(ambiguous) is None
    assert (
        build_dividends_economics_report([ambiguous])["summary"][
            "scrip_valuation_status"
        ]
        == "UNAVAILABLE"
    )


def test_apply_argument_contract_requires_hash_confirmation_and_scope():
    parser = repair._parser()
    with pytest.raises(repair.RepairError):
        repair._validate_args(parser.parse_args(["--apply"]))
    with pytest.raises(repair.RepairError):
        repair._validate_args(
            parser.parse_args(
                [
                    "--apply",
                    "--plan-sha256",
                    "a" * 64,
                    "--confirm",
                    repair.APPLY_CONFIRMATION,
                ]
            )
        )
        with pytest.raises(repair.RepairError):
            repair._validate_args(parser.parse_args(["--restore", "backup.json"]))
        repair._validate_args(
            parser.parse_args(
                [
                    "--restore",
                    "backup.json",
                    "--backup-sha256",
                    "b" * 64,
                    "--confirm",
                    repair.RESTORE_CONFIRMATION,
                ]
            )
        )
        with pytest.raises(repair.RepairError):
            repair._validate_args(
                parser.parse_args(
                    [
                        "--restore",
                        "backup.json",
                        "--backup-sha256",
                        "b" * 64,
                        "--confirm",
                        repair.RESTORE_CONFIRMATION,
                        "--account-id",
                        "acct",
                    ]
                )
            )
    repair._validate_args(
        parser.parse_args(
            [
                "--apply",
                "--plan-sha256",
                "a" * 64,
                "--confirm",
                repair.APPLY_CONFIRMATION,
                "--movement-id",
                "share",
            ]
        )
    )
