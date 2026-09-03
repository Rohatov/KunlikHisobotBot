"""Google Sheets access and per-worksheet PDF export.

Design notes
------------
Reading spreadsheet metadata (to verify the configured ``sheetId``
values actually exist) uses the official Sheets API v4
(``spreadsheets.get``) via ``google-api-python-client``.

There is, however, no official Google API method that exports a single
worksheet of a spreadsheet to PDF while preserving native Sheets
formatting (fonts, borders, merged cells, colors, print layout, ...).
The Drive API's ``files.export`` only exports the *entire* file and does
not accept a sheet/gid selector.

The reliable, Google-supported mechanism for a single-sheet PDF export is
the same endpoint the Sheets web UI uses for "File > Download > PDF",
authenticated with an OAuth2 bearer token instead of a browser session:

    GET https://docs.google.com/spreadsheets/d/{spreadsheetId}/export
        ?format=pdf&gid={sheetId}&...layout params

Because our Service Account has been granted Viewer access to the
spreadsheet, an ``AuthorizedSession`` built from its credentials can call
this endpoint directly (Authorization: Bearer <token> header), with no
browser, cookies, or interactive login involved. This preserves the
sheet's native rendering (fonts, colors, borders, merges, column widths)
far better than re-building the sheet from raw cell data.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable, Optional

from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account
from googleapiclient.discovery import build as build_service
from googleapiclient.errors import HttpError
from requests.exceptions import RequestException

from app.config import Config
from app.exceptions import PDFExportError, SheetsAccessError, WorksheetNotFoundError
from app.sheet_date import (
    DEFAULT_SCAN_COLUMNS,
    DEFAULT_SCAN_ROWS,
    cell_address,
    column_letter,
    find_date_in_grid,
    parse_a1_cell,
)

logger = logging.getLogger(__name__)

# Read-only scopes only: metadata reads use the Sheets API, and the PDF
# export endpoint lives under docs.google.com but is authorized against
# the same Drive ACL, so drive.readonly is required alongside
# spreadsheets.readonly.
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

_EXPORT_URL_TEMPLATE = "https://docs.google.com/spreadsheets/d/{spreadsheet_id}/export"

# Layout parameters for the export request. These mirror Google Sheets'
# own "Download as PDF" dialog defaults, tuned to keep the sheet's native
# look (gridlines, colors, merges) rather than a stripped-down printout.
_EXPORT_PARAMS = {
    "format": "pdf",
    "portrait": "true",
    "size": "A4",
    "fitw": "true",  # fit to page width
    "gridlines": "true",
    "printtitle": "false",
    "sheetnames": "false",
    "pagenumbers": "false",
    "fzr": "false",  # don't repeat frozen rows on every page
    "top_margin": "0.50",
    "bottom_margin": "0.50",
    "left_margin": "0.50",
    "right_margin": "0.50",
}


@dataclass(frozen=True)
class WorksheetDate:
    """Outcome of looking up the report date inside a worksheet.

    ``value`` is None when no date could be read; ``note`` then explains
    why in a short, admin-readable form (it never contains secrets).
    ``cell`` is the A1 address the date was read from (or was expected in).
    """

    value: Optional[date]
    cell: Optional[str]
    display: str = ""
    note: str = ""


class SheetsService:
    """Wraps Service Account authenticated access to a single spreadsheet."""

    def __init__(self, config: Config) -> None:
        self._config = config
        try:
            self._credentials = service_account.Credentials.from_service_account_file(
                str(config.google_service_account_file), scopes=SCOPES
            )
        except (ValueError, OSError) as exc:
            # Never log exc's str() blindly here beyond the message; it does
            # not contain the private key, only a description of what's wrong
            # with the file (e.g. malformed JSON).
            raise SheetsAccessError(
                "Failed to load the Google Service Account credential file. "
                "Verify GOOGLE_SERVICE_ACCOUNT_FILE points to a valid Service "
                "Account JSON key."
            ) from exc

        self._session = AuthorizedSession(self._credentials)
        self._api = build_service("sheets", "v4", credentials=self._credentials, cache_discovery=False)

    def get_spreadsheet_metadata(self) -> dict:
        """Fetch spreadsheet title and per-sheet properties (id, gid, ...)."""
        try:
            return (
                self._api.spreadsheets()
                .get(
                    spreadsheetId=self._config.spreadsheet_id,
                    fields="properties.title,sheets.properties",
                )
                .execute()
            )
        except HttpError as exc:
            status = exc.resp.status if exc.resp is not None else None
            if status == 404:
                raise SheetsAccessError(
                    "Spreadsheet not found. Check that GOOGLE_SHEET_URL points to "
                    "an existing spreadsheet."
                ) from exc
            if status == 403:
                raise SheetsAccessError(
                    "Access denied to the spreadsheet. Make sure it has been "
                    "shared with the Service Account's email address as a Viewer."
                ) from exc
            raise SheetsAccessError(f"Google Sheets API error (HTTP {status}).") from exc
        except RequestException as exc:
            raise SheetsAccessError(f"Network error while contacting Google Sheets API: {exc}") from exc

    def verify_worksheets_exist(self, sheet_ids: Iterable[int]) -> None:
        """Raise WorksheetNotFoundError if any configured sheetId is missing."""
        metadata = self.get_spreadsheet_metadata()
        existing_ids = {
            sheet["properties"]["sheetId"] for sheet in metadata.get("sheets", [])
        }
        missing = [sid for sid in sheet_ids if sid not in existing_ids]
        if missing:
            raise WorksheetNotFoundError(
                f"Configured worksheet sheetId(s) not found in the spreadsheet: {missing}. "
                "Open the sheet in a browser, select each tab, and check the "
                "'gid=' value in the URL to find the correct sheetId."
            )
        logger.info("Verified %d configured worksheet(s) exist in the spreadsheet", len(list(sheet_ids)))

    def get_worksheet_date(self, sheet_id: int, date_cell: Optional[str] = None) -> Optional[date]:
        """Shortcut for ``lookup_worksheet_date(...).value``."""
        return self.lookup_worksheet_date(sheet_id, date_cell).value

    def lookup_worksheet_date(self, sheet_id: int, date_cell: Optional[str] = None) -> WorksheetDate:
        """Read the report date written inside a worksheet.

        With ``date_cell`` (A1 notation) only that cell is read; otherwise
        the top-left ``DEFAULT_SCAN_ROWS`` x ``DEFAULT_SCAN_COLUMNS`` block
        is scanned in reading order, skipping hidden rows/columns and
        preferring cells that visibly display a full date (see
        ``app.sheet_date.find_date_in_grid``). Never raises: when no date
        can be determined the result carries ``value=None`` and a ``note``
        saying why, so callers can fall back and tell the admin.
        """
        scan_label = f"A1:{column_letter(DEFAULT_SCAN_COLUMNS - 1)}{DEFAULT_SCAN_ROWS}"
        if date_cell:
            row, column = parse_a1_cell(date_cell)
            grid_range = {
                "sheetId": sheet_id,
                "startRowIndex": row,
                "endRowIndex": row + 1,
                "startColumnIndex": column,
                "endColumnIndex": column + 1,
            }
        else:
            grid_range = {
                "sheetId": sheet_id,
                "startRowIndex": 0,
                "endRowIndex": DEFAULT_SCAN_ROWS,
                "startColumnIndex": 0,
                "endColumnIndex": DEFAULT_SCAN_COLUMNS,
            }

        try:
            response = (
                self._api.spreadsheets()
                .getByDataFilter(
                    spreadsheetId=self._config.spreadsheet_id,
                    body={"dataFilters": [{"gridRange": grid_range}], "includeGridData": True},
                    fields=(
                        "sheets(properties.sheetId,data(startRow,startColumn,"
                        "rowMetadata(hiddenByUser,hiddenByFilter),"
                        "columnMetadata(hiddenByUser,hiddenByFilter),"
                        "rowData(values(effectiveValue,formattedValue,effectiveFormat.numberFormat))))"
                    ),
                )
                .execute()
            )
        except HttpError as exc:
            status = exc.resp.status if exc.resp is not None else None
            logger.warning(
                "Could not read the date from worksheet ID %s (HTTP %s: %s); falling back to today's date",
                sheet_id,
                status,
                exc,
            )
            return WorksheetDate(None, date_cell, note=f"Google Sheets API xatosi (HTTP {status})")
        except RequestException as exc:
            logger.warning(
                "Could not read the date from worksheet ID %s (%s); falling back to today's date",
                sheet_id,
                exc,
            )
            return WorksheetDate(None, date_cell, note="Google Sheets bilan bog'lanishda tarmoq xatosi")

        default_year = datetime.now(self._config.timezone).year
        display_of_target = ""
        for sheet in response.get("sheets", []):
            if sheet.get("properties", {}).get("sheetId") != sheet_id:
                continue
            for grid in sheet.get("data", []):
                row_data = grid.get("rowData", [])
                # An explicitly configured cell is honoured even if hidden.
                hidden_rows = () if date_cell else _hidden_indices(grid.get("rowMetadata"))
                hidden_columns = () if date_cell else _hidden_indices(grid.get("columnMetadata"))
                found = find_date_in_grid(row_data, default_year, hidden_rows, hidden_columns)
                if found:
                    # For an explicit cell we asked for exactly that cell, so
                    # its address is known without relying on the grid offset.
                    address = date_cell or cell_address(
                        grid.get("startRow", 0) + found.row, grid.get("startColumn", 0) + found.column
                    )
                    logger.info(
                        "Worksheet ID %s report date: %s (cell %s displays '%s'%s)",
                        sheet_id,
                        found.value.isoformat(),
                        address,
                        found.display,
                        "" if found.visible else "; date value with partial display",
                    )
                    return WorksheetDate(found.value, address, found.display)
                if date_cell:
                    display_of_target = _first_display_value(row_data)

        if date_cell:
            note = (
                f"{date_cell} katagi bo'sh"
                if not display_of_target
                else f"{date_cell} katagidagi '{display_of_target}' sana emas"
            )
        else:
            note = f"{scan_label} oralig'ida sana topilmadi"
        logger.warning(
            "No date found in worksheet ID %s (%s); falling back to today's date",
            sheet_id,
            f"cell {date_cell}, displays '{display_of_target}'" if date_cell else f"scanned {scan_label}",
        )
        return WorksheetDate(None, date_cell, display_of_target, note)

    def export_worksheet_pdf(self, sheet_id: int) -> bytes:
        """Export a single worksheet (by sheetId/gid) to PDF bytes."""
        logger.info("Exporting worksheet ID %s", sheet_id)
        params = dict(_EXPORT_PARAMS, gid=str(sheet_id))
        url = _EXPORT_URL_TEMPLATE.format(spreadsheet_id=self._config.spreadsheet_id)

        try:
            response = self._session.get(url, params=params, timeout=90)
        except RequestException as exc:
            raise PDFExportError(
                f"Network error while exporting worksheet ID {sheet_id}: {exc}"
            ) from exc

        content_type = response.headers.get("Content-Type", "")
        if response.status_code == 403:
            raise PDFExportError(
                f"Export of worksheet ID {sheet_id} was denied (HTTP 403). "
                "Verify the Service Account still has Viewer access to the spreadsheet."
            )
        if response.status_code != 200 or "pdf" not in content_type.lower():
            raise PDFExportError(
                f"Google Sheets export failed for worksheet ID {sheet_id} "
                f"(HTTP {response.status_code}, content-type='{content_type}')."
            )

        logger.info("PDF export succeeded for worksheet ID %s (%d bytes)", sheet_id, len(response.content))
        return response.content


def _hidden_indices(dimension_metadata: Optional[list[dict]]) -> frozenset[int]:
    """Indices (relative to the grid) of rows/columns hidden by user or filter."""
    return frozenset(
        index
        for index, props in enumerate(dimension_metadata or [])
        if props.get("hiddenByUser") or props.get("hiddenByFilter")
    )


def _first_display_value(row_data: list[dict]) -> str:
    """Displayed text of the first cell in the grid (for diagnostics)."""
    for row in row_data or []:
        for cell in row.get("values") or []:
            formatted = cell.get("formattedValue")
            if isinstance(formatted, str):
                return formatted
            string_value = (cell.get("effectiveValue") or {}).get("stringValue")
            if isinstance(string_value, str):
                return string_value
            return ""
    return ""
