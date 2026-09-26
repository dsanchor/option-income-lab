"""Dividends CSV parser.

Expected columns (7):
  Año | Empresa | Fecha de cobro | Importe Bruto | Importe Neto |
  Retención Origen | Retención Destino

The removed legacy 8-column layout is accepted only when its rights amount is
zero. Any non-zero rights value fails explicitly.

Bilingual: Spanish or English headers are both accepted (Amendment G).
Additional columns beyond col 8 are preserved as `extra_cols`.

Spanish locale: DD/MM/YYYY dates, decimal comma numbers.
Delimiter auto-detected: tab, semicolon, comma.
"""

from __future__ import annotations

import unicodedata
from decimal import Decimal
from typing import Any, Dict, List, Optional, Set

from ..rights_policy import RIGHTS_UNSUPPORTED_MESSAGE
from .common import (
    normalize_company_name,
    parse_spanish_date,
    parse_spanish_decimal,
    parse_year,
    read_csv_rows,
)

# Positional alias map for dividend CSV headers (Amendment G §G.4.4).
_DIVIDENDS_BASE_ALIASES: Dict[int, Set[str]] = {
    0: {"ano", "year"},
    1: {"empresa", "company"},
    2: {"fecha de cobro", "fecha cobro", "payment date", "date"},
    3: {"importe bruto", "gross amount", "gross"},
    4: {"importe neto", "net amount", "net"},
}
_RIGHTS_HEADER_ALIASES = {"importe en derechos", "rights amount", "scrip amount"}
_SOURCE_WHT_ALIASES = {"retencion origen", "source withholding", "withholding source", "wht source"}
_DEST_WHT_ALIASES = {"retencion destino", "destination withholding", "withholding destination", "wht destination", "wht dest"}


def _normalize_header(h: str) -> str:
    """NFKD → strip combining marks → lowercase → collapse whitespace."""
    nfkd = unicodedata.normalize("NFKD", h)
    stripped = "".join(c for c in nfkd if not unicodedata.combining(c)).lower().strip()
    return " ".join(stripped.split())


def parse_dividends(content: bytes) -> List[Dict[str, Any]]:
    """Parse dividends CSV content.

    Returns a list of row dicts, each containing:
      - row_index: int (0-based, excludes header)
      - year: Optional[int]
      - empresa_raw: str
      - empresa_normalized: str
      - payment_date: Optional[str] — ISO YYYY-MM-DD
      - gross: Decimal
      - net: Decimal
      - wht_source: Decimal
      - wht_destination: Decimal
      - extra_cols: List[str]
      - source_row: Dict[str, str] — raw cell values by header
      - warnings: List[Dict]

    Raises ValueError on parse failure.
    """
    _, rows = read_csv_rows(content)

    if len(rows) < 2:
        raise ValueError("No data rows found (only header or empty)")

    header_row = rows[0]
    normalized_headers = [_normalize_header(h) for h in header_row]

    for pos, aliases in _DIVIDENDS_BASE_ALIASES.items():
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

    legacy_rights_column = (
        len(normalized_headers) >= 8
        and normalized_headers[5] in _RIGHTS_HEADER_ALIASES
    )
    wht_source_pos = 6 if legacy_rights_column else 5
    wht_dest_pos = 7 if legacy_rights_column else 6
    for pos, aliases in (
        (wht_source_pos, _SOURCE_WHT_ALIASES),
        (wht_dest_pos, _DEST_WHT_ALIASES),
    ):
        if pos >= len(normalized_headers) or normalized_headers[pos] not in aliases:
            raise ValueError(
                f"Column {pos + 1}: unrecognized header "
                f"{header_row[pos] if pos < len(header_row) else ''!r}. "
                f"Expected one of: {', '.join(sorted(aliases))}"
            )

    results: List[Dict[str, Any]] = []

    for row_index, row in enumerate(rows[1:]):
        while len(row) < (8 if legacy_rights_column else 7):
            row.append("")

        source_row: Dict[str, str] = {}
        for col_i, cell in enumerate(row):
            header_name = header_row[col_i] if col_i < len(header_row) else f"col_{col_i}"
            source_row[header_name] = cell

        try:
            year = parse_year(row[0])
            empresa_raw = row[1].strip()
            payment_date = parse_spanish_date(row[2])
            gross = parse_spanish_decimal(row[3]) or Decimal("0")
            net = parse_spanish_decimal(row[4]) or Decimal("0")
            if legacy_rights_column:
                rights_amount = parse_spanish_decimal(row[5]) or Decimal("0")
                if not rights_amount.is_finite() or rights_amount != Decimal("0"):
                    raise ValueError(RIGHTS_UNSUPPORTED_MESSAGE)
            wht_source = parse_spanish_decimal(row[wht_source_pos]) or Decimal("0")
            wht_destination = parse_spanish_decimal(row[wht_dest_pos]) or Decimal("0")
        except (ValueError, IndexError) as exc:
            raise ValueError(f"Row {row_index + 2}: {exc}") from exc

        base_columns = 8 if legacy_rights_column else 7
        extra_cols = row[base_columns:] if len(row) > base_columns else []
        empresa_normalized = normalize_company_name(empresa_raw)

        results.append({
            "row_index": row_index,
            "year": year,
            "empresa_raw": empresa_raw,
            "empresa_normalized": empresa_normalized,
            "payment_date": payment_date,
            "gross": gross,
            "net": net,
            "wht_source": wht_source,
            "wht_destination": wht_destination,
            "extra_cols": extra_cols,
            "source_row": source_row,
            "warnings": [],
        })

    return results
