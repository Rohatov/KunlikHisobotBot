"""Daily report scheduling via APScheduler.

Runs inside the same asyncio event loop as the Telegram bot
(AsyncIOScheduler), so it never spins up a competing thread/loop and
never blocks bot update processing — the actual report work happens in
`ReportService`, which off-loads its blocking Google/file-IO calls with
`asyncio.to_thread`.
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from telegram import Bot

from app.config import Config
from app.report_service import ReportService

logger = logging.getLogger(__name__)


class ReportScheduler:
    JOB_ID = "daily_report_job"

    def __init__(self, config: Config, report_service: ReportService, bot: Bot) -> None:
        self._config = config
        self._report_service = report_service
        self._bot = bot
        self.scheduler = AsyncIOScheduler(timezone=config.timezone)

    def start(self) -> None:
        if self.scheduler.get_job(self.JOB_ID) is None:
            self.scheduler.add_job(
                self._run_scheduled_job,
                trigger=CronTrigger(
                    hour=self._config.schedule_hour,
                    minute=self._config.schedule_minute,
                    timezone=self._config.timezone,
                ),
                id=self.JOB_ID,
                replace_existing=True,
                max_instances=1,
                misfire_grace_time=3600,
            )
            logger.info(
                "Scheduled daily report job for %02d:%02d %s",
                self._config.schedule_hour,
                self._config.schedule_minute,
                self._config.timezone_name,
            )
        else:
            logger.info("Scheduler job '%s' already registered; not adding a duplicate", self.JOB_ID)

        if not self.scheduler.running:
            self.scheduler.start()

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            logger.info("Scheduler stopped")

    def next_run_time(self):
        job = self.scheduler.get_job(self.JOB_ID)
        return job.next_run_time if job else None

    async def _run_scheduled_job(self) -> None:
        logger.info("Scheduled report job started")
        result = await self._report_service.generate_and_send_reports(self._bot, triggered_by="scheduler")

        if result.skipped_due_to_lock:
            logger.warning("Scheduled report job skipped: a report generation was already running")
            return

        for wr in result.worksheet_results:
            if wr.sent:
                logger.info("Scheduled job - %s: sent successfully", wr.label)
            elif wr.pdf_generated:
                logger.error("Scheduled job - %s: PDF generated but delivery failed", wr.label)
            else:
                logger.error("Scheduled job - %s: generation failed", wr.label)

        logger.info("Scheduled report job finished (overall_success=%s)", result.overall_success)
