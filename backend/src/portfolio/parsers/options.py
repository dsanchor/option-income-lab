"""Option movements CSV parser.

Expected columns:
  Símbolo/Empresa | Tipo | Fecha | Strike | Expiración | Gross USD |
  Gross EUR | Commission EUR | [Net EUR] | [Fecha de cierre / Close date] | Account

Bilingual: Spanish or English headers are both accepted.
Movement types accepted case-insensitively with underscore or space:
  CALL_SELL, CALL_BUY, PUT_SELL, PUT_BUY

Spanish locale numbers are supported, and plain decimal-point numbers are
accepted via parse_spanish_decimal.
Delimiter auto-detected: tab, semicolon, comma.
"""

from __future__ import annotations

import unicodedata
from decimal import Decimal
from typing import Any, Dict, List, Optional, Set

from .common import (
    normalize_company_name,
    parse_spanish_date,
    parse_spanish_decimal,
    read_csv_rows,
)

_TYPE_ALIASES: Set[str] = {"tipo", "type"}
_OPTION_BASE_ALIASES: Dict[int, Set[str]] = {
    0: {"simbolo", "empresa", "ticker", "symbol", "company"},
    1: _TYPE_ALIASES,
    2: {"fecha", "date"},
    3: {"strike"},
    4: {"expiracion", "vencimiento", "expiration"},
    5: {"importe usd bruto", "bruto usd", "gross usd", "usd gross"},
    6: {"importe eur bruto", "bruto eur", "gross eur", "eur gross"},
    7: {"comision eur", "comision", "commission eur", "commission", "fees"},
}
_NET_ALIASES: Set[str] = {"importe eur neto", "neto eur", "net eur", "eur net"}
_CLOSE_DATE_ALIASES: Set[str] = {
    "fecha cierre",
    "fecha de cierre",
    "close date",
    "closing date",
}
_ACCOUNT_ALIASES: Set[str] = {"cuenta", "account"}
_TYPE_VALUE_ALIASES: Dict[str, str] = {
    "CALL_SELL": "CALL_SELL",
    "CALL BUY": "CALL_BUY",
    "CALL_BUY": "CALL_BUY",
    "CALL SELL": "CALL_SELL",
    "PUT_SELL": "PUT_SELL",
    "PUT BUY": "PUT_BUY",
    "PUT_BUY": "PUT_BUY",
    "PUT SELL": "PUT_SELL",
}


def _normalize_header(h: str) -> str:
    nfkd = unicodedata.normalize("NFKD", h)
    stripped = "".join(c for c in nfkd if not unicodedata.combining(c)).lower().strip()
    return " ".join(stripped.split())


def _normalize_option_txn_type(raw: str) -> str:
    stripped = str(raw or "").strip()
    if not stripped:
        raise ValueError(
            "Invalid Tipo value ''; must be one of: CALL_SELL, CALL_BUY, PUT_SELL, PUT_BUY"
        )
    nfkd = unicodedata.normalize("NFKD", stripped)
    normalized = "".join(c for c in nfkd if not unicodedata.combining(c)).upper().strip()
    normalized = " ".join(normalized.replace("-", " ").replace("_", " ").split())
    mapped = _TYPE_VALUE_ALIASES.get(normalized) or _TYPE_VALUE_ALIASES.get(normalized.replace(" ", "_"))
    if mapped is None:
        raise ValueError(
            f"Invalid Tipo value {raw!r}; must be one of: CALL_SELL, CALL_BUY, PUT_SELL, PUT_BUY"
        )
    return mapped


def parse_options(content: bytes) -> List[Dict[str, Any]]:
    """Parse option movements CSV content."""
    _, rows = read_csv_rows(content)

    if len(rows) < 2:
        raise ValueError("No data rows found (only header or empty)")

    header_row = rows[0]
    normalized_headers = [_normalize_header(h) for h in header_row]

    for pos, aliases in _OPTION_BASE_ALIASES.items():
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

    n = len(normalized_headers)
    if n < 9 or normalized_headers[-1] not in _ACCOUNT_ALIASES:
        raise ValueError(
            "Expected final column layout to be either "
            "[... Commission EUR, Account], "
            "[... Commission EUR, Net EUR, Account], "
            "[... Commission EUR, Close Date, Account], or "
            "[... Commission EUR, Net EUR, Close Date, Account]"
        )
    optional_headers = normalized_headers[8:-1]
    if len(optional_headers) > 2:
        raise ValueError("Expected at most two optional columns before Account: Net EUR and/or Close Date")
    if len(set(optional_headers)) != len(optional_headers):
        raise ValueError("Duplicate optional option-import columns are not allowed")
    invalid_optional = [
        header for header in optional_headers
        if header not in _NET_ALIASES and header not in _CLOSE_DATE_ALIASES
    ]
    if invalid_optional:
        raise ValueError(
            "Unrecognized optional column(s) before Account: "
            + ", ".join(repr(header_row[normalized_headers.index(h)]) for h in invalid_optional)
        )
    min_cols = len(normalized_headers)

    results: List[Dict[str, Any]] = []

    for row_index, row in enumerate(rows[1:]):
        while len(row) < min_cols:
            row.append("")

        source_row: Dict[str, str] = {}
        for col_i, cell in enumerate(row):
            header_name = header_row[col_i] if col_i < len(header_row) else f"col_{col_i}"
            source_row[header_name] = cell

        try:
            empresa_raw = row[0].strip()
            txn_type = _normalize_option_txn_type(row[1])
            trade_date = parse_spanish_date(row[2])
            strike = parse_spanish_decimal(row[3])
            option_expiration = parse_spanish_date(row[4])
            gross_usd = parse_spanish_decimal(row[5]) or Decimal("0")
            gross_eur = parse_spanish_decimal(row[6]) or Decimal("0")
            commission_eur = parse_spanish_decimal(row[7]) or Decimal("0")
            if strike is None:
                raise ValueError("strike is required")
            if option_expiration is None:
                raise ValueError("expiration is required")
            if trade_date is None:
                raise ValueError("date is required")
            net_eur: Optional[Decimal] = None
            option_close_date: Optional[str] = None
            for offset, header in enumerate(optional_headers, start=8):
                if header in _NET_ALIASES:
                    net_eur = parse_spanish_decimal(row[offset])
                elif header in _CLOSE_DATE_ALIASES:
                    option_close_date = parse_spanish_date(row[offset])
            account_raw = row[len(normalized_headers) - 1].strip()
        except (ValueError, IndexError) as exc:
            raise ValueError(f"Row {row_index + 2}: {exc}") from exc

        results.append({
            "row_index": row_index,
            "empresa_raw": empresa_raw,
            "empresa_normalized": normalize_company_name(empresa_raw),
            "txn_type": txn_type,
            "trade_date": trade_date,
            "option_strike": float(strike),
            "option_expiration": option_expiration,
            "option_close_date": option_close_date,
            "gross_usd": gross_usd,
            "gross_eur": gross_eur,
            "commission_eur": commission_eur,
            "net_eur": net_eur,
            "account_raw": account_raw,
            "source_row": source_row,
            "warnings": [],
        })

    return results
