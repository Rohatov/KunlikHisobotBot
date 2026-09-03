"""Central report generation + delivery logic.

`ReportService.generate_and_send_reports` is the single function used by
both the daily APScheduler job and the manual admin button, so the two
trigger paths can never drift out of sync. An asyncio.Lock guards against
overlapping runs (e.g. the scheduler firing while a manual run is still
in flight).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from telegram import Bot
from telegram.error import TelegramError

from app.config import Config, WorksheetConfig
from app.pdf_service import PDFService

logger = logging.getLogger(__name__)


@dataclass
class WorksheetResult:
    sheet_id: int
    label: str
    pdf_generated: bool = False
    sent: bool = False
    error: Optional[str] = None


@dataclass
class ReportResult:
    started_at: datetime
    finished_at: Optional[datetime] = None
    worksheet_results: list[WorksheetResult] = field(default_factory=list)
    overall_success: bool = False
    skipped_due_to_lock: bool = False


@dataclass
class LastRunInfo:
    finished_at: datetime
    success: bool
    triggered_by: str


class ReportService:
    def __init__(self, pdf_service: PDFService, config: Config) -> None:
        self._pdf_service = pdf_service
        self._config = config
        self._lock = asyncio.Lock()
        self.last_run: Optional[LastRunInfo] = None

    async def generate_and_send_reports(self, bot: Bot, triggered_by: str) -> ReportResult:
        """Generate both worksheet PDFs and send them to the channel.

        If a report generation is already in progress, returns immediately
        with `skipped_due_to_lock=True` instead of queuing or blocking.
        """
        if self._lock.locked():
            logger.warning(
                "Report generation already in progress; skipping request (triggered_by=%s)",
                triggered_by,
            )
            return ReportResult(started_at=datetime.now(self._config.timezone), skipped_due_to_lock=True)

        async with self._lock:
            return await self._run(bot, triggered_by)

    async def _run(self, bot: Bot, triggered_by: str) -> ReportResult:
        started_at = datetime.now(self._config.timezone)
        logger.info("Report generation started (triggered_by=%s)", triggered_by)

        result = ReportResult(started_at=started_at)

        for worksheet in self._config.worksheets:
            worksheet_result = await self._process_worksheet(bot, worksheet)
            result.worksheet_results.append(worksheet_result)

        result.finished_at = datetime.now(self._config.timezone)
        result.overall_success = all(
            wr.pdf_generated and wr.sent for wr in result.worksheet_results
        )

        self.last_run = LastRunInfo(
            finished_at=result.finished_at,
            success=result.overall_success,
            triggered_by=triggered_by,
        )

        logger.info(
            "Report generation finished (triggered_by=%s, overall_success=%s)",
            triggered_by,
            result.overall_success,
        )
        return result

    async def _process_worksheet(self, bot: Bot, worksheet: WorksheetConfig) -> WorksheetResult:
        wr = WorksheetResult(sheet_id=worksheet.sheet_id, label=worksheet.label)
        pdf_path: Optional[Path] = None

        try:
            pdf_path = await asyncio.to_thread(
                self._pdf_service.generate_worksheet_pdf,
                worksheet.sheet_id,
                worksheet.slug,
                worksheet.date_cell,
            )
            wr.pdf_generated = True
        except Exception as exc:  # noqa: BLE001 - convert to safe, logged result
            wr.error = "PDF generation failed"
            logger.error("Failed to export worksheet ID %s: %s", worksheet.sheet_id, exc)
            return wr

        try:
            logger.info("Sending PDF to Telegram channel (%s)", worksheet.label)
            with open(pdf_path, "rb") as pdf_file:
                await bot.send_document(
                    chat_id=self._config.telegram_channel_id,
                    document=pdf_file,
                    filename=pdf_path.name,
                )
            wr.sent = True
            logger.info("%s sent successfully", worksheet.label)
        except TelegramError as exc:
            wr.error = "Telegram delivery failed"
            logger.error("Failed to send %s to Telegram: %s", worksheet.label, exc)
        except OSError as exc:
            wr.error = "Could not read generated PDF"
            logger.error("Failed to read PDF for %s: %s", worksheet.label, exc)
        finally:
            await asyncio.to_thread(self._pdf_service.cleanup, pdf_path)

        return wr
