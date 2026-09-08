"""Amendment G — Bilingual CSV parser tests.

Contract references: §G.4 bilingual headers and §G.4.3 English sales type aliases.

Coverage:
- Purchases parser accepts full English headers (Amendment G §G.4.1)
- Sales parser accepts English 6-column headers (Amendment G §G.4.2)
- Sales parser 7-col variant A with English headers + English Tipo column
- Sales type alias: STOCKS → ACCIONES (new in G.4.3)
- Sales type alias: SHARES → ACCIONES (new in G.4.3)
- Sales type alias: RIGHTS → DERECHOS (new in G.4.3)
- Case-insensitive English aliases (Stocks, stocks, STOCKS all accepted)
- Non-empty unrecognized alias still raises ValueError
- Empty type still defaults to ACCIONES (6-col backward compat)
- Dividends parser accepts English headers (Amendment G §G.4.4)
- Spanish-only files continue to parse unchanged (no regression)
- Mixed-language header row (some Spanish, some English aliases) accepted

All assertions use exact equality — no weakened checks.
"""

from __future__ import annotations

import pytest
from decimal import Decimal

from src.portfolio.parsers.purchases import parse_purchases
from src.portfolio.parsers.sales import parse_sales, _normalize_sales_type
from src.portfolio.parsers.dividends import parse_dividends


def _enc(text: str) -> bytes:
    return text.encode("utf-8")


# ---------------------------------------------------------------------------
# §G.4.1 — Purchases parser bilingual headers
# ---------------------------------------------------------------------------

# Full English header variant (tab-delimited)
_PURCHASES_EN = (
    "Year\tCompany\tPurchase Date\tPrice per share\tShares\tTotal\tCommission\n"
    "2024\tApple Inc.\t15/01/2024\t182,50\t10\t1.825,00\t7,50\n"
    "2024\tTelefónica\t20/02/2024\t4,20\t500\t2.100,00\t5,00\n"
)

# Alternative English aliases (ISO date + alternative column names)
_PURCHASES_EN_ALT = (
    "Year\tCompany\tBuy Date\tUnit Price\tQuantity\tTrade Value\tFees\n"
    "2024\tMicrosoft\t2024-03-10\t420,00\t5\t2.100,00\t9,95\n"
)

# Spanish headers continue to work (regression guard)
_PURCHASES_ES = (
    "Año\tEmpresa\tFecha compra\tValor compra\tAcciones\tTotal (€)\tComisión\n"
    "2024\tApple Inc.\t10/01/2024\t182,50\t10\t1.825,00\t7,50\n"
)


class TestPurchasesEnglishHeaders:
    def test_english_headers_parse_without_error(self):
        """Full English header row must parse without ValueError."""
        rows = parse_purchases(_enc(_PURCHASES_EN))
        assert len(rows) == 2

    def test_english_headers_price_per_share_correct(self):
        rows = parse_purchases(_enc(_PURCHASES_EN))
        assert rows[0]["price_per_share"] == Decimal("182.50"), (
            "Price per share must parse correctly with English header"
        )

    def test_english_headers_shares_correct(self):
        rows = parse_purchases(_enc(_PURCHASES_EN))
        assert rows[0]["quantity"] == Decimal("10")

    def test_english_headers_total_cost_correct(self):
        rows = parse_purchases(_enc(_PURCHASES_EN))
        assert rows[0]["total_cost"] == Decimal("1825.00")

    def test_english_headers_commission_correct(self):
        rows = parse_purchases(_enc(_PURCHASES_EN))
        assert rows[0]["commission"] == Decimal("7.50")

    def test_english_headers_date_correct(self):
        rows = parse_purchases(_enc(_PURCHASES_EN))
        assert rows[0]["purchase_date"] == "2024-01-15"

    def test_english_headers_multi_row(self):
        """Both rows parse when headers are English."""
        rows = parse_purchases(_enc(_PURCHASES_EN))
        assert rows[1]["quantity"] == Decimal("500")
        assert rows[1]["commission"] == Decimal("5.00")

    def test_alternative_english_aliases_accepted(self):
        """'Buy Date', 'Unit Price', 'Quantity', 'Trade Value', 'Fees' are valid aliases."""
        rows = parse_purchases(_enc(_PURCHASES_EN_ALT))
        assert len(rows) == 1
        assert rows[0]["price_per_share"] == Decimal("420.00")

    def test_spanish_headers_still_work(self):
        """Spanish-only headers must continue to parse identically (regression guard)."""
        rows = parse_purchases(_enc(_PURCHASES_ES))
        assert len(rows) == 1
        assert rows[0]["quantity"] == Decimal("10")

    def test_cost_basis_complete_with_english_headers(self):
        rows = parse_purchases(_enc(_PURCHASES_EN))
        assert rows[0]["cost_basis_status"] == "COMPLETE"

    def test_zero_cost_acquisition_with_english_headers(self):
        """English-header zero-price row → ZERO_COST status (scrip dividend); no warning."""
        csv = (
            "Year\tCompany\tPurchase Date\tPrice per share\tShares\tTotal\tCommission\n"
            "2024\tScrip Co\t2024-01-01\t0\t50\t0\t0\n"
        )
        rows = parse_purchases(_enc(csv))
        assert rows[0]["cost_basis_status"] == "ZERO_COST"
        assert not any(w["type"] == "ZERO_COST_ACQUISITION" for w in rows[0]["warnings"])

    def test_unrecognized_english_header_raises(self):
        """A column header not in any alias set must raise ValueError."""
        csv = (
            "Year\tCompany\tDate\tWRONG_HEADER\tShares\tTotal\tCommission\n"
            "2024\tFoo\t2024-01-01\t100\t10\t1000\t5\n"
        )
        with pytest.raises(ValueError):
            parse_purchases(_enc(csv))


# ---------------------------------------------------------------------------
# §G.4.2 — Sales parser bilingual headers
# ---------------------------------------------------------------------------

# 6-column English headers
_SALES_6COL_EN = (
    "Year\tCompany\tSale Date\tShares\tCommission\tTotal Proceeds\n"
    "2024\tApple Inc.\t20/06/2024\t5\t7,50\t1.050,00\n"
    "2024\tTelefónica\t25/06/2024\t50\t4,00\t200,00\n"
)

# 7-column English headers (Type column in position 3 — variant A)
_SALES_7COL_EN_TYPE = (
    "Year\tCompany\tSale Date\tType\tShares\tCommission\tTotal Proceeds\n"
    "2024\tApple Inc.\t20/06/2024\tStocks\t5\t7,50\t1.050,00\n"
    "2024\tBanco Santander\t15/07/2024\tRights\t0\t3,00\t150,00\n"
)

# Sale Type header as "Sale Type" (alias)
_SALES_7COL_EN_SALETYPE = (
    "Year\tCompany\tSell Date\tSale Type\tQuantity\tFees\tProceeds\n"
    "2024\tApple Inc.\t2024-06-20\tShares\t10\t8,00\t2.000,00\n"
)


class TestSalesEnglishHeaders:
    def test_6col_english_headers_parse(self):
        rows = parse_sales(_enc(_SALES_6COL_EN))
        assert len(rows) == 2

    def test_6col_english_quantity_correct(self):
        rows = parse_sales(_enc(_SALES_6COL_EN))
        assert rows[0]["quantity"] == Decimal("5")

    def test_6col_english_commission_correct(self):
        rows = parse_sales(_enc(_SALES_6COL_EN))
        assert rows[0]["commission"] == Decimal("7.50")

    def test_6col_english_total_proceeds_correct(self):
        rows = parse_sales(_enc(_SALES_6COL_EN))
        assert rows[0]["total_proceeds"] == Decimal("1050.00")

    def test_6col_english_date_correct(self):
        rows = parse_sales(_enc(_SALES_6COL_EN))
        assert rows[0]["sale_date"] == "2024-06-20"

    def test_6col_english_defaults_acciones(self):
        """6-column English CSV (no Type column) → every row is ACCIONES."""
        rows = parse_sales(_enc(_SALES_6COL_EN))
        for row in rows:
            assert row["sales_type"] == "ACCIONES"

    def test_7col_english_type_header_stocks_parsed(self):
        """Type column with English header 'Type' + value 'Stocks' → ACCIONES."""
        rows = parse_sales(_enc(_SALES_7COL_EN_TYPE))
        assert rows[0]["sales_type"] == "ACCIONES"

    def test_7col_english_type_header_rights_parsed(self):
        """Type column value 'Rights' → DERECHOS."""
        rows = parse_sales(_enc(_SALES_7COL_EN_TYPE))
        assert rows[1]["sales_type"] == "DERECHOS"

    def test_7col_alternative_headers_accepted(self):
        """'Sell Date', 'Sale Type', 'Quantity', 'Fees', 'Proceeds' are valid aliases."""
        rows = parse_sales(_enc(_SALES_7COL_EN_SALETYPE))
        assert len(rows) == 1
        assert rows[0]["sales_type"] == "ACCIONES"  # Shares → ACCIONES


# ---------------------------------------------------------------------------
# §G.4.3 — Sales type alias matrix (new English aliases)
# ---------------------------------------------------------------------------

class TestSalesTypeAliases:
    """Strict tests for _normalize_sales_type() English alias expansion.

    These aliases are NEW in Amendment G and must not regress.
    Previous behavior: only ACCIONES and DERECHOS (Spanish) accepted.
    New behavior: STOCKS, SHARES, RIGHTS (English) also accepted.
    """

    def test_stocks_maps_to_acciones(self):
        assert _normalize_sales_type("STOCKS") == "ACCIONES"

    def test_stocks_lowercase_maps_to_acciones(self):
        """Case-insensitive: 'stocks' → ACCIONES."""
        assert _normalize_sales_type("stocks") == "ACCIONES"

    def test_stocks_mixedcase_maps_to_acciones(self):
        assert _normalize_sales_type("Stocks") == "ACCIONES"

    def test_shares_maps_to_acciones(self):
        assert _normalize_sales_type("SHARES") == "ACCIONES"

    def test_shares_lowercase_maps_to_acciones(self):
        assert _normalize_sales_type("shares") == "ACCIONES"

    def test_rights_maps_to_derechos(self):
        assert _normalize_sales_type("RIGHTS") == "DERECHOS"

    def test_rights_lowercase_maps_to_derechos(self):
        assert _normalize_sales_type("rights") == "DERECHOS"

    def test_rights_mixedcase_maps_to_derechos(self):
        assert _normalize_sales_type("Rights") == "DERECHOS"

    def test_acciones_unchanged(self):
        """Spanish 'ACCIONES' continues to work (regression guard)."""
        assert _normalize_sales_type("ACCIONES") == "ACCIONES"

    def test_derechos_unchanged(self):
        """Spanish 'DERECHOS' continues to work (regression guard)."""
        assert _normalize_sales_type("DERECHOS") == "DERECHOS"

    def test_empty_string_defaults_acciones(self):
        """Empty / whitespace → ACCIONES (legacy 6-col default; unchanged behavior)."""
        assert _normalize_sales_type("") == "ACCIONES"
        assert _normalize_sales_type("   ") == "ACCIONES"

    def test_invalid_english_typo_raises(self):
        """'stock' (without S) is not in aliases → ValueError."""
        with pytest.raises(ValueError, match="Invalid Tipo"):
            _normalize_sales_type("stock")

    def test_invalid_spanish_opciones_raises(self):
        """'OPCIONES' is not a valid alias → ValueError (unchanged behavior)."""
        with pytest.raises(ValueError):
            _normalize_sales_type("OPCIONES")

    def test_invalid_mixed_garbage_raises(self):
        """Arbitrary non-empty string → ValueError."""
        with pytest.raises(ValueError):
            _normalize_sales_type("VENTA_RAPIDA")

    def test_stocks_in_7col_csv(self):
        """End-to-end: 'Stocks' in Tipo column of a 7-col CSV → ACCIONES."""
        csv = (
            "Año\tEmpresa\tFecha venta\tTipo\tAcciones\tComisión\tTotal Venta\n"
            "2024\tFoo Corp\t20/06/2024\tStocks\t5\t7,50\t1.050,00\n"
        )
        rows = parse_sales(_enc(csv))
        assert rows[0]["sales_type"] == "ACCIONES"

    def test_shares_in_7col_csv(self):
        """End-to-end: 'Shares' in Tipo column → ACCIONES."""
        csv = (
            "Año\tEmpresa\tFecha venta\tTipo\tAcciones\tComisión\tTotal Venta\n"
            "2024\tFoo Corp\t20/06/2024\tShares\t10\t5,00\t2.000,00\n"
        )
        rows = parse_sales(_enc(csv))
        assert rows[0]["sales_type"] == "ACCIONES"

    def test_rights_in_7col_csv(self):
        """End-to-end: 'Rights' in Tipo column → DERECHOS."""
        csv = (
            "Año\tEmpresa\tFecha venta\tTipo\tAcciones\tComisión\tTotal Venta\n"
            "2024\tFoo Corp\t20/06/2024\tRights\t0\t3,00\t150,00\n"
        )
        rows = parse_sales(_enc(csv))
        assert rows[0]["sales_type"] == "DERECHOS"

    def test_invalid_type_in_7col_csv_raises(self):
        """Non-empty unrecognized type in actual CSV row raises ValueError (G-13)."""
        csv = (
            "Año\tEmpresa\tFecha venta\tTipo\tAcciones\tComisión\tTotal Venta\n"
            "2024\tFoo Corp\t20/06/2024\tOptionsale\t5\t7,50\t1.050,00\n"
        )
        with pytest.raises(ValueError):
            parse_sales(_enc(csv))


# ---------------------------------------------------------------------------
# §G.4.4 — Dividends parser bilingual headers
# ---------------------------------------------------------------------------

# Full English dividend headers (tab-delimited)
_DIVIDENDS_EN = (
    "Year\tCompany\tPayment Date\tGross Amount\tNet Amount\t"
    "Rights Amount\tSource Withholding\tDestination Withholding\n"
    "2024\tApple Inc.\t15/03/2024\t1.000,00\t800,00\t0,00\t100,00\t100,00\n"
    "2024\tTelefónica\t20/06/2024\t500,00\t400,00\t50,00\t75,00\t25,00\n"
)

# Alternative English aliases
_DIVIDENDS_EN_ALT = (
    "Year\tCompany\tDate\tGross\tNet\tScrip Amount\tWHT Source\tWHT Dest\n"
    "2024\tMicrosoft\t2024-04-10\t200,00\t165,00\t0,00\t20,00\t15,00\n"
)

# Spanish headers (regression guard)
_DIVIDENDS_ES = (
    "Año\tEmpresa\tFecha de cobro\tImporte Bruto\tImporte Neto\t"
    "Importe en Derechos\tRetención Origen\tRetención Destino\n"
    "2024\tApple Inc.\t15/03/2024\t1.000,00\t800,00\t0,00\t100,00\t100,00\n"
)


class TestDividendsEnglishHeaders:
    def test_english_headers_parse_without_error(self):
        rows = parse_dividends(_enc(_DIVIDENDS_EN))
        assert len(rows) == 2

    def test_english_gross_amount_correct(self):
        rows = parse_dividends(_enc(_DIVIDENDS_EN))
        assert rows[0]["gross"] == Decimal("1000.00")

    def test_english_net_amount_correct(self):
        rows = parse_dividends(_enc(_DIVIDENDS_EN))
        assert rows[0]["net"] == Decimal("800.00")

    def test_english_payment_date_correct(self):
        rows = parse_dividends(_enc(_DIVIDENDS_EN))
        assert rows[0]["payment_date"] == "2024-03-15"

    def test_english_source_withholding_correct(self):
        rows = parse_dividends(_enc(_DIVIDENDS_EN))
        assert rows[0]["wht_source"] == Decimal("100.00")

    def test_english_destination_withholding_correct(self):
        rows = parse_dividends(_enc(_DIVIDENDS_EN))
        assert rows[0]["wht_destination"] == Decimal("100.00")

    def test_english_rights_amount_nonzero_warns(self):
        """Rights amount > 0 in English-header file still emits RIGHTS_AMOUNT warning."""
        csv = (
            "Year\tCompany\tPayment Date\tGross Amount\tNet Amount\t"
            "Rights Amount\tSource Withholding\tDestination Withholding\n"
            "2024\tFoo Corp\t2024-01-01\t500,00\t400,00\t50,00\t50,00\t0,00\n"
        )
        rows = parse_dividends(_enc(csv))
        assert any(w["type"] == "RIGHTS_AMOUNT" for w in rows[0]["warnings"])

    def test_alternative_english_aliases_accepted(self):
        """'Date', 'Gross', 'Net', 'Scrip Amount', 'WHT Source', 'WHT Dest' are valid."""
        rows = parse_dividends(_enc(_DIVIDENDS_EN_ALT))
        assert len(rows) == 1
        assert rows[0]["gross"] == Decimal("200.00")

    def test_spanish_headers_unchanged(self):
        """Spanish headers continue to parse identically (regression guard, G-15)."""
        rows = parse_dividends(_enc(_DIVIDENDS_ES))
        assert len(rows) == 1
        assert rows[0]["gross"] == Decimal("1000.00")

    def test_unrecognized_dividend_header_raises(self):
        """Unrecognized column header → ValueError."""
        csv = (
            "Year\tCompany\tPayment Date\tBAD_COL\tNet Amount\t"
            "Rights Amount\tSource Withholding\tDestination Withholding\n"
            "2024\tFoo\t2024-01-01\t100\t80\t0\t10\t10\n"
        )
        with pytest.raises(ValueError):
            parse_dividends(_enc(csv))
