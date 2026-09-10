"""Tests for the three domain CSV parsers.

All tests are hermetic: no Cosmos connectivity, no network.
Synthetic fixtures only.
"""

import pytest
from decimal import Decimal

from src.portfolio.parsers.dividends import parse_dividends
from src.portfolio.parsers.options import parse_options
from src.portfolio.parsers.purchases import parse_purchases
from src.portfolio.parsers.sales import parse_sales
from src.portfolio.parsers.common import (
    parse_spanish_decimal,
    parse_spanish_date,
    normalize_company_name,
    row_idempotency_hash,
)


# ---------------------------------------------------------------------------
# Shared CSV helpers
# ---------------------------------------------------------------------------

def _encode(text: str) -> bytes:
    return text.encode("utf-8")


# ---------------------------------------------------------------------------
# Common utilities
# ---------------------------------------------------------------------------

class TestParseSpanishDecimal:
    def test_simple_decimal(self):
        assert parse_spanish_decimal("123,45") == Decimal("123.45")

    def test_thousands_separator(self):
        assert parse_spanish_decimal("1.234,56") == Decimal("1234.56")

    def test_large_amount(self):
        assert parse_spanish_decimal("1.234.567,89") == Decimal("1234567.89")

    def test_zero(self):
        assert parse_spanish_decimal("0") == Decimal("0")

    def test_zero_comma(self):
        assert parse_spanish_decimal("0,00") == Decimal("0")

    def test_empty(self):
        assert parse_spanish_decimal("") is None

    def test_none(self):
        assert parse_spanish_decimal(None) is None

    def test_na(self):
        assert parse_spanish_decimal("N/A") is None

    def test_invalid(self):
        with pytest.raises(ValueError):
            parse_spanish_decimal("abc")

    # F2 regression — dot-only strings are thousands separators (not English decimals)
    def test_dot_only_is_thousands(self):
        """'1.234' is Spanish 1234, not English 1.234."""
        assert parse_spanish_decimal("1.234") == Decimal("1234")

    def test_dot_only_large(self):
        """'10.500' → 10500 (ten thousand five hundred)."""
        assert parse_spanish_decimal("10.500") == Decimal("10500")

    def test_dot_only_round_thousand(self):
        """'1.000' → 1000 (one thousand, not one)."""
        assert parse_spanish_decimal("1.000") == Decimal("1000")

    def test_no_dot_no_comma(self):
        """Plain integer string is unchanged."""
        assert parse_spanish_decimal("100") == Decimal("100")

    def test_comma_decimal_preserved(self):
        """Comma-decimal with dot thousands is unchanged."""
        assert parse_spanish_decimal("1.234,56") == Decimal("1234.56")

    def test_comma_only_decimal(self):
        """Comma-only decimal string is parsed correctly."""
        assert parse_spanish_decimal("0,50") == Decimal("0.50")

    def test_plain_dot_decimal_supported(self):
        """Single-dot decimals with 1-2 decimal digits are accepted."""
        assert parse_spanish_decimal("150.25") == Decimal("150.25")


class TestParseSpanishDate:
    def test_valid(self):
        assert parse_spanish_date("15/06/2024") == "2024-06-15"

    def test_single_digits(self):
        assert parse_spanish_date("01/01/2024") == "2024-01-01"

    def test_empty(self):
        assert parse_spanish_date("") is None

    def test_none(self):
        assert parse_spanish_date(None) is None

    # ISO pass-through — formerly raised ValueError (pre-fix regression anchor)
    def test_iso_passthrough(self):
        """Already-normalised ISO YYYY-MM-DD is accepted unchanged."""
        assert parse_spanish_date("2024-06-15") == "2024-06-15"

    def test_iso_passthrough_old_date(self):
        """Reproduces the production bug: '2016-07-18' must not raise."""
        assert parse_spanish_date("2016-07-18") == "2016-07-18"

    def test_spanish_normalises_to_iso(self):
        """Spanish 18/07/2016 normalises to ISO 2016-07-18 exactly once."""
        assert parse_spanish_date("18/07/2016") == "2016-07-18"

    def test_iso_idempotent(self):
        """Calling parse_spanish_date twice on the same value is safe."""
        first = parse_spanish_date("18/07/2016")
        second = parse_spanish_date(first)
        assert second == "2016-07-18"

    def test_iso_calendar_invalid_raises(self):
        """ISO with out-of-range day raises ValueError."""
        with pytest.raises(ValueError, match="Cannot parse date"):
            parse_spanish_date("2024-02-30")

    def test_spanish_calendar_invalid_raises(self):
        """Spanish with month 13 raises ValueError."""
        with pytest.raises(ValueError, match="Cannot parse date"):
            parse_spanish_date("01/13/2024")

    def test_invalid_format(self):
        with pytest.raises(ValueError):
            parse_spanish_date("not-a-date")

    def test_us_date_high_month_rejected(self):
        """US MM/DD/YYYY with day > 12 read as Spanish produces month > 12 → rejected."""
        with pytest.raises(ValueError):
            parse_spanish_date("07/18/2016")  # day=07, month=18 → invalid


class TestNormalizeCompanyName:
    def test_lowercase(self):
        assert normalize_company_name("Apple Inc.") == "apple inc."

    def test_accents_stripped(self):
        assert normalize_company_name("Telefónica") == "telefonica"

    def test_whitespace_collapsed(self):
        assert normalize_company_name("  Apple  Inc  ") == "apple inc"

    def test_empty(self):
        assert normalize_company_name("") == ""


class TestRowIdempotencyHash:
    def test_deterministic(self):
        h1 = row_idempotency_hash("XNYS:AAPL", "DIVIDEND", "2024-06-15", Decimal("100"), Decimal("86.25"))
        h2 = row_idempotency_hash("XNYS:AAPL", "DIVIDEND", "2024-06-15", Decimal("100"), Decimal("86.25"))
        assert h1 == h2

    def test_different_inputs(self):
        h1 = row_idempotency_hash("XNYS:AAPL", "DIVIDEND", "2024-06-15", Decimal("100"), Decimal("86.25"))
        h2 = row_idempotency_hash("XMAD:TEF", "DIVIDEND", "2024-06-15", Decimal("100"), Decimal("86.25"))
        assert h1 != h2


# ---------------------------------------------------------------------------
# Dividends parser
# ---------------------------------------------------------------------------

DIVIDENDS_CSV_TAB = """\
Año\tEmpresa\tFecha de cobro\tImporte Bruto\tImporte Neto\tImporte en Derechos\tRetención Origen\tRetención Destino
2024\tApple Inc.\t15/06/2024\t100,00\t73,31\t0,00\t12,94\t13,75
2024\tTelefónica\t20/06/2024\t50,00\t38,00\t0,00\t7,50\t4,50
"""

DIVIDENDS_CSV_SEMICOLON = """\
Año;Empresa;Fecha de cobro;Importe Bruto;Importe Neto;Importe en Derechos;Retención Origen;Retención Destino
2024;Apple Inc.;15/06/2024;100,00;73,31;0,00;12,94;13,75
"""

DIVIDENDS_CSV_COMMA = """\
Año,Empresa,Fecha de cobro,Importe Bruto,Importe Neto,Importe en Derechos,Retención Origen,Retención Destino
2024,Apple Inc.,15/06/2024,"100,00","73,31","0,00","12,94","13,75"
"""

DIVIDENDS_CSV_DERECHOS = """\
Año\tEmpresa\tFecha de cobro\tImporte Bruto\tImporte Neto\tImporte en Derechos\tRetención Origen\tRetención Destino
2024\tSantander\t10/06/2024\t80,00\t60,00\t45,30\t12,00\t8,00
"""


class TestDividendsParser:
    def test_tab_delimiter(self):
        rows = parse_dividends(_encode(DIVIDENDS_CSV_TAB))
        assert len(rows) == 2

    def test_semicolon_delimiter(self):
        rows = parse_dividends(_encode(DIVIDENDS_CSV_SEMICOLON))
        assert len(rows) == 1

    def test_correct_amounts(self):
        rows = parse_dividends(_encode(DIVIDENDS_CSV_TAB))
        first = rows[0]
        assert first["gross"] == Decimal("100.00")
        assert first["net"] == Decimal("73.31")
        assert first["wht_source"] == Decimal("12.94")
        assert first["wht_destination"] == Decimal("13.75")

    def test_correct_date(self):
        rows = parse_dividends(_encode(DIVIDENDS_CSV_TAB))
        assert rows[0]["payment_date"] == "2024-06-15"

    def test_empresa_normalized(self):
        rows = parse_dividends(_encode(DIVIDENDS_CSV_TAB))
        assert rows[1]["empresa_normalized"] == "telefonica"

    def test_rights_amount_warning(self):
        rows = parse_dividends(_encode(DIVIDENDS_CSV_DERECHOS))
        assert len(rows) == 1
        row = rows[0]
        assert row["derechos"] == Decimal("45.30")
        assert len(row["warnings"]) == 1
        assert row["warnings"][0]["type"] == "RIGHTS_AMOUNT"
        assert row["warnings"][0]["amount"] == "45.30"

    def test_no_rights_warning_when_zero(self):
        rows = parse_dividends(_encode(DIVIDENDS_CSV_TAB))
        for row in rows:
            assert not any(w["type"] == "RIGHTS_AMOUNT" for w in row["warnings"])

    def test_row_index_zero_based(self):
        rows = parse_dividends(_encode(DIVIDENDS_CSV_TAB))
        assert rows[0]["row_index"] == 0
        assert rows[1]["row_index"] == 1

    def test_source_row_preserved(self):
        rows = parse_dividends(_encode(DIVIDENDS_CSV_TAB))
        assert "source_row" in rows[0]
        assert isinstance(rows[0]["source_row"], dict)

    def test_empty_file_raises(self):
        with pytest.raises(ValueError):
            parse_dividends(b"")

    def test_header_only_raises(self):
        header = _encode(
            "Año\tEmpresa\tFecha de cobro\tImporte Bruto\t"
            "Importe Neto\tImporte en Derechos\tRetención Origen\tRetención Destino\n"
        )
        with pytest.raises(ValueError):
            parse_dividends(header)

    def test_wrong_columns_raises(self):
        with pytest.raises(ValueError):
            parse_dividends(_encode("Col1\tCol2\tCol3\n1\t2\t3\n"))

    def test_year_parsed(self):
        rows = parse_dividends(_encode(DIVIDENDS_CSV_TAB))
        assert rows[0]["year"] == 2024

    def test_spanish_thousands_in_amounts(self):
        csv = (
            "Año\tEmpresa\tFecha de cobro\tImporte Bruto\tImporte Neto\t"
            "Importe en Derechos\tRetención Origen\tRetención Destino\n"
            "2024\tCo\t15/06/2024\t1.234,56\t900,00\t0,00\t100,00\t234,56\n"
        )
        rows = parse_dividends(_encode(csv))
        assert rows[0]["gross"] == Decimal("1234.56")


# ---------------------------------------------------------------------------
# Purchases parser
# ---------------------------------------------------------------------------

PURCHASES_CSV = """\
Año\tEmpresa\tFecha compra\tValor compra\tAcciones\tTotal (€)\tComisión
2024\tApple Inc.\t10/01/2024\t182,50\t10\t1.825,00\t7,50
2024\tTelefónica\t15/03/2024\t3,80\t100\t380,00\t4,00
"""

PURCHASES_CSV_ZERO_COST = """\
Año\tEmpresa\tFecha compra\tValor compra\tAcciones\tTotal (€)\tComisión
2024\tTelefónica\t20/06/2024\t0\t50\t0,00\t0,00
"""


class TestPurchasesParser:
    def test_basic_parse(self):
        rows = parse_purchases(_encode(PURCHASES_CSV))
        assert len(rows) == 2

    def test_amounts(self):
        rows = parse_purchases(_encode(PURCHASES_CSV))
        assert rows[0]["price_per_share"] == Decimal("182.50")
        assert rows[0]["quantity"] == Decimal("10")
        assert rows[0]["total_cost"] == Decimal("1825.00")
        assert rows[0]["commission"] == Decimal("7.50")

    def test_date(self):
        rows = parse_purchases(_encode(PURCHASES_CSV))
        assert rows[0]["purchase_date"] == "2024-01-10"

    def test_cost_basis_complete(self):
        rows = parse_purchases(_encode(PURCHASES_CSV))
        assert rows[0]["cost_basis_status"] == "COMPLETE"
        assert not any(w["type"] == "ZERO_COST_ACQUISITION" for w in rows[0]["warnings"])

    def test_zero_cost_acquisition(self):
        rows = parse_purchases(_encode(PURCHASES_CSV_ZERO_COST))
        assert len(rows) == 1
        row = rows[0]
        # Zero-price explicit BUY is ZERO_COST (scrip dividend): legitimate, no warning.
        assert row["cost_basis_status"] == "ZERO_COST"
        assert row["price_per_share"] == Decimal("0")
        assert row["quantity"] == Decimal("50")
        assert len(row["warnings"]) == 0

    def test_empty_file_raises(self):
        with pytest.raises(ValueError):
            parse_purchases(b"")

    def test_wrong_columns_raises(self):
        with pytest.raises(ValueError):
            parse_purchases(_encode("Col1\tCol2\n1\t2\n"))


# ---------------------------------------------------------------------------
# Sales parser
# ---------------------------------------------------------------------------

SALES_CSV = """\
Año\tEmpresa\tFecha venta\tAcciones\tComisión\tTotal Venta
2024\tApple Inc.\t20/06/2024\t5\t7,50\t1.050,00
2024\tTelefónica\t25/06/2024\t50\t4,00\t200,00
"""


class TestSalesParser:
    def test_basic_parse(self):
        rows = parse_sales(_encode(SALES_CSV))
        assert len(rows) == 2

    def test_amounts(self):
        rows = parse_sales(_encode(SALES_CSV))
        assert rows[0]["quantity"] == Decimal("5")
        assert rows[0]["commission"] == Decimal("7.50")
        assert rows[0]["total_proceeds"] == Decimal("1050.00")

    def test_date(self):
        rows = parse_sales(_encode(SALES_CSV))
        assert rows[0]["sale_date"] == "2024-06-20"

    def test_empresa_normalized(self):
        rows = parse_sales(_encode(SALES_CSV))
        assert rows[0]["empresa_normalized"] == "apple inc."

    def test_no_warnings_by_default(self):
        rows = parse_sales(_encode(SALES_CSV))
        for row in rows:
            assert row["warnings"] == []

    def test_empty_file_raises(self):
        with pytest.raises(ValueError):
            parse_sales(b"")

    def test_wrong_columns_raises(self):
        with pytest.raises(ValueError):
            parse_sales(_encode("Col1\tCol2\n1\t2\n"))


# ---------------------------------------------------------------------------
# Options parser
# ---------------------------------------------------------------------------

OPTIONS_CSV = """\
Símbolo\tTipo\tFecha\tStrike\tExpiración\tImporte USD Bruto\tImporte EUR Bruto\tComisión EUR\tCuenta
AAPL\tCALL_SELL\t19/07/2024\t210\t19/07/2024\t150.00\t138.00\t3.25\tacct_ib_main
MSFT\tput buy\t16/08/2024\t400\t16/08/2024\t80,00\t74,00\t-1,25\tacct_ib_main
"""

OPTIONS_CSV_WITH_NET = """\
Symbol\tType\tDate\tStrike\tExpiration\tGross USD\tGross EUR\tCommission EUR\tNet EUR\tAccount
AAPL\tcall sell\t19/07/2024\t210\t19/07/2024\t150.00\t138.00\t3.25\t134.75\tacct_ib_main
"""

OPTIONS_CSV_WITH_CLOSE_DATE = """\
Symbol\tType\tDate\tStrike\tExpiration\tGross USD\tGross EUR\tCommission EUR\tClose Date\tAccount
AAPL\tcall sell\t19/07/2024\t210\t19/07/2024\t150.00\t138.00\t3.25\t26/07/2024\tacct_ib_main
MSFT\tput buy\t16/08/2024\t400\t16/08/2024\t80,00\t74,00\t-1,25\t\tacct_ib_main
"""

OPTIONS_CSV_INVALID_TYPE = """\
Símbolo\tTipo\tFecha\tStrike\tExpiración\tImporte USD Bruto\tImporte EUR Bruto\tComisión EUR\tCuenta
AAPL\troll\t19/07/2024\t210\t19/07/2024\t150.00\t138.00\t3.25\tacct_ib_main
"""


class TestOptionsParser:
    def test_basic_parse(self):
        rows = parse_options(_encode(OPTIONS_CSV))
        assert len(rows) == 2

    def test_type_normalization(self):
        rows = parse_options(_encode(OPTIONS_CSV))
        assert rows[0]["txn_type"] == "CALL_SELL"
        assert rows[1]["txn_type"] == "PUT_BUY"

    def test_plain_decimal_point_numbers_supported(self):
        rows = parse_options(_encode(OPTIONS_CSV))
        assert rows[0]["gross_usd"] == Decimal("150.00")
        assert rows[0]["gross_eur"] == Decimal("138.00")

    def test_negative_commission_supported(self):
        rows = parse_options(_encode(OPTIONS_CSV))
        assert rows[1]["commission_eur"] == Decimal("-1.25")

    def test_optional_net_column_supported(self):
        rows = parse_options(_encode(OPTIONS_CSV_WITH_NET))
        assert rows[0]["net_eur"] == Decimal("134.75")

    def test_optional_close_date_column_supported(self):
        rows = parse_options(_encode(OPTIONS_CSV_WITH_CLOSE_DATE))
        assert rows[0]["option_close_date"] == "2024-07-26"
        assert rows[1]["option_close_date"] is None

    def test_invalid_type_raises_with_row_context(self):
        with pytest.raises(ValueError, match="Row 2: Invalid Tipo value"):
            parse_options(_encode(OPTIONS_CSV_INVALID_TYPE))


# ---------------------------------------------------------------------------
# Regression: ISO date pass-through across all three parsers
# ---------------------------------------------------------------------------
# Reproduces the production bug: user files that already contain ISO-formatted
# dates (e.g. after re-export or Excel normalisation) must not raise.

class TestIsoDatesRegressionPurchases:
    """Purchases: ISO YYYY-MM-DD in 'Fecha compra' column."""

    _CSV_ISO = (
        "Año\tEmpresa\tFecha compra\tValor compra\tAcciones\tTotal (€)\tComisión\n"
        "2016\tSome Corp\t2016-07-18\t10,00\t100\t1.000,00\t5,00\n"
    )

    def test_iso_date_accepted(self):
        """'2016-07-18' must not raise 'Cannot parse date'."""
        rows = parse_purchases(_encode(self._CSV_ISO))
        assert len(rows) == 1
        assert rows[0]["purchase_date"] == "2016-07-18"

    def test_spanish_date_still_normalises(self):
        csv = (
            "Año\tEmpresa\tFecha compra\tValor compra\tAcciones\tTotal (€)\tComisión\n"
            "2016\tSome Corp\t18/07/2016\t10,00\t100\t1.000,00\t5,00\n"
        )
        rows = parse_purchases(_encode(csv))
        assert rows[0]["purchase_date"] == "2016-07-18"

    def test_iso_and_spanish_mixed(self):
        """A file with both formats in different rows parses cleanly."""
        csv = (
            "Año\tEmpresa\tFecha compra\tValor compra\tAcciones\tTotal (€)\tComisión\n"
            "2016\tCorp A\t18/07/2016\t10,00\t100\t1.000,00\t5,00\n"
            "2017\tCorp B\t2017-03-15\t20,00\t50\t1.000,00\t5,00\n"
        )
        rows = parse_purchases(_encode(csv))
        assert rows[0]["purchase_date"] == "2016-07-18"
        assert rows[1]["purchase_date"] == "2017-03-15"


class TestIsoDatesRegressionDividends:
    """Dividends: ISO YYYY-MM-DD in 'Fecha de cobro' column."""

    _CSV_ISO = (
        "Año\tEmpresa\tFecha de cobro\tImporte Bruto\tImporte Neto\t"
        "Importe en Derechos\tRetención Origen\tRetención Destino\n"
        "2016\tSome Corp\t2016-07-18\t50,00\t38,00\t0,00\t7,50\t4,50\n"
    )

    def test_iso_date_accepted(self):
        rows = parse_dividends(_encode(self._CSV_ISO))
        assert len(rows) == 1
        assert rows[0]["payment_date"] == "2016-07-18"


class TestIsoDatesRegressionSales:
    """Sales: ISO YYYY-MM-DD in 'Fecha venta' column."""

    _CSV_ISO = (
        "Año\tEmpresa\tFecha venta\tAcciones\tComisión\tTotal Venta\n"
        "2016\tSome Corp\t2016-07-18\t50\t5,00\t600,00\n"
    )

    def test_iso_date_accepted(self):
        rows = parse_sales(_encode(self._CSV_ISO))
        assert len(rows) == 1
        assert rows[0]["sale_date"] == "2016-07-18"


# ---------------------------------------------------------------------------
# Sales parser — Tipo (4th column) support
# Authoritative header: Año | Empresa | Fecha venta | Tipo | Acciones | Comisión | Total Venta
# Regression coverage for danny-rights-sale-contract design.
# ---------------------------------------------------------------------------

_SALES_7COL_ACCIONES = (
    "Año\tEmpresa\tFecha venta\tTipo\tAcciones\tComisión\tTotal Venta\n"
    "2024\tApple Inc.\t20/06/2024\tAcciones\t5\t7,50\t1.050,00\n"
    "2024\tTelefónica\t25/06/2024\tACCIONES\t50\t4,00\t200,00\n"
)

_SALES_7COL_DERECHOS = (
    "Año\tEmpresa\tFecha venta\tTipo\tAcciones\tComisión\tTotal Venta\n"
    "2024\tApple Inc.\t20/06/2024\tDerechos\t5\t7,50\t1.050,00\n"
    "2024\tTelefónica\t25/06/2024\tderechos\t50\t4,00\t200,00\n"
)

_SALES_7COL_MIXED = (
    "Año\tEmpresa\tFecha venta\tTipo\tAcciones\tComisión\tTotal Venta\n"
    "2024\tApple Inc.\t20/06/2024\tAcciones\t5\t7,50\t1.050,00\n"
    "2024\tTelefónica\t25/06/2024\tDerechos\t0\t4,00\t200,00\n"
)

_SALES_7COL_EMPTY_TIPO = (
    "Año\tEmpresa\tFecha venta\tTipo\tAcciones\tComisión\tTotal Venta\n"
    "2024\tApple Inc.\t20/06/2024\t\t5\t7,50\t1.050,00\n"
)

_SALES_7COL_INVALID_TIPO = (
    "Año\tEmpresa\tFecha venta\tTipo\tAcciones\tComisión\tTotal Venta\n"
    "2024\tApple Inc.\t20/06/2024\tOtro\t5\t7,50\t1.050,00\n"
)


class TestSalesParserSalesType:
    """Regression: Tipo column (4th position) in 7-column sales CSV.

    Authoritative header: Año | Empresa | Fecha venta | Tipo | Acciones | Comisión | Total Venta
    All assertions are un-weakened.
    """

    def test_6col_defaults_to_acciones(self):
        """6-column CSV (no Tipo) → every row has sales_type='ACCIONES'."""
        rows = parse_sales(_encode(SALES_CSV))
        for row in rows:
            assert row.get("sales_type") == "ACCIONES"

    def test_7col_header_parses_without_error(self):
        """7-column CSV with Tipo header is accepted and returns correct row count."""
        rows = parse_sales(_encode(_SALES_7COL_ACCIONES))
        assert len(rows) == 2

    def test_7col_acciones_normalized(self):
        """'Acciones' and 'ACCIONES' both normalize to the canonical 'ACCIONES'."""
        rows = parse_sales(_encode(_SALES_7COL_ACCIONES))
        for row in rows:
            assert row["sales_type"] == "ACCIONES"

    def test_7col_derechos_normalized(self):
        """'Derechos' and 'derechos' both normalize to the canonical 'DERECHOS'."""
        rows = parse_sales(_encode(_SALES_7COL_DERECHOS))
        for row in rows:
            assert row["sales_type"] == "DERECHOS"

    def test_7col_sales_type_raw_preserved(self):
        """sales_type_raw carries the original, un-normalized cell value."""
        rows = parse_sales(_encode(_SALES_7COL_DERECHOS))
        assert rows[0]["sales_type_raw"] == "Derechos"
        assert rows[1]["sales_type_raw"] == "derechos"

    def test_7col_mixed_tipos(self):
        """First row ACCIONES, second row DERECHOS — each parsed independently."""
        rows = parse_sales(_encode(_SALES_7COL_MIXED))
        assert rows[0]["sales_type"] == "ACCIONES"
        assert rows[1]["sales_type"] == "DERECHOS"

    def test_7col_empty_tipo_defaults_acciones(self):
        """Empty Tipo cell → defaults to 'ACCIONES' with no INVALID_SALES_TYPE warning."""
        rows = parse_sales(_encode(_SALES_7COL_EMPTY_TIPO))
        assert rows[0]["sales_type"] == "ACCIONES"
        assert not any(w.get("type") == "INVALID_SALES_TYPE" for w in rows[0]["warnings"])

    def test_7col_invalid_tipo_raises_value_error(self):
        """A non-empty Tipo value that cannot be normalized raises ValueError."""
        with pytest.raises(ValueError):
            parse_sales(_encode(_SALES_7COL_INVALID_TIPO))

    def test_7col_whitespace_stripped_from_tipo(self):
        """Leading/trailing whitespace in Tipo cell is stripped before normalization."""
        csv = (
            "Año\tEmpresa\tFecha venta\tTipo\tAcciones\tComisión\tTotal Venta\n"
            "2024\tApple Inc.\t20/06/2024\t  Acciones  \t5\t7,50\t1.050,00\n"
        )
        rows = parse_sales(_encode(csv))
        assert rows[0]["sales_type"] == "ACCIONES"

    def test_7col_accent_insensitive_derechos(self):
        """Accented variant of a valid word normalizes correctly (accent-insensitive)."""
        # NFKD decomposition strips the accent → "DERECHOS"
        csv = (
            "Año\tEmpresa\tFecha venta\tTipo\tAcciones\tComisión\tTotal Venta\n"
            "2024\tApple Inc.\t20/06/2024\tDérechos\t5\t7,50\t1.050,00\n"
        )
        rows = parse_sales(_encode(csv))
        assert rows[0]["sales_type"] == "DERECHOS"

    def test_7col_derechos_with_positive_qty_warns(self):
        """DERECHOS sale with quantity > 0 emits a DERECHOS_WITH_QUANTITY warning."""
        csv = (
            "Año\tEmpresa\tFecha venta\tTipo\tAcciones\tComisión\tTotal Venta\n"
            "2024\tApple Inc.\t20/06/2024\tDerechos\t15\t7,50\t1.050,00\n"
        )
        rows = parse_sales(_encode(csv))
        assert rows[0]["sales_type"] == "DERECHOS"
        warning_types = [w["type"] for w in rows[0]["warnings"]]
        assert "DERECHOS_WITH_QUANTITY" in warning_types

    def test_7col_acciones_zero_qty_warns(self):
        """ACCIONES sale with quantity == 0 emits an ACCIONES_ZERO_QUANTITY warning."""
        csv = (
            "Año\tEmpresa\tFecha venta\tTipo\tAcciones\tComisión\tTotal Venta\n"
            "2024\tApple Inc.\t20/06/2024\tAcciones\t0\t7,50\t1.050,00\n"
        )
        rows = parse_sales(_encode(csv))
        assert rows[0]["sales_type"] == "ACCIONES"
        warning_types = [w["type"] for w in rows[0]["warnings"]]
        assert "ACCIONES_ZERO_QUANTITY" in warning_types

    def test_6col_no_sales_type_warnings(self):
        """6-column CSV with normal rows produces no sales-type-related warnings."""
        rows = parse_sales(_encode(SALES_CSV))
        sales_type_warning_kinds = {
            "DERECHOS_WITH_QUANTITY",
            "ACCIONES_ZERO_QUANTITY",
            "INVALID_SALES_TYPE",
        }
        for row in rows:
            actual = {w["type"] for w in row["warnings"]}
            assert not (actual & sales_type_warning_kinds)


# ===========================================================================
# Amendment G — Bilingual CSV headers and English type aliases
# ===========================================================================


class TestPurchasesParserBilingual:
    """G-9/G-10/G-15: English headers and bilingual purchases parser."""

    _ENGLISH_CSV = (
        "Year\tCompany\tPurchase Date\tPrice per share\tShares\tTotal\tFees\n"
        "2024\tApple Inc.\t15/03/2024\t175,50\t10\t1.755,00\t9,95\n"
    )
    _MIXED_CSV = (
        "Año\tEmpresa\tFecha compra\tPrecio\tShares\tTotal\tComisión\n"
        "2024\tApple Inc.\t15/03/2024\t175,50\t10\t1.755,00\t9,95\n"
    )

    def test_english_headers_parse_ok(self):
        rows = parse_purchases(_encode(self._ENGLISH_CSV))
        assert len(rows) == 1
        assert rows[0]["quantity"] == Decimal("10")
        assert rows[0]["purchase_date"] == "2024-03-15"

    def test_mixed_spanish_english_headers(self):
        """Spanish accents optional; mixed column names parse OK."""
        rows = parse_purchases(_encode(self._MIXED_CSV))
        assert rows[0]["price_per_share"] == Decimal("175.5")

    def test_spanish_headers_still_work(self):
        """Regression: original Spanish-only files unchanged (G-15)."""
        csv = (
            "Año\tEmpresa\tFecha compra\tValor compra\tAcciones\tTotal (€)\tComisión\n"
            "2024\tMicrosoft Corp\t10/01/2024\t320,00\t5\t1.600,00\t7,95\n"
        )
        rows = parse_purchases(_encode(csv))
        assert rows[0]["cost_basis_status"] == "COMPLETE"
        assert rows[0]["year"] == 2024

    def test_unrecognized_header_raises(self):
        csv = (
            "Año\tEmpresa\tFecha compra\tUnknownCol\tAcciones\tTotal (€)\tComisión\n"
            "2024\tApple\t10/01/2024\t100\t5\t500\t5\n"
        )
        with pytest.raises(ValueError, match="unrecognized header"):
            parse_purchases(_encode(csv))

    def test_english_fees_alias(self):
        csv = (
            "Year\tCompany\tBuy Date\tUnit Price\tQuantity\tTrade Value\tFees\n"
            "2024\tApple Inc.\t15/03/2024\t175,50\t10\t1.755,00\t9,95\n"
        )
        rows = parse_purchases(_encode(csv))
        assert rows[0]["commission"] == Decimal("9.95")


class TestSalesParserBilingual:
    """G-9/G-12/G-13/G-14: English headers, English type values, bilingual sales parser."""

    def test_english_6col_headers(self):
        """6-column CSV with English headers (G-9 partial)."""
        csv = (
            "Year\tCompany\tSale Date\tShares\tCommission\tTotal Proceeds\n"
            "2024\tApple Inc.\t20/06/2024\t10\t7,50\t1.820,00\n"
        )
        rows = parse_sales(_encode(csv))
        assert len(rows) == 1
        assert rows[0]["sales_type"] == "ACCIONES"
        assert rows[0]["total_proceeds"] == Decimal("1820.00")

    def test_english_7a_headers_with_type_alias(self):
        """7A variant with English 'Type' header and 'Stocks' value (G-9/G-12)."""
        csv = (
            "Year\tCompany\tSale Date\tType\tShares\tFees\tProceeds\n"
            "2024\tApple Inc.\t20/06/2024\tStocks\t10\t7,50\t1.820,00\n"
        )
        rows = parse_sales(_encode(csv))
        assert rows[0]["sales_type"] == "ACCIONES"

    def test_stocks_alias_maps_to_acciones(self):
        """'Stocks' type value → ACCIONES (G-12)."""
        csv = (
            "Año\tEmpresa\tFecha venta\tTipo\tAcciones\tComisión\tTotal Venta\n"
            "2024\tApple Inc.\t20/06/2024\tStocks\t10\t7,50\t1.820,00\n"
        )
        rows = parse_sales(_encode(csv))
        assert rows[0]["sales_type"] == "ACCIONES"

    def test_shares_alias_maps_to_acciones(self):
        """'Shares' type value → ACCIONES (G-12)."""
        csv = (
            "Año\tEmpresa\tFecha venta\tTipo\tAcciones\tComisión\tTotal Venta\n"
            "2024\tApple Inc.\t20/06/2024\tShares\t10\t7,50\t1.820,00\n"
        )
        rows = parse_sales(_encode(csv))
        assert rows[0]["sales_type"] == "ACCIONES"

    def test_rights_alias_maps_to_derechos(self):
        """'Rights' type value → DERECHOS (G-12)."""
        csv = (
            "Año\tEmpresa\tFecha venta\tTipo\tAcciones\tComisión\tTotal Venta\n"
            "2024\tApple Inc.\t20/06/2024\tRights\t0\t2,00\t500,00\n"
        )
        rows = parse_sales(_encode(csv))
        assert rows[0]["sales_type"] == "DERECHOS"

    def test_invalid_nonempty_type_raises(self):
        """Non-empty unrecognized type value raises ValueError (G-13)."""
        csv = (
            "Año\tEmpresa\tFecha venta\tTipo\tAcciones\tComisión\tTotal Venta\n"
            "2024\tApple Inc.\t20/06/2024\tOPCIONES\t10\t7,50\t1.820,00\n"
        )
        with pytest.raises(ValueError, match="Invalid Tipo"):
            parse_sales(_encode(csv))

    def test_empty_type_defaults_acciones(self):
        """Empty type cell defaults to ACCIONES (G-14, legacy backward compat)."""
        csv = (
            "Año\tEmpresa\tFecha venta\tTipo\tAcciones\tComisión\tTotal Venta\n"
            "2024\tApple Inc.\t20/06/2024\t\t10\t7,50\t1.820,00\n"
        )
        rows = parse_sales(_encode(csv))
        assert rows[0]["sales_type"] == "ACCIONES"

    def test_spanish_6col_regression(self):
        """Spanish-only 6-column files still work (G-15)."""
        csv = (
            "Año\tEmpresa\tFecha venta\tAcciones\tComisión\tTotal Venta\n"
            "2024\tApple Inc.\t20/06/2024\t10\t7,50\t1.820,00\n"
        )
        rows = parse_sales(_encode(csv))
        assert rows[0]["sales_type"] == "ACCIONES"


class TestDividendsParserBilingual:
    """G-11/G-15: English headers for dividends parser."""

    _ENGLISH_CSV = (
        "Year\tCompany\tPayment Date\tGross Amount\tNet Amount\t"
        "Rights Amount\tSource Withholding\tDestination Withholding\n"
        "2024\tUnilever PLC\t28/03/2024\t225,50\t170,38\t0,00\t23,68\t31,44\n"
    )

    def test_english_headers_parse_ok(self):
        """G-11: English headers accepted."""
        rows = parse_dividends(_encode(self._ENGLISH_CSV))
        assert len(rows) == 1
        assert rows[0]["payment_date"] == "2024-03-28"
        assert rows[0]["gross"] == Decimal("225.50")
        assert rows[0]["wht_source"] == Decimal("23.68")

    def test_wht_dest_alias(self):
        csv = (
            "Year\tCompany\tDate\tGross\tNet\tScrip Amount\tWHT Source\tWHT Dest\n"
            "2024\tUnilever PLC\t28/03/2024\t225,50\t170,38\t0,00\t23,68\t31,44\n"
        )
        rows = parse_dividends(_encode(csv))
        assert rows[0]["wht_destination"] == Decimal("31.44")

    def test_spanish_headers_regression(self):
        """G-15: Original Spanish-only files parse identically."""
        csv = (
            "Año\tEmpresa\tFecha de cobro\tImporte Bruto\tImporte Neto\t"
            "Importe en Derechos\tRetención Origen\tRetención Destino\n"
            "2024\tUnilever PLC\t28/03/2024\t225,50\t170,38\t0,00\t23,68\t31,44\n"
        )
        rows = parse_dividends(_encode(csv))
        assert rows[0]["gross"] == Decimal("225.50")

    def test_unrecognized_header_raises(self):
        csv = (
            "Year\tCompany\tPayment Date\tGross Amount\tNet Amount\t"
            "Rights Amount\tSource Withholding\tBadColumn\n"
            "2024\tUnilever PLC\t28/03/2024\t225,50\t170,38\t0,00\t23,68\t31,44\n"
        )
        with pytest.raises(ValueError, match="unrecognized header"):
            parse_dividends(_encode(csv))
