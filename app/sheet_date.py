"""Locating and parsing the report date stored *inside* a worksheet.

The daily reports are prepared for a business date that is written in the
worksheet itself (e.g. a "Sana: 01.09.2026" header cell), and that date is
often not the day the bot sends it — a report for 1 September may be
posted at 12:00 on 3 September. The generated PDF's filename must carry
the worksheet's date, not the send date, so this module turns raw Sheets
cell data into a ``datetime.date``.

Two lookups are supported:

* an explicit A1 cell configured per worksheet (``WORKSHEET_N_DATE_CELL``),
* or, when not configured, a scan of the top-left region of the worksheet
  in reading order (row by row, left to right) for the first cell that is
  either a real date-formatted value or text that looks like a date.

Only pure parsing lives here; the Sheets API call is in
``app.sheets_service``.
"""

from __future__ import annotations

import math
import re
from datetime import date, timedelta
from typing import Optional

# Google Sheets serial day 0 is 30 December 1899.
_SHEETS_EPOCH = date(1899, 12, 30)

_A1_CELL_PATTERN = re.compile(r"^\$?([A-Za-z]{1,3})\$?([0-9]+)$")

# How much of a worksheet is scanned when no explicit date cell is set.
DEFAULT_SCAN_ROWS = 40
DEFAULT_SCAN_COLUMNS = 26

# Month names as they commonly appear in Uzbek (Latin + Cyrillic), Russian
# and English headers. Each alternation is anchored to whole words so that,
# e.g., "Jami 12 ta" or "mart oyi rejasi" cannot be mistaken for a date.
_MONTH_PATTERNS: tuple[tuple[int, str], ...] = (
    (1, r"yanvar|январ[ья]?|jan(?:uary)?"),
    (2, r"fevral|феврал[ья]?|feb(?:ruary)?"),
    (3, r"mart|март[а]?|mar(?:ch)?"),
    (4, r"aprel|апрел[ья]?|apr(?:il)?"),
    (5, r"may|ма[йя]"),
    (6, r"iyun|июн[ья]?|jun(?:e)?"),
    (7, r"iyul|июл[ья]?|jul(?:y)?"),
    (8, r"avgust|август[а]?|aug(?:ust)?"),
    (9, r"sentyabr|sentabr|сентябр[ья]?|sep(?:t(?:ember)?)?"),
    (10, r"oktyabr|oktabr|октябр[ья]?|oct(?:ober)?"),
    (11, r"noyabr|ноябр[ья]?|nov(?:ember)?"),
    (12, r"dekabr|декабр[ья]?|dec(?:ember)?"),
)

_MONTH_ALTERNATION = "|".join(f"(?P<m{num}>{pattern})" for num, pattern in _MONTH_PATTERNS)

# 01.09.2026, 1/9/26, 01-09-2026
_NUMERIC_DMY = re.compile(r"(?<!\d)(\d{1,2})[./-](\d{1,2})[./-](\d{2}|\d{4})(?!\d)")
# 2026-09-01, 2026.09.01, 2026/9/1
_NUMERIC_YMD = re.compile(r"(?<!\d)(\d{4})[./-](\d{1,2})[./-](\d{1,2})(?!\d)")
# "1-sentyabr", "1 sentyabr 2026", "1-сентябрь, 2026 й."
_DAY_MONTH = re.compile(
    r"(?<!\d)(?P<day>\d{1,2})\s*[-–—]?\s*\b(?:" + _MONTH_ALTERNATION + r")\b\.?"
    r"(?:\s*[-,]?\s*(?P<year>\d{4}))?",
    re.IGNORECASE,
)
# "sentyabr 1, 2026", "Sep 1 2026"
_MONTH_DAY = re.compile(
    r"\b(?:" + _MONTH_ALTERNATION + r")\b\.?\s*(?P<day>\d{1,2})(?!\d)"
    r"(?:\s*[-,]?\s*(?P<year>\d{4}))?",
    re.IGNORECASE,
)


def parse_a1_cell(cell: str) -> tuple[int, int]:
    """Convert an A1 reference like ``B3`` into zero-based (row, column).

    Raises ValueError for anything that is not a single-cell reference.
    """
    match = _A1_CELL_PATTERN.match(cell.strip())
    if not match:
        raise ValueError(f"'{cell}' is not a valid single-cell A1 reference (e.g. 'B2').")
    letters, digits = match.group(1).upper(), match.group(2)
    column = 0
    for char in letters:
        column = column * 26 + (ord(char) - ord("A") + 1)
    row = int(digits)
    if row < 1:
        raise ValueError(f"'{cell}' is not a valid single-cell A1 reference (row must be >= 1).")
    return row - 1, column - 1


def serial_to_date(serial: float) -> Optional[date]:
    """Convert a Google Sheets serial day number to a date (time part dropped)."""
    if serial is None or isinstance(serial, bool) or not math.isfinite(serial):
        return None
    days = math.floor(serial)
    # Google Sheets cannot represent dates outside roughly 1900..9999.
    if days < 0 or days > 3_000_000:
        return None
    try:
        return _SHEETS_EPOCH + timedelta(days=days)
    except OverflowError:
        return None


def _safe_date(year: int, month: int, day: int) -> Optional[date]:
    if year < 100:
        year += 2000
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _month_from_match(match: re.Match) -> Optional[int]:
    for num, _ in _MONTH_PATTERNS:
        if match.group(f"m{num}"):
            return num
    return None


def parse_date_text(text: str, default_year: int) -> Optional[date]:
    """Find a date inside free text such as ``"Sana: 01.09.2026"``.

    Numeric forms are read day-first (``dd.mm.yyyy``), matching the
    convention used in the target spreadsheets. ISO ``yyyy-mm-dd`` is also
    accepted. Month names may be Uzbek (Latin or Cyrillic), Russian or
    English; when the year is omitted (``"1-sentyabr"``), ``default_year``
    is used. Returns None if nothing date-like is present.
    """
    if not text:
        return None
    text = text.strip()

    match = _NUMERIC_YMD.search(text)
    if match:
        year, month, day = (int(g) for g in match.groups())
        result = _safe_date(year, month, day)
        if result:
            return result

    match = _NUMERIC_DMY.search(text)
    if match:
        day, month, year = (int(g) for g in match.groups())
        result = _safe_date(year, month, day)
        if result:
            return result

    for pattern in (_DAY_MONTH, _MONTH_DAY):
        match = pattern.search(text)
        if not match:
            continue
        month = _month_from_match(match)
        if month is None:
            continue
        day = int(match.group("day"))
        year = int(match.group("year")) if match.group("year") else default_year
        result = _safe_date(year, month, day)
        if result:
            return result

    return None


def date_from_cell(cell: dict, default_year: int) -> Optional[date]:
    """Interpret a single ``CellData`` dict from the Sheets API as a date.

    A cell counts as a date if it holds a number rendered with a DATE or
    DATE_TIME number format (the normal case for dates typed into Sheets),
    or if its displayed text parses as a date.
    """
    if not cell:
        return None

    effective_value = cell.get("effectiveValue") or {}
    number_format = ((cell.get("effectiveFormat") or {}).get("numberFormat") or {})
    format_type = number_format.get("type", "")

    if "numberValue" in effective_value and format_type in ("DATE", "DATE_TIME"):
        result = serial_to_date(effective_value["numberValue"])
        if result:
            return result

    formatted = cell.get("formattedValue")
    if isinstance(formatted, str):
        result = parse_date_text(formatted, default_year)
        if result:
            return result

    string_value = effective_value.get("stringValue")
    if isinstance(string_value, str) and string_value != formatted:
        return parse_date_text(string_value, default_year)

    return None


def find_date_in_grid(row_data: list[dict], default_year: int) -> Optional[date]:
    """Scan ``GridData.rowData`` in reading order and return the first date."""
    for row in row_data or []:
        for cell in row.get("values") or []:
            result = date_from_cell(cell, default_year)
            if result:
                return result
    return None
