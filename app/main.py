"""Application entry point.

Run with:  python -m app.main
"""

from __future__ import annotations

import logging
import sys

from telegram import Update

from app.bot import build_application
from app.config import load_config
from app.exceptions import AppError
from app.logging_config import setup_logging
from app.pdf_service import PDFService
from app.report_service import ReportService
from app.sheets_service import SheetsService

logger = logging.getLogger(__name__)


def main() -> None:
    try:
        config = load_config()
    except AppError as exc:
        # Logging isn't configured yet at this point, so this must reach
        # the operator via stderr.
        print(f"Configuration error: {exc}", file=sys.stderr)
        sys.exit(1)

    setup_logging()
    logger.info("Starting Telegram Sheets Bot...")

    try:
        sheets_service = SheetsService(config)
        sheets_service.verify_worksheets_exist(
            [config.worksheet_1.sheet_id, config.worksheet_2.sheet_id]
        )
        logger.info("Google Sheets connection verified")
    except AppError:
        logger.exception("Failed to verify Google Sheets access during startup")
        sys.exit(1)

    pdf_service = PDFService(sheets_service, config)
    report_service = ReportService(pdf_service, config)
    application = build_application(config, report_service)

    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
