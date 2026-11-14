import json
from copy import deepcopy
from pathlib import Path
from shutil import rmtree
from uuid import uuid4

import pytest
from azure.core import MatchConditions
from azure.cosmos.exceptions import CosmosHttpResponseError

from scripts import repair_dividend_destination_withholding as repair
from src.dividends_economics import _event_from_legs

TARGET = {"endpoint": "https://example.documents.azure.com",
          "database": "db", "portfolio_container": "portfolio"}


@pytest.fixture
def backup_path():
    directory = Path(__file__).resolve().parents[1] / ".test-artifacts" / f"dividend-wht-{uuid4().hex}"
    directory.mkdir(parents=True)
    try:
        yield directory / "before.json"
    finally:
        rmtree(directory)


def account(identifier, name, **extra):
    return {"id": identifier, "account_id": identifier, "doc_type": "account",
            "name": name, "_etag": "1", **extra}


def dividend(identifier="cash", **extra):
    return {
        "id": identifier, "account_id": "fidelity", "_etag": "1",
        "doc_type": "ledger_txn", "txn_type": "DIVIDEND", "correction_status": "ACTIVE",
        "security_id": "XNAS:AAPL", "ticker": "AAPL", "trade_date": "2025-01-01",
        "gross": {"amount": "100", "currency": "EUR", "eur_amount": "100"},
        "fees": {"total": "2", "currency": "EUR", "total_eur": "2", "details": ["keep"]},
        "net": {"amount": "73", "currency": "EUR", "eur_amount": "73", "extra": "keep"},
        "fx": {"rate": "1", "rate_source": "ECB"},
        "withholding": {
            "source": {"amount_eur": "15", "rate_pct": "15", "country": "US", "extra": "keep"},
            "destination": {"amount_eur": "10", "rate_pct": "10", "country": "ES"},
            "extra": "keep",
        },
        "notes": "keep", "unknown_future_field": {"keep": True}, **extra,
    }


class MemoryContainer:
    def __init__(self, docs=()):
        all_docs = [
            account("fidelity", "Fidelity NetBenefits"), account("ibkr", "IBKR"),
            *docs,
        ]
        self.docs = {(d["account_id"], d["id"]): deepcopy(d) for d in all_docs}
        self.patches = []
        self.before_patch = None
        self.after_patch = None
        self.partition_paths = ["/account_id"]

    def read(self):
        return {"partitionKey": {"paths": self.partition_paths}}

    def query_items(self, query, parameters, **kwargs):
        assert kwargs["enable_cross_partition_query"] is True
        docs = list(self.docs.values())
        if "c.doc_type = 'account'" in query:
            docs = [d for d in docs if d["doc_type"] == "account"]
        else:
            docs = [d for d in docs if d["doc_type"] == "ledger_txn"]
            params = {p["name"]: p["value"] for p in parameters}
            if "@accounts" in params:
                docs = [d for d in docs if d["account_id"] in params["@accounts"]]
            if "@group" in params:
                docs = [d for d in docs if d.get("ca_group_id") == params["@group"]]
        return iter(deepcopy(docs))

    def read_item(self, item, partition_key):
        return deepcopy(self.docs[(partition_key, item)])

    def patch_item(self, item, partition_key, patch_operations, etag, match_condition, no_response):
        if self.before_patch:
            self.before_patch()
        assert match_condition == MatchConditions.IfNotModified
        assert no_response is False
        key = (partition_key, item)
        if self.docs[key]["_etag"] != etag:
            raise CosmosHttpResponseError(status_code=412, message="ETag conflict")
        self.patches.append((key, deepcopy(patch_operations), etag))
        for operation in patch_operations:
            field = operation["path"].removeprefix("/")
            assert field in {"net", "withholding"}
            if operation["op"] == "remove":
                del self.docs[key][field]
            else:
                self.docs[key][field] = deepcopy(operation["value"])
        self.docs[key]["_etag"] = str(int(etag) + 1)
        result = deepcopy(self.docs[key])
        if self.after_patch:
            self.after_patch()
        return result


def test_exact_names_not_broker_or_id_and_active_dividends_only():
    excluded = [
        dividend("buy", txn_type="BUY"),
        dividend("sell", txn_type="SELL"),
        dividend("superseded", correction_status="SUPERSEDED"),
        dividend("voided", correction_status="VOIDED"),
        dividend("deleted", is_deleted=True),
        dividend("deleted-at", deleted_at="2025-01-01"),
        dividend("unknown-status", correction_status="DELETED"),
        dividend("linked", superseded_by="new"),
        dividend("other", account_id="other"),
    ]
    c = MemoryContainer([
        dividend(), dividend("ibkr-cash", account_id="ibkr"), *excluded,
        account("other", "IBKR ", broker="IBKR"),
        account("Fidelity NetBenefits", "Different name", broker="Fidelity NetBenefits"),
        dividend("wrong-name", account_id="Fidelity NetBenefits"),
    ])
    report = repair.plan(c)
    assert report["matched"] == 2
    assert {e["id"] for e in report["entries"]} == {"cash", "ibkr-cash"}
    assert not report["failures"]
    assert not c.patches


@pytest.mark.parametrize("missing", [True, False])
def test_absent_or_ambiguous_exact_name_fails_closed(missing):
    c = MemoryContainer()
    if missing:
        del c.docs[("ibkr", "ibkr")]
    else:
        c.docs[("duplicate", "duplicate")] = account("duplicate", "IBKR")
    with pytest.raises(repair.RepairError, match="expected one"):
        repair.plan(c)


def test_runtime_partition_schema_must_match():
    c = MemoryContainer([dividend()])
    c.partition_paths = ["/symbol"]
    with pytest.raises(repair.RepairError, match="partition"):
        repair.plan(c)


def test_foreign_net_uses_native_fx_and_keeps_source_fees_and_unknown_fields():
    doc = dividend(
        gross={"amount": "100", "currency": "USD", "eur_amount": "80"},
        fees={"total": "2", "currency": "USD", "total_eur": "1.6"},
        fx={"rate": "0.8", "rate_source": "BROKER"},
    )
    after = repair.proposed(doc)
    assert after["net"] == {"amount": "79.250000", "currency": "USD",
                            "eur_amount": "63.400000", "extra": "keep"}
    assert after["withholding"]["destination"] is None
    assert after["withholding"]["source"] == doc["withholding"]["source"]
    assert after["withholding"]["extra"] == "keep"
    for key in doc.keys() - {"_etag", "net", "withholding"}:
        assert after[key] == doc[key]
    assert doc["withholding"]["destination"]["amount_eur"] == "10"


def test_eur_fees_in_foreign_dividend_and_six_place_rounding():
    doc = dividend(
        gross={"amount": "100", "currency": "GBP", "eur_amount": "120"},
        fx={"rate": "1.2"},
    )
    assert repair.proposed(doc)["net"]["amount"] == "85.833333"
    assert repair.proposed(doc)["net"]["eur_amount"] == "103.000000"


def test_null_with_stale_net_and_explicit_zero_both_repaired():
    zero = dividend("zero")
    zero["withholding"]["destination"] = {"amount_eur": "0", "rate_pct": "0", "country": "ES"}
    zero["net"].update(amount="83", eur_amount="83")
    null = dividend("null")
    null["withholding"]["destination"] = None
    report = repair.plan(MemoryContainer([zero, null]))
    assert len(report["entries"]) == 2
    for entry in report["entries"]:
        assert entry["after"]["withholding"]["destination"] is None
        assert entry["after"]["net"]["eur_amount"] == "83.000000"


def composite():
    cash = dividend(ca_group_id="group", ca_leg_type="CASH_DIVIDEND",
                    ca_event_type="DIVIDEND_WITH_SCRIP")
    share = dividend(
        "share", txn_type="BUY", ca_group_id="group", ca_leg_type="SHARE_ACQUISITION",
        ca_event_type="DIVIDEND_WITH_SCRIP", quantity="2", cost_basis_status="COMPLETE",
        gross={"amount": "0", "currency": "EUR", "eur_amount": "0"},
        fees={"total": "0", "currency": "EUR", "total_eur": "0"},
        net={"amount": "0", "currency": "EUR", "eur_amount": "0"},
        share_fmv={"eur_amount": "40", "currency": "EUR"},
        withholding={"source": None, "destination": None},
    )
    return cash, share


def test_composite_changes_cash_only_and_preserves_event_economics(backup_path):
    cash, share = composite()
    historical = dividend("historical", ca_group_id="group", correction_status="VOIDED")
    c = MemoryContainer([cash, share, historical])
    before = _event_from_legs("group", [cash, share])
    report = repair.plan(c)
    assert len(report["entries"]) == 1
    repair.apply_plan(c, report, TARGET, backup_path)
    current_cash = c.read_item("cash", "fidelity")
    after = _event_from_legs("group", [current_cash, share])
    assert after["_cash_net_eur"] == before["_cash_net_eur"] + 10
    assert after["_scrip_value_eur"] == before["_scrip_value_eur"]
    assert c.read_item("share", "fidelity") == share
    assert c.read_item("historical", "fidelity") == historical


@pytest.mark.parametrize("problem", ["duplicate", "missing", "cross-account", "identity", "leg-type"])
def test_inconsistent_composite_fails_closed(problem, backup_path):
    cash, share = composite()
    docs = [cash, share]
    if problem == "duplicate":
        docs.append({**share, "id": "second-share"})
    elif problem == "missing":
        docs = [cash]
    elif problem == "cross-account":
        share["account_id"] = "ibkr"
    elif problem == "identity":
        share["security_id"] = "XNAS:MSFT"
    else:
        cash["ca_leg_type"] = "SHARE_ACQUISITION"
    c = MemoryContainer(docs)
    report = repair.plan(c)
    assert report["failures"]
    with pytest.raises(repair.RepairError, match="Validation failures"):
        repair.apply_plan(c, report, TARGET, backup_path)
    assert not c.patches
    assert not backup_path.exists()


def test_backup_before_patch_etag_readback_idempotency_and_restore(backup_path):
    doc = dividend()
    c = MemoryContainer([doc])
    report = repair.plan(c)

    def check_backup():
        assert backup_path.exists()
        backup = json.loads(backup_path.read_text())
        assert backup["payload"]["entries"][0]["before"] == repair.clean(doc)
        assert backup["payload"]["entries"][0]["etag"] == "1"
        assert backup_path.stat().st_mode & 0o777 == 0o600
        assert repair.journal_path(backup_path).exists()

    c.before_patch = check_backup
    repair.apply_plan(c, report, TARGET, backup_path)
    repeated = repair.plan(c)
    assert repeated["entries"] == []
    assert repeated["unchanged"] == 1
    repair.apply_plan(c, repeated, TARGET, backup_path)
    assert len(c.patches) == 1
    repair.restore(c, TARGET, backup_path)
    assert repair.clean(c.read_item("cash", "fidelity")) == repair.clean(doc)
    repair.restore(c, TARGET, backup_path)
    assert len(c.patches) == 2


def test_backup_failure_prevents_every_write(backup_path, monkeypatch):
    c = MemoryContainer([dividend()])

    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr(repair, "durable_create", fail)
    with pytest.raises(OSError, match="disk full"):
        repair.apply_plan(c, repair.plan(c), TARGET, backup_path)
    assert not c.patches


def test_existing_backup_never_overwritten(backup_path):
    backup_path.write_text("existing")
    c = MemoryContainer([dividend()])
    with pytest.raises(FileExistsError):
        repair.apply_plan(c, repair.plan(c), TARGET, backup_path)
    assert backup_path.read_text() == "existing"
    assert not c.patches


def test_concurrent_edit_after_audit_stops_before_backup(backup_path):
    c = MemoryContainer([dividend()])
    report = repair.plan(c)
    c.docs[("fidelity", "cash")]["_etag"] = "2"
    with pytest.raises(repair.RepairError, match="changed since audit"):
        repair.apply_plan(c, report, TARGET, backup_path)
    assert not c.patches
    assert not backup_path.exists()


def test_cas_conflict_has_nonzero_exit_and_preserves_concurrent_edit(backup_path, monkeypatch, capsys):
    c = MemoryContainer([dividend()])
    monkeypatch.setattr(repair, "build_container", lambda *args: (c, TARGET))

    def concurrent():
        c.docs[("fidelity", "cash")]["_etag"] = "2"
        c.docs[("fidelity", "cash")]["notes"] = "concurrent edit"

    c.before_patch = concurrent
    assert repair.main(["--apply", "--backup", str(backup_path)]) == 1
    assert "HTTP 412" in capsys.readouterr().err
    assert backup_path.exists()
    assert not c.patches
    assert c.docs[("fidelity", "cash")]["notes"] == "concurrent edit"


def test_partial_failure_receipts_restore_only_successful_writes(backup_path):
    c = MemoryContainer([dividend(), dividend("second")])
    report = repair.plan(c)

    def conflict_on_second():
        if c.patches:
            c.docs[("fidelity", "second")]["_etag"] = "2"

    c.before_patch = conflict_on_second
    with pytest.raises(CosmosHttpResponseError):
        repair.apply_plan(c, report, TARGET, backup_path)
    c.before_patch = None
    repair.restore(c, TARGET, backup_path)
    assert c.read_item("cash", "fidelity")["withholding"]["destination"] is not None
    assert len(c.patches) == 2


@pytest.mark.parametrize("changed", ["etag", "body", "target", "backup", "receipt"])
def test_restore_never_overwrites_later_changes_or_invalid_backup(changed, backup_path):
    c = MemoryContainer([dividend()])
    repair.apply_plan(c, repair.plan(c), TARGET, backup_path)
    target = deepcopy(TARGET)
    if changed == "etag":
        c.docs[("fidelity", "cash")]["_etag"] = "3"
    elif changed == "body":
        c.docs[("fidelity", "cash")]["notes"] = "changed"
    elif changed == "target":
        target["database"] = "other"
    elif changed == "backup":
        data = json.loads(backup_path.read_text())
        data["payload"]["entries"][0]["before"]["notes"] = "tampered"
        backup_path.write_text(json.dumps(data))
    else:
        repair.journal_path(backup_path).write_text("")
    with pytest.raises(repair.RepairError):
        repair.restore(c, target, backup_path)
    assert len(c.patches) == 1


def test_readback_failure_is_nonzero_and_receipt_is_durable(backup_path, monkeypatch):
    c = MemoryContainer([dividend()])
    monkeypatch.setattr(repair, "build_container", lambda *args: (c, TARGET))
    c.after_patch = lambda: c.docs[("fidelity", "cash")]["net"].update(amount="wrong")
    assert repair.main(["--apply", "--backup", str(backup_path)]) == 1
    receipts = repair.journal_path(backup_path).read_text().splitlines()
    assert json.loads(receipts[0])["etag"] == "2"


@pytest.mark.parametrize(("block", "field", "value"), [
    ("gross", "amount", "bad"), ("gross", "eur_amount", None),
    ("fees", "total_eur", "NaN"), ("net", "amount", True),
    ("net", "eur_amount", "Infinity"), ("fx", "rate", "0"),
    ("fx", "rate", "2"), ("fees", "currency", "JPY"),
    ("gross", "currency", "eur"),
])
def test_malformed_money_fx_fails_closed_and_returns_failure(block, field, value, monkeypatch):
    doc = dividend()
    doc[block][field] = value
    c = MemoryContainer([doc, dividend("valid")])
    monkeypatch.setattr(repair, "build_container", lambda *args: (c, TARGET))
    report = repair.plan(c)
    assert len(report["failures"]) == 1
    assert repair.main([]) == 1
    assert not c.patches


@pytest.mark.parametrize("side", ["source", "destination"])
def test_malformed_withholding_never_silently_zeroed(side):
    doc = dividend()
    doc["withholding"][side]["amount_eur"] = "unparseable"
    with pytest.raises(repair.RepairError, match="invalid decimal"):
        repair.proposed(doc)


def test_missing_foreign_fx_fails_even_with_persisted_eur_amount():
    doc = dividend(gross={"amount": "100", "currency": "USD", "eur_amount": "80"}, fx=None)
    with pytest.raises(repair.RepairError, match="persisted FX"):
        repair.proposed(doc)


def test_absent_withholding_is_materialized_without_other_field_changes(backup_path):
    doc = dividend()
    del doc["withholding"]
    c = MemoryContainer([doc])
    repair.apply_plan(c, repair.plan(c), TARGET, backup_path)
    assert c.read_item("cash", "fidelity")["withholding"] == {"destination": None}
    repair.restore(c, TARGET, backup_path)
    assert "withholding" not in c.read_item("cash", "fidelity")


def test_default_audit_prints_target_accounts_counts_and_deltas_without_writes(monkeypatch, capsys):
    c = MemoryContainer([dividend()])
    monkeypatch.setattr(repair, "build_container", lambda *args: (c, TARGET))
    assert repair.main([]) == 0
    output = capsys.readouterr().out
    assert "Fidelity NetBenefits" in output and '"change_count": 1' in output
    assert '"endpoint": "https://example.documents.azure.com"' in output
    assert '"eur_amount": "83.000000"' in output
    assert not c.patches
