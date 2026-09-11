"""Tests for the import session state machine (ImportService).

Hermetic: fake Cosmos containers, no network, no LLM.
"""

import pytest
from decimal import Decimal
from uuid import uuid4

from src.portfolio.cosmos_portfolio import CosmosPortfolioService
from src.portfolio.cosmos_securities import CosmosSecuritiesService
from src.portfolio.import_service import (
    ImportService,
    StateError,
    UnresolvedQuestionsError,
    AlreadyCommittedError,
    _build_preview_movements,
)


# ---------------------------------------------------------------------------
# Fake containers
# ---------------------------------------------------------------------------

class FakeImportSessionsContainer:
    def __init__(self):
        self._store: dict = {}

    def create_item(self, body: dict):
        key = body["id"]
        self._store[key] = dict(body)
        return dict(body)

    def read_item(self, item: str, partition_key: str):
        from azure.cosmos.exceptions import CosmosResourceNotFoundError
        if item not in self._store:
            raise CosmosResourceNotFoundError(message="not found", response=None)
        return dict(self._store[item])

    def replace_item(self, item: str, body: dict):
        from azure.cosmos.exceptions import CosmosResourceNotFoundError
        if item not in self._store:
            raise CosmosResourceNotFoundError(message="not found", response=None)
        self._store[item] = dict(body)
        return dict(body)


class FakePortfolioContainer:
    def __init__(self):
        self._store: dict = {}

    def create_item(self, body: dict):
        self._store[body["id"]] = dict(body)
        return dict(body)

    def upsert_item(self, body: dict):
        self._store[body["id"]] = dict(body)
        return dict(body)

    def query_items(self, query="", parameters=None, enable_cross_partition_query=True, partition_key=None):
        if "COUNT" in query:
            return iter([0])
        items = list(self._store.values())
        if "doc_type = 'account'" in query:
            items = [d for d in items if d.get("doc_type") == "account"]
        elif "doc_type = 'ledger_txn'" in query:
            items = [d for d in items if d.get("doc_type") == "ledger_txn"]
        return iter(items)

    def read_item(self, item: str, partition_key: str):
        from azure.cosmos.exceptions import CosmosResourceNotFoundError
        if item not in self._store:
            raise CosmosResourceNotFoundError(message="not found", response=None)
        return dict(self._store[item])

    def replace_item(self, item: str, body: dict):
        self._store[item] = dict(body)
        return dict(body)


class FakeSymbolsContainer:
    def __init__(self):
        self._store: dict = {}

    def read_item(self, item: str, partition_key: str):
        from azure.cosmos.exceptions import CosmosResourceNotFoundError
        key = (partition_key, item)
        if key not in self._store:
            raise CosmosResourceNotFoundError(message="not found", response=None)
        return dict(self._store[key])

    def create_item(self, body: dict):
        ticker = body["symbol"]
        key = (ticker, body["id"])
        self._store[key] = dict(body)
        return dict(body)

    def query_items(self, query="", parameters=None, enable_cross_partition_query=False, partition_key=None):
        param_map = {p["name"]: p["value"] for p in (parameters or [])}
        results = []
        for (pk, did), doc in self._store.items():
            if partition_key and pk != partition_key:
                continue
            if doc.get("doc_type") != "security_master":
                continue
            if "@isin" in param_map and doc.get("isin") != param_map["@isin"]:
                continue
            results.append(dict(doc))
        return iter(results)

    def replace_item(self, item: str, body: dict):
        for key in self._store:
            if self._store[key].get("id") == item:
                self._store[key] = dict(body)
                return dict(body)
        from azure.cosmos.exceptions import CosmosResourceNotFoundError
        raise CosmosResourceNotFoundError(message="not found", response=None)


def _make_import_service(preload_securities=None):
    sessions_container = FakeImportSessionsContainer()
    portfolio_container = FakePortfolioContainer()
    symbols_container = FakeSymbolsContainer()

    # Pre-load securities if provided
    if preload_securities:
        sec_svc = CosmosSecuritiesService(symbols_container)
        for sec_data in preload_securities:
            sec_svc.create_security(sec_data)

    portfolio_svc = CosmosPortfolioService(portfolio_container, sessions_container)
    securities_svc = CosmosSecuritiesService(symbols_container)
    return ImportService(portfolio_svc, securities_svc)


def _create_account(portfolio_svc, name="IB Main", broker="interactive_brokers"):
    return portfolio_svc.create_account(broker=broker, name=name, currency="EUR")


# ---------------------------------------------------------------------------
# CSV fixtures
# ---------------------------------------------------------------------------

def _dividends_csv(rows=None):
    header = "Año\tEmpresa\tFecha de cobro\tImporte Bruto\tImporte Neto\tImporte en Derechos\tRetención Origen\tRetención Destino\n"
    if rows is None:
        rows = [
            "2024\tApple Inc.\t15/06/2024\t100,00\t73,31\t0,00\t12,94\t13,75\n",
            "2024\tTelefónica\t20/06/2024\t50,00\t38,00\t0,00\t7,50\t4,50\n",
        ]
    return (header + "".join(rows)).encode("utf-8")


def _purchases_csv(rows=None):
    header = "Año\tEmpresa\tFecha compra\tValor compra\tAcciones\tTotal (€)\tComisión\n"
    if rows is None:
        rows = [
            "2024\tApple Inc.\t10/01/2024\t182,50\t10\t1.825,00\t7,50\n",
        ]
    return (header + "".join(rows)).encode("utf-8")


def _sales_csv(rows=None):
    header = "Año\tEmpresa\tFecha venta\tAcciones\tComisión\tTotal Venta\n"
    if rows is None:
        rows = [
            "2024\tApple Inc.\t20/06/2024\t5\t7,50\t1.050,00\n",
        ]
    return (header + "".join(rows)).encode("utf-8")


def _options_csv(rows=None, with_net=False, with_close_date=False):
    if with_net and with_close_date:
        header = (
            "Símbolo\tTipo\tFecha\tStrike\tExpiración\tImporte USD Bruto\t"
            "Importe EUR Bruto\tComisión EUR\tImporte EUR Neto\tFecha de cierre\tCuenta\n"
        )
    elif with_net:
        header = (
            "Símbolo\tTipo\tFecha\tStrike\tExpiración\tImporte USD Bruto\t"
            "Importe EUR Bruto\tComisión EUR\tImporte EUR Neto\tCuenta\n"
        )
    elif with_close_date:
        header = (
            "Símbolo\tTipo\tFecha\tStrike\tExpiración\tImporte USD Bruto\t"
            "Importe EUR Bruto\tComisión EUR\tFecha de cierre\tCuenta\n"
        )
    else:
        header = (
            "Símbolo\tTipo\tFecha\tStrike\tExpiración\tImporte USD Bruto\t"
            "Importe EUR Bruto\tComisión EUR\tCuenta\n"
        )
    if rows is None:
        rows = [
            "AAPL\tCALL_SELL\t19/07/2024\t210\t19/07/2024\t150.00\t138.00\t3.25\tIB Main\n",
            "AAPL\tPUT_BUY\t16/08/2024\t180\t16/08/2024\t80.00\t74.00\t-1.25\tIB Main\n",
        ]
    return (header + "".join(rows)).encode("utf-8")


_AAPL_SEC = {
    "ticker": "AAPL",
    "company_name": "Apple Inc.",
    "exchange_mic": "XNYS",
    "listing_currency": "USD",
    "isin": "US0378331005",
    "aliases": [{"source": "user", "value": "Apple Inc."}],
}

_TEF_SEC = {
    "ticker": "TEF",
    "company_name": "Telefónica SA",
    "exchange_mic": "XMAD",
    "listing_currency": "EUR",
    "isin": "ES0178430E18",
    "aliases": [{"source": "user", "value": "Telefónica"}],
}


# ---------------------------------------------------------------------------
# Session creation
# ---------------------------------------------------------------------------

class TestCreateSession:
    def test_create_returns_session_id(self):
        svc = _make_import_service()
        session = svc.create_session(_dividends_csv(), format_hint="dividends")
        assert session["session_id"].startswith("imp_")

    def test_creates_entity_questions(self):
        svc = _make_import_service()
        session = svc.create_session(_dividends_csv(), format_hint="dividends")
        questions = session.get("questions", [])
        entity_q = [q for q in questions if q["scope"] == "ENTITY"]
        # Two distinct companies → two ENTITY questions
        assert len(entity_q) == 2

    def test_each_company_asked_once(self):
        """Duplicate company names produce only one question."""
        rows = [
            "2024\tApple Inc.\t15/06/2024\t100,00\t73,31\t0,00\t12,94\t13,75\n",
            "2024\tApple Inc.\t16/06/2024\t50,00\t36,00\t0,00\t7,50\t6,50\n",  # same company
        ]
        svc = _make_import_service()
        session = svc.create_session(_dividends_csv(rows), format_hint="dividends")
        entity_q = [q for q in session["questions"] if q["scope"] == "ENTITY"]
        assert len(entity_q) == 1  # asked once, not twice

    def test_detected_format(self):
        svc = _make_import_service()
        session = svc.create_session(_dividends_csv(), format_hint="dividends")
        assert session["detected_format"] == "dividends"

    def test_currency_default_eur(self):
        svc = _make_import_service()
        session = svc.create_session(_dividends_csv())
        assert session["currency"] == "EUR"

    def test_account_id_default_unassigned(self):
        svc = _make_import_service()
        session = svc.create_session(_dividends_csv())
        assert session["account_id"] == "_unassigned"

    def test_rights_amount_warning_in_session(self):
        rows = [
            "2024\tSantander\t10/06/2024\t80,00\t60,00\t45,30\t12,00\t8,00\n",
        ]
        svc = _make_import_service()
        session = svc.create_session(_dividends_csv(rows))
        warnings = session.get("warnings", [])
        assert any(w["type"] == "RIGHTS_AMOUNT" for w in warnings)

    def test_empty_file_raises_value_error(self):
        svc = _make_import_service()
        with pytest.raises(ValueError):
            svc.create_session(b"", format_hint="dividends")

    def test_parse_error_propagates(self):
        svc = _make_import_service()
        with pytest.raises(ValueError):
            svc.create_session(b"not,a,valid,csv", format_hint="dividends")

    def test_options_account_name_resolves_to_account_id(self):
        svc = _make_import_service(preload_securities=[_AAPL_SEC])
        acct = _create_account(svc.portfolio_svc)
        session = svc.create_session(
            _options_csv(rows=[
                "AAPL\tCALL_SELL\t19/07/2024\t210\t19/07/2024\t150.00\t138.00\t3.25\tIB Main\n",
            ]),
            format_hint="options",
        )
        stored = svc.portfolio_svc.get_session(session["session_id"])
        parsed_row = stored["parsed_rows"][0]
        assert parsed_row["account_id"] == acct["account_id"]


# ---------------------------------------------------------------------------
# Get session
# ---------------------------------------------------------------------------

class TestGetSession:
    def test_get_nonexistent_returns_none(self):
        svc = _make_import_service()
        assert svc.get_session("imp_nonexistent") is None

    def test_get_existing_returns_session(self):
        svc = _make_import_service()
        session = svc.create_session(_dividends_csv())
        sid = session["session_id"]
        retrieved = svc.get_session(sid)
        assert retrieved is not None
        assert retrieved["session_id"] == sid


# ---------------------------------------------------------------------------
# Answer questions
# ---------------------------------------------------------------------------

class TestAnswerQuestion:
    def test_answer_selected_candidate(self):
        svc = _make_import_service(preload_securities=[_AAPL_SEC])
        session = svc.create_session(_purchases_csv(), format_hint="purchases")
        sid = session["session_id"]
        q = next(q for q in session["questions"] if q["scope"] == "ENTITY")

        updated = svc.answer_question(sid, {
            "question_id": q["question_id"],
            "answer_type": "SELECTED_CANDIDATE",
            "selected_security_id": "XNYS:AAPL",
        })
        q_updated = next(
            uq for uq in updated["questions"] if uq["question_id"] == q["question_id"]
        )
        assert q_updated["answer"] == "SELECTED_CANDIDATE"
        assert q_updated["selected_security_id"] == "XNYS:AAPL"

    def test_entity_answer_advances_to_preview_ready(self):
        """All ENTITY questions answered → state advances to PREVIEW_READY."""
        svc = _make_import_service(preload_securities=[_AAPL_SEC])
        session = svc.create_session(_purchases_csv(), format_hint="purchases")
        sid = session["session_id"]
        q = next(q for q in session["questions"] if q["scope"] == "ENTITY")

        updated = svc.answer_question(sid, {
            "question_id": q["question_id"],
            "answer_type": "SELECTED_CANDIDATE",
            "selected_security_id": "XNYS:AAPL",
        })
        assert updated["state"] == "PREVIEW_READY"

    def test_fan_out_same_company(self):
        """Answering once resolves all rows with same empresa_normalized."""
        rows = [
            "2024\tApple Inc.\t10/01/2024\t182,50\t10\t1.825,00\t7,50\n",
            "2024\tApple Inc.\t11/01/2024\t183,00\t5\t915,00\t4,00\n",
        ]
        svc = _make_import_service(preload_securities=[_AAPL_SEC])
        session = svc.create_session(_purchases_csv(rows), format_hint="purchases")
        sid = session["session_id"]
        q = next(q for q in session["questions"] if q["scope"] == "ENTITY")
        svc.answer_question(sid, {
            "question_id": q["question_id"],
            "answer_type": "SELECTED_CANDIDATE",
            "selected_security_id": "XNYS:AAPL",
        })
        # Both rows should be resolved (state advanced to PREVIEW_READY)
        updated_session = svc.get_session(sid)
        assert updated_session["state"] == "PREVIEW_READY"

    def test_skip_company_marks_rows(self):
        svc = _make_import_service()
        session = svc.create_session(_dividends_csv(), format_hint="dividends")
        sid = session["session_id"]
        entity_qs = [q for q in session["questions"] if q["scope"] == "ENTITY"]

        # Skip both companies
        for q in entity_qs:
            svc.answer_question(sid, {
                "question_id": q["question_id"],
                "answer_type": "SKIPPED_COMPANY",
            })

        updated = svc.get_session(sid)
        assert updated["state"] == "PREVIEW_READY"

    def test_answer_nonexistent_session_raises(self):
        svc = _make_import_service()
        with pytest.raises(LookupError):
            svc.answer_question("imp_nonexistent", {
                "question_id": "q_123",
                "answer_type": "SKIPPED_COMPANY",
            })

    def test_answer_terminal_session_raises_state_error(self):
        svc = _make_import_service(preload_securities=[_AAPL_SEC, _TEF_SEC])
        session = svc.create_session(_dividends_csv(), format_hint="dividends")
        sid = session["session_id"]

        # Answer all questions
        for q in session["questions"]:
            security_id = "XNYS:AAPL" if "apple" in q["normalized_name"] else "XMAD:TEF"
            svc.answer_question(sid, {
                "question_id": q["question_id"],
                "answer_type": "SELECTED_CANDIDATE",
                "selected_security_id": security_id,
            })

        svc.generate_preview(sid)
        svc.commit_session(sid)

        with pytest.raises(StateError):
            svc.answer_question(sid, {
                "question_id": "q_any",
                "answer_type": "SKIPPED_COMPANY",
            })


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------

class TestGeneratePreview:
    def test_preview_with_resolved_entities(self):
        svc = _make_import_service(preload_securities=[_AAPL_SEC])
        session = svc.create_session(_purchases_csv(), format_hint="purchases")
        sid = session["session_id"]
        q = next(q for q in session["questions"] if q["scope"] == "ENTITY")
        svc.answer_question(sid, {
            "question_id": q["question_id"],
            "answer_type": "SELECTED_CANDIDATE",
            "selected_security_id": "XNYS:AAPL",
        })

        preview = svc.generate_preview(sid)
        assert preview["state"] == "PREVIEW_READY"
        assert len(preview["preview"]["movements"]) == 1
        m = preview["preview"]["movements"][0]
        assert m["txn_type"] == "BUY"
        assert m["security_id"] == "XNYS:AAPL"

    def test_preview_with_unresolved_raises(self):
        svc = _make_import_service()
        session = svc.create_session(_dividends_csv(), format_hint="dividends")
        sid = session["session_id"]
        # Don't answer any questions
        with pytest.raises(UnresolvedQuestionsError):
            svc.generate_preview(sid)

    def test_negative_inventory_non_blocking_warning(self):
        """SELL before BUY produces NEGATIVE_INVENTORY warning, not error."""
        svc = _make_import_service(preload_securities=[_AAPL_SEC])
        session = svc.create_session(_sales_csv(), format_hint="sales")
        sid = session["session_id"]
        q = session["questions"][0]
        svc.answer_question(sid, {
            "question_id": q["question_id"],
            "answer_type": "SELECTED_CANDIDATE",
            "selected_security_id": "XNYS:AAPL",
        })

        # Preview should succeed (not raise)
        preview = svc.generate_preview(sid)
        assert preview["state"] == "PREVIEW_READY"
        warning_types = [w["type"] for w in preview["preview"]["warnings"]]
        assert "NEGATIVE_INVENTORY" in warning_types

    def test_skipped_company_excluded_from_preview(self):
        svc = _make_import_service(preload_securities=[_AAPL_SEC])
        session = svc.create_session(_dividends_csv(), format_hint="dividends")
        sid = session["session_id"]
        entity_qs = [q for q in session["questions"] if q["scope"] == "ENTITY"]

        for q in entity_qs:
            if "apple" in q["normalized_name"]:
                svc.answer_question(sid, {
                    "question_id": q["question_id"],
                    "answer_type": "SELECTED_CANDIDATE",
                    "selected_security_id": "XNYS:AAPL",
                })
            else:
                svc.answer_question(sid, {
                    "question_id": q["question_id"],
                    "answer_type": "SKIPPED_COMPANY",
                })

        preview = svc.generate_preview(sid)
        # Only Apple rows should be in movements
        movements = preview["preview"]["movements"]
        sids = {m["security_id"] for m in movements}
        assert "XNYS:AAPL" in sids
        # Skipped rows in skip_reasons
        assert preview["preview"]["skipped_rows"] >= 1

    def test_options_preview_uses_existing_net_formula_with_negative_commission(self):
        svc = _make_import_service(preload_securities=[_AAPL_SEC])
        acct = _create_account(svc.portfolio_svc)
        session = svc.create_session(
            _options_csv(rows=[
                "AAPL\tCALL_SELL\t19/07/2024\t210\t19/07/2024\t150.00\t138.00\t3.25\t26/07/2024\tIB Main\n",
                "AAPL\tPUT_BUY\t16/08/2024\t180\t16/08/2024\t80.00\t74.00\t-1.25\t\tIB Main\n",
            ], with_close_date=True),
            format_hint="options",
        )
        sid = session["session_id"]
        q = session["questions"][0]
        svc.answer_question(sid, {
            "question_id": q["question_id"],
            "answer_type": "SELECTED_CANDIDATE",
            "selected_security_id": "XNYS:AAPL",
        })
        preview = svc.generate_preview(sid)
        movements = sorted(preview["preview"]["movements"], key=lambda m: m["trade_date"])
        sell = next(m for m in movements if m["txn_type"] == "CALL_SELL")
        buy = next(m for m in movements if m["txn_type"] == "PUT_BUY")
        assert sell["net_eur"] == "134.750000"
        assert buy["net_eur"] == "72.750000"

        committed = svc.commit_session(sid)
        assert committed["state"] == "COMMITTED"
        txns = [
            v for v in svc.portfolio_svc.portfolio_container._store.values()
            if v.get("doc_type") == "ledger_txn"
        ]
        txns_by_type = {t["txn_type"]: t for t in txns}
        assert txns_by_type["CALL_SELL"]["account_id"] == acct["account_id"]
        assert txns_by_type["CALL_SELL"]["option_position_id"] is None
        assert txns_by_type["CALL_SELL"]["option_link_kind"] == "OPEN_SELL"
        assert txns_by_type["CALL_SELL"]["option_type"] == "call"
        assert txns_by_type["CALL_SELL"]["option_close_date"] == "2024-07-26"
        assert txns_by_type["PUT_BUY"]["option_link_kind"] == "CLOSE_BUY"
        assert txns_by_type["PUT_BUY"]["option_type"] == "put"
        assert txns_by_type["PUT_BUY"]["option_close_date"] is None

    def test_probable_duplicate_suppressed_when_dup_is_own_row(self):
        """Re-importing a row that will overwrite itself in place (same
        deterministic id) must not raise a PROBABLE_DUPLICATE warning."""
        svc = _make_import_service(preload_securities=[_AAPL_SEC])
        _create_account(svc.portfolio_svc)
        session = svc.create_session(
            _options_csv(rows=[
                "AAPL\tCALL_SELL\t19/07/2024\t210\t19/07/2024\t150.00\t138.00\t3.25\t26/07/2024\tIB Main\n",
            ], with_close_date=True),
            format_hint="options",
        )
        sid = session["session_id"]
        q = session["questions"][0]
        svc.answer_question(sid, {
            "question_id": q["question_id"],
            "answer_type": "SELECTED_CANDIDATE",
            "selected_security_id": "XNYS:AAPL",
        })

        doc = svc.portfolio_svc.get_session(sid)

        # No prior committed movement -> no dup found, no warning.
        svc.portfolio_svc.find_probable_duplicate = lambda *a, **k: None
        movements, warnings1, _ = _build_preview_movements(doc, svc.portfolio_svc)
        own_id = movements[0]["id"]
        assert not any(w["type"] == "PROBABLE_DUPLICATE" for w in warnings1)

        # "Duplicate" found is this exact row's own deterministic id -> the
        # row will simply be upserted in place; no warning expected.
        svc.portfolio_svc.find_probable_duplicate = lambda *a, **k: {"id": own_id}
        _, warnings2, _ = _build_preview_movements(doc, svc.portfolio_svc)
        assert not any(w["type"] == "PROBABLE_DUPLICATE" for w in warnings2)

        # A genuinely different existing movement matches the fuzzy fields ->
        # still warn, since this is a real possible duplicate.
        svc.portfolio_svc.find_probable_duplicate = lambda *a, **k: {"id": "some_other_txn_id"}
        _, warnings3, _ = _build_preview_movements(doc, svc.portfolio_svc)
        assert any(w["type"] == "PROBABLE_DUPLICATE" for w in warnings3)

    def test_options_parser_invalid_type_reaches_session_creation_error(self):
        svc = _make_import_service()
        _create_account(svc.portfolio_svc)
        with pytest.raises(ValueError, match="Row 2: Invalid Tipo value"):
            svc.create_session(
                _options_csv(rows=[
                    "AAPL\tOTHER\t19/07/2024\t210\t19/07/2024\t150.00\t138.00\t3.25\tacct_ib_main\n",
                ]),
                format_hint="options",
            )


# ---------------------------------------------------------------------------
# Commit
# ---------------------------------------------------------------------------

class TestCommitSession:
    def _setup_and_answer(self, svc, content, format_hint, security_id, entity_qs_map=None):
        session = svc.create_session(content, format_hint=format_hint)
        sid = session["session_id"]
        for q in session["questions"]:
            if q["scope"] == "ENTITY":
                mapped = security_id
                if entity_qs_map:
                    mapped = entity_qs_map.get(q["normalized_name"], security_id)
                svc.answer_question(sid, {
                    "question_id": q["question_id"],
                    "answer_type": "SELECTED_CANDIDATE",
                    "selected_security_id": mapped,
                })
        return sid

    def test_commit_writes_movements(self):
        svc = _make_import_service(preload_securities=[_AAPL_SEC])
        sid = self._setup_and_answer(svc, _purchases_csv(), "purchases", "XNYS:AAPL")
        svc.generate_preview(sid)
        result = svc.commit_session(sid)
        assert result["state"] == "COMMITTED"
        assert result["committed_count"] >= 1

    def test_already_committed_raises(self):
        svc = _make_import_service(preload_securities=[_AAPL_SEC])
        sid = self._setup_and_answer(svc, _purchases_csv(), "purchases", "XNYS:AAPL")
        svc.generate_preview(sid)
        svc.commit_session(sid)
        with pytest.raises(AlreadyCommittedError):
            svc.commit_session(sid)

    def test_commit_without_preview_ok(self):
        """commit_session implicitly builds movements if preview not done."""
        svc = _make_import_service(preload_securities=[_AAPL_SEC])
        sid = self._setup_and_answer(svc, _purchases_csv(), "purchases", "XNYS:AAPL")
        # No explicit generate_preview call
        result = svc.commit_session(sid)
        assert result["state"] == "COMMITTED"

    def test_commit_idempotency_hash_set(self):
        svc = _make_import_service(preload_securities=[_AAPL_SEC])
        portfolio_svc = svc.portfolio_svc
        sid = self._setup_and_answer(svc, _purchases_csv(), "purchases", "XNYS:AAPL")
        svc.generate_preview(sid)
        svc.commit_session(sid)
        # Check that committed movements have idempotency_hash
        movements = list(portfolio_svc.portfolio_container._store.values())
        ledger_txns = [m for m in movements if m.get("doc_type") == "ledger_txn"]
        assert len(ledger_txns) >= 1
        assert all("idempotency_hash" in m for m in ledger_txns)


# ---------------------------------------------------------------------------
# Inline security creation
# ---------------------------------------------------------------------------

class TestInlineCreateSecurity:
    def test_inline_create_resolves_entity(self):
        svc = _make_import_service()
        session = svc.create_session(_purchases_csv(), format_hint="purchases")
        sid = session["session_id"]
        q = next(q for q in session["questions"] if q["scope"] == "ENTITY")

        updated = svc.inline_create_security(sid, q["question_id"], {
            "ticker": "AAPL",
            "company_name": "Apple Inc.",
            "exchange_mic": "XNYS",
            "listing_currency": "USD",
        })
        q_updated = next(
            uq for uq in updated["questions"] if uq["question_id"] == q["question_id"]
        )
        assert q_updated["answer"] == "CREATED_NEW_SECURITY"
        assert q_updated["selected_security_id"] == "XNYS:AAPL"


# ---------------------------------------------------------------------------
# F1 — Commission preserved in fees
# ---------------------------------------------------------------------------

class TestCommissionInFees:
    def _commit_and_get_txn(self, svc, content, fmt, security_id):
        """Helper: answer, preview, commit, return first ledger_txn."""
        session = svc.create_session(content, format_hint=fmt)
        sid = session["session_id"]
        q = next(q for q in session["questions"] if q["scope"] == "ENTITY")
        svc.answer_question(sid, {
            "question_id": q["question_id"],
            "answer_type": "SELECTED_CANDIDATE",
            "selected_security_id": security_id,
        })
        svc.generate_preview(sid)
        svc.commit_session(sid)
        txns = [
            v for v in svc.portfolio_svc.portfolio_container._store.values()
            if v.get("doc_type") == "ledger_txn"
        ]
        return txns[0]

    def test_purchase_commission_in_fees(self):
        """Purchase Comisión 7,50 → fees.total_eur reflects commission."""
        # _purchases_csv() default row has Comisión = 7,50
        svc = _make_import_service(preload_securities=[_AAPL_SEC])
        txn = self._commit_and_get_txn(svc, _purchases_csv(), "purchases", "XNYS:AAPL")
        assert Decimal(txn["fees"]["total_eur"]) == Decimal("7.50")
        assert Decimal(txn["fees"]["total"]) == Decimal("7.50")

    def test_sale_commission_in_fees(self):
        """Sale with Comisión 5,00 → fees.total_eur == '5.00'."""
        rows = ["2024\tApple Inc.\t20/06/2024\t5\t5,00\t1.050,00\n"]
        svc = _make_import_service(preload_securities=[_AAPL_SEC])
        txn = self._commit_and_get_txn(svc, _sales_csv(rows), "sales", "XNYS:AAPL")
        assert Decimal(txn["fees"]["total_eur"]) == Decimal("5.00")
        assert Decimal(txn["fees"]["total"]) == Decimal("5.00")

    def test_dividend_fees_zero(self):
        """Dividend movements always have fees.total_eur == '0.00'."""
        # Single-company CSV to avoid unresolved-question blocker
        single_row = ["2024\tApple Inc.\t15/06/2024\t100,00\t73,31\t0,00\t12,94\t13,75\n"]
        svc = _make_import_service(preload_securities=[_AAPL_SEC])
        txn = self._commit_and_get_txn(svc, _dividends_csv(single_row), "dividends", "XNYS:AAPL")
        assert txn["fees"]["total_eur"] == "0.00"
        assert txn["fees"]["total"] == "0.00"


# ---------------------------------------------------------------------------
# F3 — Preview includes company_name from security catalog
# ---------------------------------------------------------------------------

class TestPreviewCompanyName:
    def test_preview_company_name_from_security_master(self):
        """Preview movement company_name must equal the catalog's company_name."""
        svc = _make_import_service(preload_securities=[_AAPL_SEC])
        session = svc.create_session(_purchases_csv(), format_hint="purchases")
        sid = session["session_id"]
        q = next(q for q in session["questions"] if q["scope"] == "ENTITY")
        svc.answer_question(sid, {
            "question_id": q["question_id"],
            "answer_type": "SELECTED_CANDIDATE",
            "selected_security_id": "XNYS:AAPL",
        })
        preview = svc.generate_preview(sid)
        m = preview["preview"]["movements"][0]
        assert m["company_name"] == "Apple Inc."

    def test_preview_company_name_nonempty(self):
        """Every preview movement must contain a non-empty company_name key."""
        svc = _make_import_service(preload_securities=[_AAPL_SEC])
        session = svc.create_session(_purchases_csv(), format_hint="purchases")
        sid = session["session_id"]
        q = next(q for q in session["questions"] if q["scope"] == "ENTITY")
        svc.answer_question(sid, {
            "question_id": q["question_id"],
            "answer_type": "SELECTED_CANDIDATE",
            "selected_security_id": "XNYS:AAPL",
        })
        preview = svc.generate_preview(sid)
        for movement in preview["preview"]["movements"]:
            assert "company_name" in movement
            assert movement["company_name"] != ""


# ---------------------------------------------------------------------------
# F5 — Dividend quantity is null; BUY/SELL quantities are non-null
# ---------------------------------------------------------------------------

class TestQuantityNullability:
    def _resolve_and_preview(self, csv, fmt, security_id):
        svc = _make_import_service(preload_securities=[_AAPL_SEC])
        session = svc.create_session(csv, format_hint=fmt)
        sid = session["session_id"]
        q = next(q for q in session["questions"] if q["scope"] == "ENTITY")
        svc.answer_question(sid, {
            "question_id": q["question_id"],
            "answer_type": "SELECTED_CANDIDATE",
            "selected_security_id": security_id,
        })
        return svc.generate_preview(sid)

    def test_dividend_quantity_null(self):
        """Dividend movements must have quantity is None (not '0' or '1')."""
        single_row = ["2024\tApple Inc.\t15/06/2024\t100,00\t73,31\t0,00\t12,94\t13,75\n"]
        preview = self._resolve_and_preview(_dividends_csv(single_row), "dividends", "XNYS:AAPL")
        m = preview["preview"]["movements"][0]
        assert m["txn_type"] == "DIVIDEND"
        assert m["quantity"] is None, f"Expected None, got {m['quantity']!r}"

    def test_buy_quantity_present(self):
        """BUY movements must have a non-null quantity string."""
        preview = self._resolve_and_preview(_purchases_csv(), "purchases", "XNYS:AAPL")
        m = preview["preview"]["movements"][0]
        assert m["txn_type"] == "BUY"
        assert m["quantity"] is not None
        assert Decimal(m["quantity"]) > 0

    def test_sell_quantity_present(self):
        """SELL movements must have a non-null quantity string."""
        preview = self._resolve_and_preview(_sales_csv(), "sales", "XNYS:AAPL")
        m = preview["preview"]["movements"][0]
        assert m["txn_type"] == "SELL"
        assert m["quantity"] is not None
        assert Decimal(m["quantity"]) > 0


# ---------------------------------------------------------------------------
# Rights sales import round-trip (danny-rights-sale-contract)
# Tests cover: 6-col default, 7-col explicit types, preview shape, commit
# persistence, and in-batch NEGATIVE_INVENTORY suppression for DERECHOS.
# Tests will fail until Livingston's implementation is merged; assertions are
# intentionally un-weakened so the failures are explicit.
# ---------------------------------------------------------------------------

def _sales_csv_7col(rows=None):
    """7-column sales CSV fixture. Authoritative column order:
    Año | Empresa | Fecha venta | Tipo | Acciones | Comisión | Total Venta
    """
    header = "Año\tEmpresa\tFecha venta\tTipo\tAcciones\tComisión\tTotal Venta\n"
    if rows is None:
        rows = ["2024\tApple Inc.\t20/06/2024\tAcciones\t5\t7,50\t1.050,00\n"]
    return (header + "".join(rows)).encode("utf-8")


def _sales_csv_7col_derechos(rows=None):
    """7-column sales CSV fixture with a Derechos row (qty=0 for a clean signal)."""
    header = "Año\tEmpresa\tFecha venta\tTipo\tAcciones\tComisión\tTotal Venta\n"
    if rows is None:
        rows = ["2024\tApple Inc.\t20/06/2024\tDerechos\t0\t4,00\t300,00\n"]
    return (header + "".join(rows)).encode("utf-8")


class TestImportSalesSalesType:
    """sales_type and is_rights_sale round-trip through preview and commit."""

    def _do_full_import(self, svc, content, security_id):
        """Create → answer → preview → commit. Returns (sid, list[ledger_txn])."""
        session = svc.create_session(content, format_hint="sales")
        sid = session["session_id"]
        q = next(q for q in session["questions"] if q["scope"] == "ENTITY")
        svc.answer_question(sid, {
            "question_id": q["question_id"],
            "answer_type": "SELECTED_CANDIDATE",
            "selected_security_id": security_id,
        })
        svc.generate_preview(sid)
        svc.commit_session(sid)
        txns = [
            v for v in svc.portfolio_svc.portfolio_container._store.values()
            if v.get("doc_type") == "ledger_txn"
        ]
        return sid, txns

    def _do_preview(self, svc, content, security_id):
        """Create → answer → preview. Returns preview response."""
        session = svc.create_session(content, format_hint="sales")
        sid = session["session_id"]
        q = next(q for q in session["questions"] if q["scope"] == "ENTITY")
        svc.answer_question(sid, {
            "question_id": q["question_id"],
            "answer_type": "SELECTED_CANDIDATE",
            "selected_security_id": security_id,
        })
        return svc.generate_preview(sid)

    # --- 6-column (legacy) CSV ------------------------------------------------

    def test_6col_committed_movement_has_acciones(self):
        """6-col SELL committed to ledger carries sales_type='ACCIONES'."""
        svc = _make_import_service(preload_securities=[_AAPL_SEC])
        _, txns = self._do_full_import(svc, _sales_csv(), "XNYS:AAPL")
        assert len(txns) == 1
        assert txns[0].get("sales_type") == "ACCIONES"

    def test_6col_committed_movement_is_rights_sale_false(self):
        """6-col SELL committed to ledger carries is_rights_sale=False."""
        svc = _make_import_service(preload_securities=[_AAPL_SEC])
        _, txns = self._do_full_import(svc, _sales_csv(), "XNYS:AAPL")
        assert txns[0].get("is_rights_sale") is False

    # --- 7-column CSV — ACCIONES row -----------------------------------------

    def test_7col_acciones_commit_has_acciones(self):
        """7-col CSV with Tipo='Acciones' → ledger doc has sales_type='ACCIONES'."""
        svc = _make_import_service(preload_securities=[_AAPL_SEC])
        _, txns = self._do_full_import(svc, _sales_csv_7col(), "XNYS:AAPL")
        assert len(txns) == 1
        assert txns[0].get("sales_type") == "ACCIONES"
        assert txns[0].get("is_rights_sale") is False

    def test_7col_acciones_preview_includes_sales_type(self):
        """Preview movement for Acciones row exposes sales_type='ACCIONES'."""
        svc = _make_import_service(preload_securities=[_AAPL_SEC])
        preview = self._do_preview(svc, _sales_csv_7col(), "XNYS:AAPL")
        movements = preview["preview"]["movements"]
        assert len(movements) == 1
        assert movements[0].get("sales_type") == "ACCIONES"

    # --- 7-column CSV — DERECHOS row -----------------------------------------

    def test_7col_derechos_commit_has_derechos(self):
        """7-col CSV with Tipo='Derechos' → ledger doc has sales_type='DERECHOS'."""
        svc = _make_import_service(preload_securities=[_AAPL_SEC])
        _, txns = self._do_full_import(svc, _sales_csv_7col_derechos(), "XNYS:AAPL")
        assert len(txns) == 1
        assert txns[0].get("sales_type") == "DERECHOS"

    def test_7col_derechos_commit_is_rights_sale_true(self):
        """7-col CSV with Tipo='Derechos' → ledger doc has is_rights_sale=True."""
        svc = _make_import_service(preload_securities=[_AAPL_SEC])
        _, txns = self._do_full_import(svc, _sales_csv_7col_derechos(), "XNYS:AAPL")
        assert txns[0].get("is_rights_sale") is True

    def test_7col_derechos_preview_includes_sales_type(self):
        """Preview movement for Derechos row exposes sales_type='DERECHOS'."""
        svc = _make_import_service(preload_securities=[_AAPL_SEC])
        preview = self._do_preview(svc, _sales_csv_7col_derechos(), "XNYS:AAPL")
        movements = preview["preview"]["movements"]
        assert len(movements) == 1
        assert movements[0].get("sales_type") == "DERECHOS"

    # --- Mixed 7-column CSV ---------------------------------------------------

    def test_7col_mixed_commit_carries_correct_types(self):
        """Mixed 7-col CSV: each committed movement preserves its own sales_type."""
        rows = [
            "2024\tApple Inc.\t20/06/2024\tAcciones\t5\t7,50\t1.050,00\n",
            "2024\tApple Inc.\t25/06/2024\tDerechos\t0\t4,00\t300,00\n",
        ]
        svc = _make_import_service(preload_securities=[_AAPL_SEC])
        _, txns = self._do_full_import(svc, _sales_csv_7col(rows), "XNYS:AAPL")
        assert len(txns) == 2
        types_by_date = {t["trade_date"]: t.get("sales_type") for t in txns}
        assert types_by_date.get("2024-06-20") == "ACCIONES"
        assert types_by_date.get("2024-06-25") == "DERECHOS"

    def test_7col_mixed_preview_carries_correct_types(self):
        """Mixed 7-col CSV preview: each movement shape preserves its sales_type."""
        rows = [
            "2024\tApple Inc.\t20/06/2024\tAcciones\t5\t7,50\t1.050,00\n",
            "2024\tApple Inc.\t25/06/2024\tDerechos\t0\t4,00\t300,00\n",
        ]
        svc = _make_import_service(preload_securities=[_AAPL_SEC])
        preview = self._do_preview(svc, _sales_csv_7col(rows), "XNYS:AAPL")
        movements = preview["preview"]["movements"]
        assert len(movements) == 2
        present_types = {m.get("sales_type") for m in movements}
        assert present_types == {"ACCIONES", "DERECHOS"}


class TestImportSalesDerechosNegativeInventory:
    """DERECHOS sales must not contribute to the in-batch NEGATIVE_INVENTORY check."""

    def test_derechos_only_import_no_negative_inventory_warning(self):
        """Importing a single DERECHOS sale with no prior buys must NOT produce
        a NEGATIVE_INVENTORY warning (DERECHOS does not decrement share count)."""
        svc = _make_import_service(preload_securities=[_AAPL_SEC])
        session = svc.create_session(_sales_csv_7col_derechos(), format_hint="sales")
        sid = session["session_id"]
        q = next(q for q in session["questions"] if q["scope"] == "ENTITY")
        svc.answer_question(sid, {
            "question_id": q["question_id"],
            "answer_type": "SELECTED_CANDIDATE",
            "selected_security_id": "XNYS:AAPL",
        })
        preview = svc.generate_preview(sid)
        warning_types = [w["type"] for w in preview["preview"]["warnings"]]
        assert "NEGATIVE_INVENTORY" not in warning_types

    def test_acciones_only_import_still_warns_on_negative_inventory(self):
        """Existing behaviour preserved: ACCIONES sale with no buys keeps the
        NEGATIVE_INVENTORY warning (non-blocking, just a signal)."""
        svc = _make_import_service(preload_securities=[_AAPL_SEC])
        # 6-column CSV → ACCIONES by default
        session = svc.create_session(_sales_csv(), format_hint="sales")
        sid = session["session_id"]
        q = session["questions"][0]
        svc.answer_question(sid, {
            "question_id": q["question_id"],
            "answer_type": "SELECTED_CANDIDATE",
            "selected_security_id": "XNYS:AAPL",
        })
        preview = svc.generate_preview(sid)
        warning_types = [w["type"] for w in preview["preview"]["warnings"]]
        assert "NEGATIVE_INVENTORY" in warning_types
