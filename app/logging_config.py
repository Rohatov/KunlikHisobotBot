"""Structured logging setup.

Logs are written both to stdout (so `journalctl -u <service>` picks them up
under systemd) and to a rotating file under `logs/app.log`. Third-party
loggers that are known to log full request URLs (which for Telegram's
Bot API includes the bot token in the path) are pinned to WARNING so
secrets never end up in application logs.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "app.log"

_MAX_BYTES = 5 * 1024 * 1024  # 5 MB per file
_BACKUP_COUNT = 5

# Loggers that may otherwise emit sensitive data (e.g. full request URLs
# containing the Telegram bot token) or excessive noise.
_QUIET_LOGGERS = {
    "httpx": logging.WARNING,
    "httpcore": logging.WARNING,
    "apscheduler": logging.INFO,
    "apscheduler.executors.default": logging.WARNING,
    "googleapiclient.discovery_cache": logging.ERROR,
    "urllib3": logging.WARNING,
}


def setup_logging(level: int = logging.INFO) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(
        fmt="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(
        LOG_FILE, maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers = [file_handler, stream_handler]

    for name, quiet_level in _QUIET_LOGGERS.items():
        logging.getLogger(name).setLevel(quiet_level)
