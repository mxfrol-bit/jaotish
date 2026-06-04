"""Telegram-бот Matrix Engine. Живёт в одном процессе с FastAPI."""
from __future__ import annotations

import asyncio
from datetime import date

from telegram import ReplyKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from . import config, database
from .engine import build_profile
from .models import AnalysisType, ProfileRequest

NAME, BIRTHDATE, REQUEST = range(3)

_TYPE_KB = ReplyKeyboardMarkup(
    [["Личность", "Текущий период"], ["Работа и деньги"]],
    one_time_keyboard=True,
    resize_keyboard=True,
)
_TYPE_MAP = {
    "личность": AnalysisType.personality,
    "текущий период": AnalysisType.current_period,
    "работа и деньги": AnalysisType.work,
}


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data.clear()
    await update.message.reply_text(
        "Matrix Engine — системный профайлинг.\n"
        "Это не гадание: числа и арканы считаются кодом, AI только собирает портрет.\n\n"
        "Как тебя зовут? (имя или ФИО — для чисел имени)"
    )
    return NAME


async def got_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data["name"] = update.message.text.strip()
    await update.message.reply_text("Дата рождения в формате ГГГГ-ММ-ДД (например, 1990-05-15):")
    return BIRTHDATE


async def got_birthdate(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        ctx.user_data["birth_date"] = date.fromisoformat(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("Не понял дату. Нужен формат ГГГГ-ММ-ДД, например 1988-11-03.")
        return BIRTHDATE
    await update.message.reply_text("Главный запрос: что хочешь понять?", reply_markup=_TYPE_KB)
    return REQUEST


async def got_request(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    ctx.user_data["main_request"] = text
    ctx.user_data["analysis_type"] = _TYPE_MAP.get(text.lower(), AnalysisType.personality)

    await update.message.reply_text("Считаю и собираю портрет… это займёт до минуты.")

    req = ProfileRequest(
        name=ctx.user_data["name"],
        birth_date=ctx.user_data["birth_date"],
        main_request=text,
        analysis_type=ctx.user_data["analysis_type"],
    )
    profile = await asyncio.to_thread(build_profile, req)
    await asyncio.to_thread(database.save_profile, profile.model_dump(mode="json"), update.effective_user.id)

    await _send_long(update, profile.report["full_report"])
    await update.message.reply_text("Готово. /start — новый разбор.")
    return ConversationHandler.END


async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Отменено. /start — начать заново.")
    return ConversationHandler.END


async def _send_long(update: Update, text: str) -> None:
    """Telegram лимит ~4096 символов — режем по абзацам."""
    limit = 3800
    while text:
        if len(text) <= limit:
            await update.message.reply_text(text)
            break
        cut = text.rfind("\n", 0, limit)
        cut = cut if cut > 0 else limit
        await update.message.reply_text(text[:cut])
        text = text[cut:].lstrip("\n")


def build_application() -> Application:
    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", cmd_start)],
        states={
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_name)],
            BIRTHDATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_birthdate)],
            REQUEST: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_request)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    )
    app.add_handler(conv)
    return app
