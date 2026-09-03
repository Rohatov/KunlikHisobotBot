"""Filesystem-side PDF handling: naming, safe temp files, and cleanup.

This module owns *where* generated PDFs live on disk; app.sheets_service
owns *how* they are produced from Google Sheets. Keeping the split means
report_service can generate + send + clean up without caring about either
concern's internals.
"""

from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from app.config import Config
from app.sheets_service import SheetsService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GeneratedPDF:
    path: Path
    report_date: date
    date_from_sheet: bool  # False when today's date had to be used instead
    date_cell: Optional[str]  # A1 address the date was read from / expected in
    date_note: str  # why the sheet date could not be used (empty when it could)


class PDFService:
    def __init__(self, sheets_service: SheetsService, config: Config) -> None:
        self._sheets_service = sheets_service
        self._config = config
        self._tmp_dir = Path(tempfile.gettempdir()) / "telegram-sheets-bot"

    def generate_worksheet_pdf(
        self, sheet_id: int, slug: str, date_cell: Optional[str] = None
    ) -> GeneratedPDF:
        """Export the given worksheet and write it to a uniquely named PDF.

        The filename carries the report date written *inside* the
        worksheet (``<slug>_<YYYY-MM-DD>.pdf``), read from ``date_cell`` or
        auto-detected — not the day the export runs, since a report is
        often sent a day or two after its business date. Today's date is
        used only when no date can be found in the worksheet, and the
        returned ``GeneratedPDF`` says so (``date_from_sheet=False`` plus a
        ``date_note``) so the caller can warn the admin.

        Writes go through a randomly-named temp file in the same directory
        and an atomic rename, so a concurrent reader never sees a partially
        written PDF.
        """
        self._tmp_dir.mkdir(parents=True, exist_ok=True)

        lookup = self._sheets_service.lookup_worksheet_date(sheet_id, date_cell)
        if lookup.value is not None:
            report_date = lookup.value
        else:
            report_date = datetime.now(self._config.timezone).date()
        date_str = report_date.strftime("%Y-%m-%d")
        destination = self._tmp_dir / f"{slug}_{date_str}.pdf"

        pdf_bytes = self._sheets_service.export_worksheet_pdf(sheet_id)

        fd, raw_tmp_path = tempfile.mkstemp(dir=self._tmp_dir, prefix=".tmp_", suffix=".pdf")
        tmp_path = Path(raw_tmp_path)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(pdf_bytes)
            tmp_path.replace(destination)
        except OSError:
            self.cleanup(tmp_path)
            raise

        logger.info("PDF generated successfully: %s", destination.name)
        return GeneratedPDF(
            path=destination,
            report_date=report_date,
            date_from_sheet=lookup.value is not None,
            date_cell=lookup.cell,
            date_note=lookup.note,
        )

    def cleanup(self, path: Path) -> None:
        """Best-effort removal of a temporary PDF. Never raises."""
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Failed to clean up temporary file %s: %s", path.name, exc)
