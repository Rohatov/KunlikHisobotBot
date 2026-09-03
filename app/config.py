"""Application configuration.

All runtime configuration is read from environment variables (typically
supplied via a `.env` file loaded with python-dotenv). Nothing here is
hardcoded, and every required value is validated at startup so the
application fails fast with an actionable error message instead of
crashing later during a scheduled run.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv

from app.exceptions import ConfigError
from app.sheet_date import parse_a1_cell

_SPREADSHEET_ID_PATTERN = re.compile(r"/spreadsheets/d/([a-zA-Z0-9-_]+)")

BASE_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class WorksheetConfig:
    """A single worksheet identified by its Google `sheetId` (gid).

    ``date_cell`` is an optional A1 reference (e.g. ``"B2"``) of the cell
    holding the report's date. When None, the top-left region of the
    worksheet is scanned for the first date-like cell instead.
    """

    sheet_id: int
    slug: str
    label: str
    date_cell: Optional[str] = None


@dataclass(frozen=True)
class Config:
    telegram_bot_token: str
    telegram_channel_id: int
    admin_telegram_id: int

    google_sheet_url: str
    spreadsheet_id: str
    google_service_account_file: Path

    worksheet_1: WorksheetConfig
    worksheet_2: WorksheetConfig

    timezone_name: str
    timezone: ZoneInfo
    schedule_hour: int
    schedule_minute: int

    @property
    def worksheets(self) -> tuple[WorksheetConfig, WorksheetConfig]:
        return (self.worksheet_1, self.worksheet_2)


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        raise ConfigError(
            f"Missing required environment variable '{name}'. "
            f"Please set it in your .env file (see .env.example)."
        )
    return value.strip()


def _require_int_env(name: str, description: str) -> int:
    raw = _require_env(name)
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"Environment variable '{name}' must be {description}, got '{raw}'.") from exc


def _extract_spreadsheet_id(url: str) -> str:
    match = _SPREADSHEET_ID_PATTERN.search(url)
    if not match:
        raise ConfigError(
            "GOOGLE_SHEET_URL does not look like a valid Google Sheets URL. "
            "Expected a format like "
            "'https://docs.google.com/spreadsheets/d/<SPREADSHEET_ID>/edit'."
        )
    return match.group(1)


def _validate_service_account_file(raw_path: str) -> Path:
    path = Path(raw_path).expanduser()
    if not path.is_file():
        raise ConfigError(
            f"Google Service Account credential file not found at '{path}'. "
            "Set GOOGLE_SERVICE_ACCOUNT_FILE in your .env to the absolute path "
            "of a valid Service Account JSON key file."
        )
    return path


def _optional_date_cell_env(name: str) -> Optional[str]:
    """Read an optional A1 cell reference; blank means "auto-detect"."""
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return None
    cell = raw.strip().replace("$", "").upper()
    try:
        parse_a1_cell(cell)
    except ValueError as exc:
        raise ConfigError(
            f"Environment variable '{name}' must be a single-cell A1 reference "
            f"such as 'B2' (or left empty to auto-detect the date), got '{raw}'."
        ) from exc
    return cell


def _validate_timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ConfigError(
            f"Invalid TIMEZONE '{name}'. Use a valid IANA timezone name, "
            "e.g. 'Asia/Tashkent'."
        ) from exc


def load_config(env_file: str | None = None) -> Config:
    """Load and validate configuration from the environment / .env file.

    Raises ConfigError with a human-readable message on any problem so the
    application can fail fast at startup.
    """
    load_dotenv(dotenv_path=env_file, override=False)

    telegram_bot_token = _require_env("TELEGRAM_BOT_TOKEN")
    telegram_channel_id = _require_int_env(
        "TELEGRAM_CHANNEL_ID", "an integer channel ID (e.g. -1001234567890)"
    )
    admin_telegram_id = _require_int_env(
        "ADMIN_TELEGRAM_ID", "a numeric Telegram user ID"
    )

    google_sheet_url = _require_env("GOOGLE_SHEET_URL")
    spreadsheet_id = _extract_spreadsheet_id(google_sheet_url)

    service_account_file = _validate_service_account_file(
        _require_env("GOOGLE_SERVICE_ACCOUNT_FILE")
    )

    worksheet_1_id = _require_int_env("WORKSHEET_1_ID", "the integer Google sheetId of worksheet 1")
    worksheet_2_id = _require_int_env("WORKSHEET_2_ID", "the integer Google sheetId of worksheet 2")
    if worksheet_1_id == worksheet_2_id:
        raise ConfigError(
            "WORKSHEET_1_ID and WORKSHEET_2_ID must refer to two different worksheets."
        )
    worksheet_1_date_cell = _optional_date_cell_env("WORKSHEET_1_DATE_CELL")
    worksheet_2_date_cell = _optional_date_cell_env("WORKSHEET_2_DATE_CELL")

    timezone_name = os.getenv("TIMEZONE", "Asia/Tashkent").strip() or "Asia/Tashkent"
    timezone = _validate_timezone(timezone_name)

    schedule_hour = _require_int_env_with_default("SCHEDULE_HOUR", 12)
    schedule_minute = _require_int_env_with_default("SCHEDULE_MINUTE", 0)
    if not (0 <= schedule_hour <= 23):
        raise ConfigError(f"SCHEDULE_HOUR must be between 0 and 23, got {schedule_hour}.")
    if not (0 <= schedule_minute <= 59):
        raise ConfigError(f"SCHEDULE_MINUTE must be between 0 and 59, got {schedule_minute}.")

    return Config(
        telegram_bot_token=telegram_bot_token,
        telegram_channel_id=telegram_channel_id,
        admin_telegram_id=admin_telegram_id,
        google_sheet_url=google_sheet_url,
        spreadsheet_id=spreadsheet_id,
        google_service_account_file=service_account_file,
        worksheet_1=WorksheetConfig(
            sheet_id=worksheet_1_id, slug="savdo", label="Savdo", date_cell=worksheet_1_date_cell
        ),
        worksheet_2=WorksheetConfig(
            sheet_id=worksheet_2_id, slug="qoldiq", label="Qoldiq", date_cell=worksheet_2_date_cell
        ),
        timezone_name=timezone_name,
        timezone=timezone,
        schedule_hour=schedule_hour,
        schedule_minute=schedule_minute,
    )


def _require_int_env_with_default(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw.strip())
    except ValueError as exc:
        raise ConfigError(f"Environment variable '{name}' must be an integer, got '{raw}'.") from exc
