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
        """Read the report date written inside a worksheet.

        With ``date_cell`` (A1 notation) only that cell is read; otherwise
        the top-left ``DEFAULT_SCAN_ROWS`` x ``DEFAULT_SCAN_COLUMNS`` block
        is scanned in reading order for the first date-like cell. Returns
        None — never raises — when no date can be determined, so a missing
        or unreadable date degrades to a fallback filename rather than
        blocking the whole report.
        """
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
                        "sheets(properties.sheetId,data(rowData(values("
                        "effectiveValue,formattedValue,effectiveFormat.numberFormat))))"
                    ),
                )
                .execute()
            )
        except (HttpError, RequestException) as exc:
            logger.warning(
                "Could not read the date from worksheet ID %s (%s); falling back to today's date",
                sheet_id,
                exc,
            )
            return None

        default_year = datetime.now(self._config.timezone).year
        for sheet in response.get("sheets", []):
            if sheet.get("properties", {}).get("sheetId") != sheet_id:
                continue
            for grid in sheet.get("data", []):
                found = find_date_in_grid(grid.get("rowData", []), default_year)
                if found:
                    logger.info("Worksheet ID %s report date: %s", sheet_id, found.isoformat())
                    return found

        logger.warning(
            "No date found in worksheet ID %s (%s); falling back to today's date",
            sheet_id,
            f"cell {date_cell}" if date_cell else f"scanned A1:{_column_letter(DEFAULT_SCAN_COLUMNS)}{DEFAULT_SCAN_ROWS}",
        )
        return None

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


def _column_letter(count: int) -> str:
    """1 -> A, 26 -> Z, 27 -> AA (for log messages only)."""
    letters = ""
    while count > 0:
        count, remainder = divmod(count - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters
