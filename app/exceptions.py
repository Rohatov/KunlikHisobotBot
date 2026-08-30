"""Custom exceptions used across the application.

Keeping these distinct (instead of raising bare Exception) lets callers
decide what is safe to show to Telegram users/admins versus what only
belongs in the logs.
"""

from __future__ import annotations


class AppError(Exception):
    """Base class for all application-level errors."""


class ConfigError(AppError):
    """Raised when required configuration is missing or invalid."""


class SheetsAccessError(AppError):
    """Raised when the Google Spreadsheet cannot be accessed or read."""


class WorksheetNotFoundError(AppError):
    """Raised when a configured worksheet sheetId does not exist."""


class PDFExportError(AppError):
    """Raised when exporting a worksheet to PDF fails."""


class TelegramDeliveryError(AppError):
    """Raised when sending a document to Telegram fails."""
