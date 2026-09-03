"""Telegram bot: commands, manual trigger button, and application wiring."""

from __future__ import annotations

import logging
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from app.authorization import admin_only
from app.config import Config
from app.report_service import ReportResult, ReportService, WorksheetResult
from app.scheduler import ReportScheduler

logger = logging.getLogger(__name__)

MANUAL_TRIGGER_CALLBACK = "send_daily_reports"
STATUS_CALLBACK = "show_status"


def build_application(config: Config, report_service: ReportService) -> Application:
    application = (
        Application.builder()
        .token(config.telegram_bot_token)
        .post_init(_build_post_init(config, report_service))
        .post_shutdown(_post_shutdown)
        .build()
    )

    application.bot_data["config"] = config
    application.bot_data["report_service"] = report_service

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("report", report_command))
    application.add_handler(
        CallbackQueryHandler(manual_trigger_callback, pattern=f"^{MANUAL_TRIGGER_CALLBACK}$")
    )
    application.add_handler(CallbackQueryHandler(status_callback, pattern=f"^{STATUS_CALLBACK}$"))
    application.add_error_handler(error_handler)

    return application


def _build_post_init(config: Config, report_service: ReportService):
    async def post_init(application: Application) -> None:
        scheduler = ReportScheduler(config, report_service, application.bot)
        scheduler.start()
        application.bot_data["scheduler"] = scheduler
        logger.info("Application started successfully")

    return post_init


async def _post_shutdown(application: Application) -> None:
    scheduler: Optional[ReportScheduler] = application.bot_data.get("scheduler")
    if scheduler is not None:
        scheduler.shutdown()


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Unhandled exception while processing an update", exc_info=context.error)


@admin_only
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: Config = context.bot_data["config"]
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📄 Hisobotni yuborish", callback_data=MANUAL_TRIGGER_CALLBACK)],
            [InlineKeyboardButton("ℹ️ Status", callback_data=STATUS_CALLBACK)],
        ]
    )
    text = (
        "🤖 *Kunlik Hisobot Boti*\n\n"
        "Ushbu bot Google jadvaldagi ikkita varaqni (Savdo va Qoldiq) har kuni "
        f"soat {config.schedule_hour:02d}:{config.schedule_minute:02d} ({config.timezone_name}) da "
        "PDF ko'rinishida hisobotlar kanaliga avtomatik yuboradi.\n\n"
        "Quyidagi tugmalardan foydalaning."
    )
    await update.message.reply_text(text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)


def _build_status_text(context: ContextTypes.DEFAULT_TYPE) -> str:
    config: Config = context.bot_data["config"]
    report_service: ReportService = context.bot_data["report_service"]
    scheduler: Optional[ReportScheduler] = context.bot_data.get("scheduler")

    next_run = scheduler.next_run_time() if scheduler else None
    last_run = report_service.last_run
    next_run_text = next_run.strftime("%d.%m.%Y %H:%M %Z") if next_run else "noma'lum"

    lines = [
        "✅ Bot ishlamoqda.",
        f"🕐 Jadval: {config.schedule_hour:02d}:{config.schedule_minute:02d} ({config.timezone_name})",
        f"⏭ Keyingi ishga tushish: {next_run_text}",
    ]

    if last_run is not None:
        outcome = "✅ muvaffaqiyatli" if last_run.success else "⚠️ xatoliklar bilan yakunlandi"
        lines.append(
            f"🕘 Oxirgi ishga tushish: {last_run.finished_at.strftime('%d.%m.%Y %H:%M %Z')} "
            f"— {outcome} (kim tomonidan: {last_run.triggered_by})"
        )
        for wr in last_run.worksheet_results:
            lines.append("   " + _describe_worksheet_result(wr))
    else:
        lines.append("🕘 Oxirgi ishga tushish: hali mavjud emas")

    return "\n".join(lines)


def _describe_worksheet_result(wr: WorksheetResult) -> str:
    """One line per worksheet: file name and where its date came from."""
    if not wr.pdf_generated:
        return f"❌ {wr.label}: tayyorlanmadi ({wr.error or 'xatolik'})"
    status = "yuborildi" if wr.sent else "yuborilmadi"
    if wr.date_from_sheet:
        source = f"sana {wr.date_cell} katagidan"
    else:
        source = f"⚠️ bugungi sana, chunki {wr.date_note or 'varaqdagi sana o\'qilmadi'}"
    return f"📄 {wr.label}: {wr.filename} — {status} ({source})"


def _date_fallback_warnings(result: ReportResult) -> list[str]:
    return [
        f"⚠️ {wr.label}: {wr.filename} bugungi sana bilan yuborildi, chunki "
        f"{wr.date_note or 'varaqdagi sana o\'qilmadi'}."
        for wr in result.worksheet_results
        if wr.sent and not wr.date_from_sheet
    ]


@admin_only
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(_build_status_text(context))


@admin_only
async def status_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    await update.effective_message.reply_text(_build_status_text(context))


async def _trigger_manual_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Runs the report flow silently: no "processing"/"success" chat messages.

    Only problems (lock conflict, generation/delivery failure) are reported
    back to the admin — the PDFs landing in the channel are confirmation
    enough on the happy path. Full detail is always in the logs and in
    /status's "last run" line.
    """
    report_service: ReportService = context.bot_data["report_service"]
    result = await report_service.generate_and_send_reports(
        context.bot, triggered_by=f"admin:{update.effective_user.id}"
    )

    if result.skipped_due_to_lock:
        await update.effective_message.reply_text(
            "⚠️ Hisobot allaqachon tayyorlanmoqda. Iltimos, u tugashini kuting."
        )
        return

    if not result.overall_success:
        lines = []
        for wr in result.worksheet_results:
            if wr.sent:
                continue
            if wr.pdf_generated:
                lines.append(f"⚠️ {wr.label}: PDF tayyorlandi, lekin yuborishda xatolik")
            else:
                lines.append(f"❌ {wr.label}: tayyorlashda xatolik")
        await update.effective_message.reply_text(
            "⚠️ Hisobotlarni tayyorlashda muammo yuz berdi.\n\n" + "\n".join(lines)
        )

    # A report whose filename had to fall back to today's date is a problem
    # worth telling the admin about, even though delivery itself succeeded.
    warnings = _date_fallback_warnings(result)
    if warnings:
        await update.effective_message.reply_text(
            "\n".join(warnings)
            + "\n\nVaraqdagi sana katagini tekshiring (Savdo: E4, Qoldiq: B1 yoki .env dagi "
            "WORKSHEET_N_DATE_CELL)."
        )


@admin_only
async def manual_trigger_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    await _trigger_manual_report(update, context)


@admin_only
async def report_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.UPLOAD_DOCUMENT)
    await _trigger_manual_report(update, context)
