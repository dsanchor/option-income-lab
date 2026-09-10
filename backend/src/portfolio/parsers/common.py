"""Shared parsing utilities for the three domain CSV schemas.

Spanish locale: decimal comma (1.234,56 → 1234.56), date DD/MM/YYYY.
Delimiter auto-detection: tab, semicolon, comma (in that preference order).
All financial amounts returned as Decimal for arithmetic precision.
"""

from __future__ import annotations

import csv
import datetime as _dt
import io
import re
import unicodedata
from decimal import Decimal, InvalidOperation
from typing import List, Optional


# ---------------------------------------------------------------------------
# Delimiter auto-detection
# ---------------------------------------------------------------------------

def _detect_delimiter(text: str) -> str:
    """Detect the most likely delimiter from the first two lines.

    Preference: tab > semicolon > comma.
    Uses a heuristic: whichever candidate produces consistent non-zero
    split counts across the first data lines wins.
    """
    head = "\n".join(text.splitlines()[:3])
    for delim in ("\t", ";", ","):
        counts = [len(line.split(delim)) for line in head.splitlines() if line.strip()]
        if counts and max(counts) > 1 and len(set(counts)) <= 2:
            return delim
    return ","


# ---------------------------------------------------------------------------
# Number / date parsing
# ---------------------------------------------------------------------------

def parse_spanish_decimal(raw: str) -> Optional[Decimal]:
    """Parse a Spanish-locale decimal string (comma as decimal separator).

    Handles:
      "1.234,56"  → Decimal("1234.56")
      "1234,56"   → Decimal("1234.56")
      "1234.56"   → Decimal("1234.56")
      "0"         → Decimal("0")
      ""          → None
      "N/A"       → None

    Raises ParseError (ValueError subclass) for non-empty invalid values.
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s or s.upper() in {"N/A", "NA", "NONE", "-", "—"}:
        return None
    if "," not in s and "." in s:
        signless = s[1:] if s[:1] in {"+", "-"} else s
        parts = signless.split(".")
        if len(parts) == 2 and all(part.isdigit() for part in parts):
            left, right = parts
            if 1 <= len(right) <= 2:
                try:
                    return Decimal(s)
                except InvalidOperation:
                    raise ValueError(f"Cannot parse decimal: {raw!r}")
    # Spanish convention: dots are ALWAYS thousands separators (even without a comma).
    # A dot-only string such as "1.234" means 1234 (not 1.234).
    s = s.replace(".", "")
    # Comma (if present) is the decimal separator.
    s = s.replace(",", ".")
    try:
        return Decimal(s)
    except InvalidOperation:
        raise ValueError(f"Cannot parse decimal: {raw!r}")


_ISO_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


def parse_spanish_date(raw: str) -> Optional[str]:
    """Parse a date string → ISO 'YYYY-MM-DD'.

    Accepts:
    - Spanish DD/MM/YYYY  → normalised to YYYY-MM-DD
    - Already-normalised ISO YYYY-MM-DD  → validated and returned as-is

    US-style MM/DD/YYYY with a month value > 12 will be rejected by the
    calendar validation below.  Ambiguous dates (e.g. 07/12/2016) are
    treated as Spanish (DD first) — the source schema is always Spanish.

    Returns None for empty / missing values.
    Raises ValueError for non-empty unrecognised or calendar-invalid values.
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None

    # --- Branch 1: already-normalised ISO YYYY-MM-DD (pass-through) ---
    iso_m = _ISO_DATE_RE.match(s)
    if iso_m:
        year, month, day = (int(p) for p in iso_m.groups())
        try:
            _dt.date(year, month, day)  # validate calendar ranges
        except ValueError:
            raise ValueError(f"Cannot parse date: {raw!r}")
        return s

    # --- Branch 2: Spanish DD/MM/YYYY ---
    parts = s.split("/")
    if len(parts) != 3:
        raise ValueError(f"Cannot parse date: {raw!r}")
    day_s, month_s, year_s = parts
    try:
        d, m, y = int(day_s), int(month_s), int(year_s)
        _dt.date(y, m, d)  # validate calendar ranges
        return f"{y:04d}-{m:02d}-{d:02d}"
    except ValueError:
        raise ValueError(f"Cannot parse date: {raw!r}")


def parse_year(raw: str) -> Optional[int]:
    """Parse a year integer (e.g. '2024')."""
    s = str(raw).strip() if raw is not None else ""
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        raise ValueError(f"Cannot parse year: {raw!r}")


# ---------------------------------------------------------------------------
# Company name normalisation
# ---------------------------------------------------------------------------

def normalize_company_name(name: str) -> str:
    """Normalise a free-text company name for matching purposes.

    1. NFKD decompose → drop combining marks (accents).
    2. Lower-case.
    3. Collapse internal whitespace.
    """
    if not name:
        return ""
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_only = "".join(c for c in nfkd if not unicodedata.combining(c))
    lowered = ascii_only.lower().strip()
    return re.sub(r"\s+", " ", lowered)


# ---------------------------------------------------------------------------
# Row normalisation hash (idempotency key)
# ---------------------------------------------------------------------------

import hashlib


def row_idempotency_hash(
    security_id: str,
    txn_type: str,
    trade_date: str,
    quantity: Decimal,
    gross_amount: Decimal,
) -> str:
    """Deterministic hash for same-session idempotency (contract §Deduplication Policy).

    Key: security_id | txn_type | trade_date | quantity | gross_amount
    """
    raw = "|".join([
        security_id or "",
        txn_type or "",
        trade_date or "",
        str(quantity.normalize()) if quantity is not None else "",
        str(gross_amount.normalize()) if gross_amount is not None else "",
    ])
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


# ---------------------------------------------------------------------------
# CSV reader helper
# ---------------------------------------------------------------------------

def read_csv_rows(content: bytes) -> tuple[str, List[List[str]]]:
    """Decode bytes, detect delimiter, return (delimiter, list-of-rows).

    Each row is a list of raw string cell values.
    Strips BOM if present. Returns only non-empty rows.
    Raises ValueError if content is empty or unreadable.
    """
    try:
        text = content.decode("utf-8-sig")  # strips BOM
    except UnicodeDecodeError:
        text = content.decode("latin-1")

    text = text.strip()
    if not text:
        raise ValueError("Empty file")

    delimiter = _detect_delimiter(text)
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    rows = [row for row in reader if any(cell.strip() for cell in row)]
    if not rows:
        raise ValueError("No data rows found")
    return delimiter, rows
