"""Admin authorization helpers for Telegram handlers."""

from __future__ import annotations

import logging
from functools import wraps
from typing import Awaitable, Callable

from telegram import Update
from telegram.ext import ContextTypes

from app.config import Config

logger = logging.getLogger(__name__)

UNAUTHORIZED_MESSAGE = "⛔ You are not authorized to operate this bot."

Handler = Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]]


def is_admin(user_id: int, config: Config) -> bool:
    return user_id == config.admin_telegram_id


def admin_only(handler: Handler) -> Handler:
    """Decorator that rejects any user who is not the configured admin.

    Handles both plain messages and callback-query-triggered handlers.
    Unauthorized attempts are logged (user id only, no sensitive payload).
    """

    @wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        config: Config = context.bot_data["config"]
        user = update.effective_user

        if user is None or not is_admin(user.id, config):
            user_id = user.id if user else "unknown"
            logger.warning("Unauthorized access attempt by user_id=%s", user_id)
            if update.callback_query is not None:
                await update.callback_query.answer(UNAUTHORIZED_MESSAGE, show_alert=True)
            elif update.message is not None:
                await update.message.reply_text(UNAUTHORIZED_MESSAGE)
            return

        await handler(update, context)

    return wrapper
