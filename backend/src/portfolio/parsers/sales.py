"""Sales CSV parser.

Expected columns (6):
  Año | Empresa | Fecha venta | Acciones | Comisión | Total Venta

Two legacy 7-column variants are recognized only to reject rights rows:

  A) Tipo between Fecha venta and Acciones (user sample layout):
     Año | Empresa | Fecha venta | Tipo | Acciones | Comisión | Total Venta

  B) Tipo appended after Total Venta (design-doc layout):
     Año | Empresa | Fecha venta | Acciones | Comisión | Total Venta | Tipo

Bilingual: Spanish or English headers are accepted.
Spanish locale: DD/MM/YYYY dates, decimal comma numbers.
Delimiter auto-detected: tab, semicolon, comma.
"""

from __future__ import annotations

import unicodedata
from decimal import Decimal
from typing import Any, Dict, List, Set

from ..rights_policy import RIGHTS_UNSUPPORTED_MESSAGE
from .common import (
    normalize_company_name,
    parse_spanish_date,
    parse_spanish_decimal,
    parse_year,
    read_csv_rows,
)

# Aliases that indicate a "Tipo/Type" column (used for variant detection).
_TIPO_ALIASES: Set[str] = {"tipo", "type", "sale type"}

# Bilingual header aliases per expected position.
# Positions are defined relative to the 6-column base layout:
#   0=Year, 1=Company, 2=Date, 3=Shares, 4=Commission, 5=TotalProceeds
# For 7A Tipo sits at position 3; 7B at position 6.
_SALES_BASE_ALIASES: Dict[int, Set[str]] = {
    0: {"ano", "year"},
    1: {"empresa", "company"},
    2: {"fecha venta", "fecha de venta", "sale date", "sell date", "date"},
}
# Shares, commission, total — position differs between variants; validated separately below.
_SHARES_ALIASES: Set[str] = {"acciones", "shares", "quantity"}
_COMMISSION_ALIASES: Set[str] = {"comision", "commission", "fees"}
_TOTAL_ALIASES: Set[str] = {"total venta", "total", "total proceeds", "proceeds"}

_ORDINARY_SALE_TYPES = {"ACCIONES", "STOCKS", "SHARES"}
_REMOVED_RIGHTS_TYPES = {"DERECHOS", "RIGHTS"}


def _normalize_header(h: str) -> str:
    """NFKD → strip combining marks → lowercase → collapse whitespace."""
    nfkd = unicodedata.normalize("NFKD", h)
    stripped = "".join(c for c in nfkd if not unicodedata.combining(c)).lower().strip()
    return " ".join(stripped.split())


def _validate_legacy_type_cell(raw: str) -> None:
    """Accept ordinary-sale labels and explicitly reject removed rights labels."""
    stripped = raw.strip()
    if not stripped:
        return
    nfkd = unicodedata.normalize("NFKD", stripped)
    normalized = "".join(c for c in nfkd if not unicodedata.combining(c)).upper().strip()
    normalized = " ".join(normalized.split())
    if normalized in _REMOVED_RIGHTS_TYPES:
        raise ValueError(RIGHTS_UNSUPPORTED_MESSAGE)
    if normalized not in _ORDINARY_SALE_TYPES:
        raise ValueError(
            f"Invalid Tipo value {raw!r}; only ordinary share sales are supported"
        )


def parse_sales(content: bytes) -> List[Dict[str, Any]]:
    """Parse sales CSV content.

    Returns a list of row dicts, each containing:
      - row_index: int (0-based, excludes header)
      - year: Optional[int]
      - empresa_raw: str
      - empresa_normalized: str
      - sale_date: Optional[str] — ISO YYYY-MM-DD
      - quantity: Decimal  (shares sold)
      - commission: Decimal
      - total_proceeds: Decimal
      - source_row: Dict[str, str]
      - warnings: List[Dict]

    Raises ValueError on parse failure or invalid Tipo value.
    """
    _, rows = read_csv_rows(content)

    if len(rows) < 2:
        raise ValueError("No data rows found (only header or empty)")

    header_row = rows[0]
    normalized_headers = [_normalize_header(h) for h in header_row]

    # Detect format variant by inspecting header positions.
    # 7A: position 3 matches a "tipo" alias (Tipo between Fecha venta and Acciones).
    # 7B: position 6 matches a "tipo" alias (Tipo appended after Total Venta).
    #  6: no Tipo column.
    n = len(normalized_headers)
    if n >= 4 and normalized_headers[3] in _TIPO_ALIASES:
        variant = "7A"
        min_cols = 7
    elif n >= 7 and normalized_headers[6] in _TIPO_ALIASES:
        variant = "7B"
        min_cols = 7
    else:
        variant = "6"
        min_cols = 6

    # Validate the first three base columns (year, company, date) — same for all variants.
    for pos, aliases in _SALES_BASE_ALIASES.items():
        if pos >= len(normalized_headers):
            raise ValueError(
                f"Missing column at position {pos + 1}: expected one of {sorted(aliases)}"
            )
        actual = normalized_headers[pos]
        if actual not in aliases:
            raise ValueError(
                f"Column {pos + 1}: unrecognized header {header_row[pos]!r}. "
                f"Expected one of: {', '.join(sorted(aliases))}"
            )

    # Validate variant-specific data columns.
    if variant == "7A":
        # pos 3 = tipo (already validated via detection), 4=shares, 5=commission, 6=total
        variant_checks = [
            (4, _SHARES_ALIASES),
            (5, _COMMISSION_ALIASES),
            (6, _TOTAL_ALIASES),
        ]
    elif variant == "7B":
        # pos 3=shares, 4=commission, 5=total, 6=tipo (already validated)
        variant_checks = [
            (3, _SHARES_ALIASES),
            (4, _COMMISSION_ALIASES),
            (5, _TOTAL_ALIASES),
        ]
    else:
        # 6-col: pos 3=shares, 4=commission, 5=total
        variant_checks = [
            (3, _SHARES_ALIASES),
            (4, _COMMISSION_ALIASES),
            (5, _TOTAL_ALIASES),
        ]

    for pos, aliases in variant_checks:
        if pos >= len(normalized_headers):
            raise ValueError(
                f"Missing column at position {pos + 1}: expected one of {sorted(aliases)}"
            )
        actual = normalized_headers[pos]
        if actual not in aliases:
            raise ValueError(
                f"Column {pos + 1}: unrecognized header {header_row[pos]!r}. "
                f"Expected one of: {', '.join(sorted(aliases))}"
            )

    results: List[Dict[str, Any]] = []

    for row_index, row in enumerate(rows[1:]):
        while len(row) < min_cols:
            row.append("")

        source_row: Dict[str, str] = {}
        for col_i, cell in enumerate(row):
            header_name = header_row[col_i] if col_i < len(header_row) else f"col_{col_i}"
            source_row[header_name] = cell

        try:
            year = parse_year(row[0])
            empresa_raw = row[1].strip()
            sale_date = parse_spanish_date(row[2])

            if variant == "7A":
                try:
                    _validate_legacy_type_cell(row[3])
                except ValueError as exc:
                    raise ValueError(f"Row {row_index + 2}: {exc}") from exc
                quantity = parse_spanish_decimal(row[4])
                commission = parse_spanish_decimal(row[5]) or Decimal("0")
                total_proceeds = parse_spanish_decimal(row[6]) or Decimal("0")
            elif variant == "7B":
                try:
                    _validate_legacy_type_cell(row[6])
                except ValueError as exc:
                    raise ValueError(f"Row {row_index + 2}: {exc}") from exc
                quantity = parse_spanish_decimal(row[3])
                commission = parse_spanish_decimal(row[4]) or Decimal("0")
                total_proceeds = parse_spanish_decimal(row[5]) or Decimal("0")
            else:
                quantity = parse_spanish_decimal(row[3])
                commission = parse_spanish_decimal(row[4]) or Decimal("0")
                total_proceeds = parse_spanish_decimal(row[5]) or Decimal("0")
            if quantity is None or not quantity.is_finite() or quantity <= 0:
                raise ValueError(
                    "Stock SELL rows require a positive finite share quantity; "
                    "zero, missing, or malformed quantities cannot be imported"
                )
        except (ValueError, IndexError) as exc:
            raise ValueError(f"Row {row_index + 2}: {exc}") from exc

        empresa_normalized = normalize_company_name(empresa_raw)

        results.append({
            "row_index": row_index,
            "year": year,
            "empresa_raw": empresa_raw,
            "empresa_normalized": empresa_normalized,
            "sale_date": sale_date,
            "quantity": quantity,
            "commission": commission,
            "total_proceeds": total_proceeds,
            "source_row": source_row,
            "warnings": [],
        })

    return results
