"""Purchases CSV parser.

Expected columns (7):
  Año | Empresa | Fecha compra | Valor compra | Acciones | Total (€) | Comisión

Bilingual: Spanish or English headers are both accepted (Amendment G).
Zero-price purchases (Valor compra = 0 with Acciones > 0) are flagged as
ZERO_COST_ACQUISITION — pending corporate-action share acquisitions.

Spanish locale: DD/MM/YYYY dates, decimal comma numbers.
Delimiter auto-detected: tab, semicolon, comma.
"""

from __future__ import annotations

import unicodedata
from decimal import Decimal
from typing import Any, Dict, List, Set

from .common import (
    normalize_company_name,
    parse_spanish_date,
    parse_spanish_decimal,
    parse_year,
    read_csv_rows,
)

# Positional alias map: position → set of acceptable normalized header values.
# Any of the aliases at a given position is accepted (Amendment G §G.4.1).
_PURCHASES_HEADER_ALIASES: Dict[int, Set[str]] = {
    0: {"ano", "year"},
    1: {"empresa", "company"},
    2: {"fecha compra", "fecha de compra", "purchase date", "buy date", "date"},
    3: {"valor compra", "precio", "price per share", "price", "unit price"},
    4: {"acciones", "shares", "quantity"},
    5: {"total (€)", "total (eur)", "total", "total cost", "trade value"},
    6: {"comision", "commission", "fees"},
}


def _normalize_header(h: str) -> str:
    """NFKD → strip combining marks → lowercase → collapse whitespace."""
    nfkd = unicodedata.normalize("NFKD", h)
    stripped = "".join(c for c in nfkd if not unicodedata.combining(c)).lower().strip()
    return " ".join(stripped.split())


def parse_purchases(content: bytes) -> List[Dict[str, Any]]:
    """Parse purchases CSV content.

    Returns a list of row dicts, each containing:
      - row_index: int (0-based, excludes header)
      - year: Optional[int]
      - empresa_raw: str
      - empresa_normalized: str
      - purchase_date: Optional[str] — ISO YYYY-MM-DD
      - price_per_share: Decimal
      - quantity: Decimal
      - total_cost: Decimal
      - commission: Decimal
      - cost_basis_status: "COMPLETE" | "INCOMPLETE"
      - source_row: Dict[str, str]
      - warnings: List[Dict]

    Raises ValueError on parse failure.
    """
    _, rows = read_csv_rows(content)

    if len(rows) < 2:
        raise ValueError("No data rows found (only header or empty)")

    header_row = rows[0]
    normalized_headers = [_normalize_header(h) for h in header_row]

    # Validate each position using the bilingual alias map.
    for pos, aliases in _PURCHASES_HEADER_ALIASES.items():
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
        while len(row) < 7:
            row.append("")

        source_row: Dict[str, str] = {}
        for col_i, cell in enumerate(row):
            header_name = header_row[col_i] if col_i < len(header_row) else f"col_{col_i}"
            source_row[header_name] = cell

        try:
            year = parse_year(row[0])
            empresa_raw = row[1].strip()
            purchase_date = parse_spanish_date(row[2])
            price_per_share = parse_spanish_decimal(row[3]) or Decimal("0")
            quantity = parse_spanish_decimal(row[4]) or Decimal("0")
            total_cost = parse_spanish_decimal(row[5]) or Decimal("0")
            commission = parse_spanish_decimal(row[6]) or Decimal("0")
        except (ValueError, IndexError) as exc:
            raise ValueError(f"Row {row_index + 2}: {exc}") from exc

        empresa_normalized = normalize_company_name(empresa_raw)

        # Zero-cost acquisition detection (e.g. scrip dividend, rights issue).
        # price=0 with quantity>0 is an explicit zero-cost entry — known cost,
        # happens to be zero.  Not a warning; enters pool at cost 0 (ZERO_COST).
        is_zero_cost = (price_per_share == Decimal("0") and quantity > Decimal("0"))
        cost_basis_status = "ZERO_COST" if is_zero_cost else "COMPLETE"

        warnings: List[Dict[str, Any]] = []

        results.append({
            "row_index": row_index,
            "year": year,
            "empresa_raw": empresa_raw,
            "empresa_normalized": empresa_normalized,
            "purchase_date": purchase_date,
            "price_per_share": price_per_share,
            "quantity": quantity,
            "total_cost": total_cost,
            "commission": commission,
            "cost_basis_status": cost_basis_status,
            "source_row": source_row,
            "warnings": warnings,
        })

    return results
