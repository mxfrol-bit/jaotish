"""Telegram-бот Matrix Engine. Живёт в одном процессе с FastAPI.

Бот запоминает данные пользователя (имя + дата) в Supabase, показывает меню с кнопками
и запускает любой разбор одним тапом — без повторного ввода.
"""
from __future__ import annotations

import asyncio
import logging
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

from . import database
from .engine import build_profile
from .models import AnalysisType, ProfileRequest

# --- состояния онбординга/редактирования ---
ASK_NAME, ASK_BIRTHDATE, EDIT_NAME, EDIT_BIRTHDATE = range(4)

# --- кнопки меню ---
BTN_PERSON = "🧬 Личность"
BTN_PERIOD = "🌗 Текущий период"
BTN_WORK = "💼 Работа и деньги"
BTN_DATA = "👤 Мои данные"
BTN_EDIT_NAME = "✏️ Изменить имя"
BTN_EDIT_DATE = "✏️ Изменить дату"
BTN_BACK = "⬅️ В меню"

_ANALYSIS = {
    BTN_PERSON: (AnalysisType.personality, "Разбор личности"),
    BTN_PERIOD: (AnalysisType.current_period, "Текущий период"),
    BTN_WORK: (AnalysisType.work, "Работа и деньги"),
}

MAIN_MENU = ReplyKeyboardMarkup(
    [[BTN_PERSON, BTN_PERIOD], [BTN_WORK, BTN_DATA]], resize_keyboard=True
)
DATA_MENU = ReplyKeyboardMarkup(
    [[BTN_EDIT_NAME, BTN_EDIT_DATE], [BTN_BACK]], resize_keyboard=True
)


# ---------- /start: онбординг или меню ----------
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    user = await asyncio.to_thread(database.get_user, update.effective_user.id)
    if user and user.get("name") and user.get("birth_date"):
        await update.message.reply_text(
            f"С возвращением, {user['name']}.\nВыбери разбор — данные уже сохранены.",
            reply_markup=MAIN_MENU,
        )
        return ConversationHandler.END

    await update.message.reply_text(
        "Matrix Engine — системный профайлинг.\n"
        "Это не гадание: числа и арканы считаются кодом, AI собирает портрет.\n\n"
        "Давай сохраню твои данные один раз. Как тебя зовут? (имя или ФИО)"
    )
    return ASK_NAME


async def onb_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data["name"] = update.message.text.strip()
    await update.message.reply_text("Дата рождения в формате ГГГГ-ММ-ДД (например, 1990-05-15):")
    return ASK_BIRTHDATE


async def onb_birthdate(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        bd = date.fromisoformat(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("Нужен формат ГГГГ-ММ-ДД, например 1988-11-03.")
        return ASK_BIRTHDATE
    await asyncio.to_thread(
        database.upsert_user,
        update.effective_user.id,
        {"name": ctx.user_data["name"], "birth_date": bd.isoformat()},
    )
    await update.message.reply_text(
        f"Готово, {ctx.user_data['name']}. Данные сохранены — больше вводить не нужно.\n"
        "Выбери разбор:",
        reply_markup=MAIN_MENU,
    )
    return ConversationHandler.END


# ---------- разбор одним тапом ----------
async def run_analysis(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    label = update.message.text
    atype, request = _ANALYSIS[label]

    user = await asyncio.to_thread(database.get_user, update.effective_user.id)
    if not user or not user.get("birth_date"):
        await update.message.reply_text("Сначала сохраним данные — нажми /start.")
        return

    await update.message.reply_text(f"{label}: считаю и собираю портрет…")

    req = ProfileRequest(
        name=user.get("name", ""),
        gender=user.get("gender", "") or "",
        birth_date=date.fromisoformat(user["birth_date"]),
        main_request=request,
        analysis_type=atype,
    )
    try:
        profile = await asyncio.to_thread(build_profile, req)
    except Exception as e:  # noqa: BLE001
        logging.exception("build_profile failed")
        await update.message.reply_text(f"Не получилось собрать разбор: {type(e).__name__}: {e}")
        return

    try:
        await asyncio.to_thread(
            database.save_profile, profile.model_dump(mode="json"), update.effective_user.id
        )
    except Exception:  # noqa: BLE001
        logging.exception("save_profile failed")

    await _send_long(update, profile.report["full_report"])
    await update.message.reply_text("Готово. Выбери следующий разбор:", reply_markup=MAIN_MENU)


# ---------- мои данные ----------
async def show_data(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = await asyncio.to_thread(database.get_user, update.effective_user.id)
    if not user:
        await update.message.reply_text("Данных пока нет — нажми /start.")
        return
    await update.message.reply_text(
        f"Твои данные:\n• Имя: {user.get('name') or '—'}\n• Дата рождения: {user.get('birth_date') or '—'}",
        reply_markup=DATA_MENU,
    )


async def edit_name_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Новое имя/ФИО:")
    return EDIT_NAME


async def edit_name_save(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await asyncio.to_thread(
        database.upsert_user, update.effective_user.id, {"name": update.message.text.strip()}
    )
    await update.message.reply_text("Имя обновлено.", reply_markup=MAIN_MENU)
    return ConversationHandler.END


async def edit_date_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Новая дата рождения (ГГГГ-ММ-ДД):")
    return EDIT_BIRTHDATE


async def edit_date_save(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        bd = date.fromisoformat(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("Нужен формат ГГГГ-ММ-ДД.")
        return EDIT_BIRTHDATE
    await asyncio.to_thread(
        database.upsert_user, update.effective_user.id, {"birth_date": bd.isoformat()}
    )
    await update.message.reply_text("Дата обновлена.", reply_markup=MAIN_MENU)
    return ConversationHandler.END


async def back_to_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Меню:", reply_markup=MAIN_MENU)


async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Отменено.", reply_markup=MAIN_MENU)
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
    app = Application.builder().token(_token()).build()

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", cmd_start),
            MessageHandler(filters.Regex(f"^{BTN_EDIT_NAME}$"), edit_name_start),
            MessageHandler(filters.Regex(f"^{BTN_EDIT_DATE}$"), edit_date_start),
        ],
        states={
            ASK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, onb_name)],
            ASK_BIRTHDATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, onb_birthdate)],
            EDIT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_name_save)],
            EDIT_BIRTHDATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_date_save)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    )
    app.add_handler(conv)

    app.add_handler(MessageHandler(filters.Regex(f"^({BTN_PERSON}|{BTN_PERIOD}|{BTN_WORK})$"), run_analysis))
    app.add_handler(MessageHandler(filters.Regex(f"^{BTN_DATA}$"), show_data))
    app.add_handler(MessageHandler(filters.Regex(f"^{BTN_BACK}$"), back_to_menu))
    return app


def _token() -> str:
    from . import config

    return config.TELEGRAM_BOT_TOKEN
