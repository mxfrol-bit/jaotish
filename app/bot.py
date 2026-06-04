"""Telegram-бот Matrix Engine. Живёт в одном процессе с FastAPI.

Бот запоминает данные пользователя (имя, дата, опц. время+место) в Supabase,
показывает меню с кнопками и запускает любой разбор одним тапом — без повторного ввода.

Время и место рождения опциональны: без них работают числа и арканы, с ними
разблокируются астрология, джйотиш и Ба Цзы. Если разбор запущен без этих данных,
бот сам предлагает их дать. Во время расчёта показываются живые статусы («спутники»).
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
from .calc import ephemeris
from .engine import build_profile
from .models import AnalysisType, ProfileRequest

# --- состояния онбординга/редактирования ---
(
    ASK_NAME,
    ASK_BIRTHDATE,
    ASK_TIME,
    ASK_PLACE,
    EDIT_NAME,
    EDIT_BIRTHDATE,
    EDIT_TIME,
    EDIT_PLACE,
) = range(8)

# --- кнопки меню ---
BTN_PERSON = "🧬 Личность"
BTN_PERIOD = "🌗 Текущий период"
BTN_WORK = "💼 Работа и деньги"
BTN_DATA = "👤 Мои данные"
BTN_EDIT_NAME = "✏️ Изменить имя"
BTN_EDIT_DATE = "✏️ Изменить дату"
BTN_EDIT_TIME = "🕐 Время рождения"
BTN_EDIT_PLACE = "📍 Место рождения"
BTN_BACK = "⬅️ В меню"
BTN_SKIP = "⏭️ Пропустить"

_ANALYSIS = {
    BTN_PERSON: (AnalysisType.personality, "Разбор личности"),
    BTN_PERIOD: (AnalysisType.current_period, "Текущий период"),
    BTN_WORK: (AnalysisType.work, "Работа и деньги"),
}

MAIN_MENU = ReplyKeyboardMarkup(
    [[BTN_PERSON, BTN_PERIOD], [BTN_WORK, BTN_DATA]], resize_keyboard=True
)
DATA_MENU = ReplyKeyboardMarkup(
    [[BTN_EDIT_NAME, BTN_EDIT_DATE], [BTN_EDIT_TIME, BTN_EDIT_PLACE], [BTN_BACK]],
    resize_keyboard=True,
)
SKIP_MENU = ReplyKeyboardMarkup([[BTN_SKIP]], resize_keyboard=True)

_ASTRO_PITCH = (
    "Хочешь разблокировать астрологию, джйотиш и Ба Цзы? Для них нужны "
    "точное время и город рождения.\n\n"
    "Пришли время рождения в формате ЧЧ:ММ (например, 14:30) "
    "или нажми «Пропустить» — числа и арканы работают и без этого."
)

# Живые статусы ожидания («вау-эффект»): крутятся, пока идёт расчёт + AI-синтез.
_STAGES_ASTRO = [
    "🛰️ Соединяюсь со спутниками…",
    "🪐 Считываю положение планет и Луны…",
    "🔢 Вычисляю числа и центральный крест арканов…",
    "🕉️ Строю ведическую карту и столпы Ба Цзы…",
    "🧬 Собираю всё в один цельный портрет…",
]
_STAGES_BASIC = [
    "🔢 Вычисляю числа жизненного пути и судьбы…",
    "🃏 Раскладываю центральный крест 22 арканов…",
    "🧬 Собираю цельный портрет…",
]


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
    await update.message.reply_text(_ASTRO_PITCH, reply_markup=SKIP_MENU)
    return ASK_TIME


async def onb_time(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if not _save_time(update.effective_user.id, update.message.text):
        await update.message.reply_text("Нужен формат ЧЧ:ММ, например 09:05. Или «Пропустить».")
        return ASK_TIME
    await update.message.reply_text(
        "Принято. Теперь город рождения (например: Москва) — или «Пропустить».",
        reply_markup=SKIP_MENU,
    )
    return ASK_PLACE


async def onb_place(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("📍 Ищу город на карте…")
    ok, msg = await _save_place(update.effective_user.id, update.message.text)
    if not ok:
        await update.message.reply_text(msg, reply_markup=SKIP_MENU)
        return ASK_PLACE
    await update.message.reply_text(msg)
    return await _finish_or_run(update, ctx)


async def onb_skip(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if not ctx.user_data.get("pending_label"):
        await update.message.reply_text(
            "Готово — данные сохранены. Время/место можно добавить позже в «Мои данные».\n"
            "Выбери разбор:",
            reply_markup=MAIN_MENU,
        )
        return ConversationHandler.END
    await update.message.reply_text("Ок, считаю по числам и арканам.")
    return await _finish_or_run(update, ctx)


async def _finish_or_run(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """После сбора данных: либо запускаем отложенный разбор, либо просто показываем меню."""
    label = ctx.user_data.pop("pending_label", None)
    if not label:
        await update.message.reply_text("Выбери разбор:", reply_markup=MAIN_MENU)
        return ConversationHandler.END
    user = await asyncio.to_thread(database.get_user, update.effective_user.id)
    await _do_analysis(update, label, user)
    return ConversationHandler.END


# ---------- разбор одним тапом ----------
async def analysis_entry(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Тап по разбору. Если нет времени/места — сначала предлагаем их дать."""
    label = update.message.text
    user = await asyncio.to_thread(database.get_user, update.effective_user.id)
    if not user or not user.get("birth_date"):
        await update.message.reply_text("Сначала сохраним данные — нажми /start.")
        return ConversationHandler.END

    has_time = bool(user.get("birth_time"))
    has_place = bool(user.get("timezone") and user.get("lat") is not None)
    if has_time and has_place:
        await _do_analysis(update, label, user)
        return ConversationHandler.END

    # данных не хватает — задаём вопросы, разбор запустим сразу после
    ctx.user_data["pending_label"] = label
    if not has_time:
        await update.message.reply_text(
            "🛰️ Чтобы подключить спутники (астрология, джйотиш, Ба Цзы) и сделать разбор "
            "глубже — пришли время рождения ЧЧ:ММ (например, 14:30).\n\n"
            "Или «Пропустить» — посчитаю по числам и арканам.",
            reply_markup=SKIP_MENU,
        )
        return ASK_TIME
    await update.message.reply_text(
        "🛰️ Остался город рождения (например: Москва) — и спутники подключатся.\n\n"
        "Или «Пропустить».",
        reply_markup=SKIP_MENU,
    )
    return ASK_PLACE


async def _do_analysis(update: Update, label: str, user: dict) -> None:
    """Считает профиль с живыми статусами ожидания и присылает отчёт."""
    atype, request = _ANALYSIS[label]
    has_astro = bool(user.get("birth_time") and user.get("timezone"))
    stages = _STAGES_ASTRO if has_astro else _STAGES_BASIC

    req = ProfileRequest(
        name=user.get("name", ""),
        gender=user.get("gender", "") or "",
        birth_date=date.fromisoformat(user["birth_date"]),
        birth_time=user.get("birth_time"),
        birth_place=user.get("birth_place"),
        lat=user.get("lat"),
        lon=user.get("lon"),
        timezone=user.get("timezone"),
        main_request=request,
        analysis_type=atype,
    )

    status = await update.message.reply_text(f"{label}\n{stages[0]}")
    task = asyncio.create_task(asyncio.to_thread(build_profile, req))
    idx = 0
    while True:
        done, _ = await asyncio.wait({task}, timeout=2.4)
        if task in done:
            break
        idx += 1
        try:
            await status.edit_text(f"{label}\n{stages[idx % len(stages)]}")
        except Exception:  # noqa: BLE001 — «message is not modified» и т.п. не важны
            pass

    try:
        profile = task.result()
    except Exception as e:  # noqa: BLE001
        logging.exception("build_profile failed")
        await status.edit_text(f"Не получилось собрать разбор: {type(e).__name__}: {e}")
        return

    try:
        await asyncio.to_thread(
            database.save_profile, profile.model_dump(mode="json"), update.effective_user.id
        )
    except Exception:  # noqa: BLE001
        logging.exception("save_profile failed")

    try:
        await status.edit_text(f"{label}: готово ✅")
    except Exception:  # noqa: BLE001
        pass
    await _send_long(update, profile.report["full_report"])
    await update.message.reply_text("Выбери следующий разбор:", reply_markup=MAIN_MENU)


# ---------- мои данные ----------
async def show_data(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = await asyncio.to_thread(database.get_user, update.effective_user.id)
    if not user:
        await update.message.reply_text("Данных пока нет — нажми /start.")
        return
    astro = "✅ подключены" if (user.get("birth_time") and user.get("timezone")) else "⛔ нужны время и место"
    await update.message.reply_text(
        "Твои данные:\n"
        f"• Имя: {user.get('name') or '—'}\n"
        f"• Дата рождения: {user.get('birth_date') or '—'}\n"
        f"• Время рождения: {user.get('birth_time') or '—'}\n"
        f"• Место рождения: {user.get('birth_place') or '—'}\n"
        f"• Астро-модули: {astro}",
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


async def edit_time_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Новое время рождения (ЧЧ:ММ):")
    return EDIT_TIME


async def edit_time_save(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if not _save_time(update.effective_user.id, update.message.text):
        await update.message.reply_text("Нужен формат ЧЧ:ММ, например 09:05.")
        return EDIT_TIME
    await update.message.reply_text("Время обновлено.", reply_markup=MAIN_MENU)
    return ConversationHandler.END


async def edit_place_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Новый город рождения (например: Москва):")
    return EDIT_PLACE


async def edit_place_save(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("📍 Ищу город на карте…")
    ok, msg = await _save_place(update.effective_user.id, update.message.text)
    if not ok:
        await update.message.reply_text(msg)
        return EDIT_PLACE
    await update.message.reply_text(msg, reply_markup=MAIN_MENU)
    return ConversationHandler.END


async def back_to_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Меню:", reply_markup=MAIN_MENU)


async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data.pop("pending_label", None)
    await update.message.reply_text("Отменено.", reply_markup=MAIN_MENU)
    return ConversationHandler.END


# ---------- общие сохранялки времени/места ----------
def _save_time(telegram_id: int, raw: str) -> bool:
    """Валидировать ЧЧ:ММ и сохранить. False — формат неверный."""
    txt = raw.strip()
    try:
        hh, mm = (int(x) for x in txt.split(":")[:2])
    except (ValueError, IndexError):
        return False
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        return False
    database.upsert_user(telegram_id, {"birth_time": f"{hh:02d}:{mm:02d}"})
    return True


async def _save_place(telegram_id: int, raw: str) -> tuple[bool, str]:
    """Геокодировать город → координаты+таймзона и сохранить. Возвращает (ok, текст)."""
    place = raw.strip()
    geo = await asyncio.to_thread(ephemeris.resolve_geo, place)
    if geo is None:
        return False, "Не нашёл такой город. Попробуй иначе (например: «Москва, Россия») или «Пропустить»."
    await asyncio.to_thread(
        database.upsert_user,
        telegram_id,
        {
            "birth_place": place,
            "lat": geo["lat"],
            "lon": geo["lon"],
            "timezone": geo["timezone"],
        },
    )
    return True, (
        f"Место сохранено: {geo.get('display_name', place)}\n"
        f"Таймзона: {geo['timezone']}. Спутники подключены 🛰️✅"
    )


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

    skip = MessageHandler(filters.Regex(f"^{BTN_SKIP}$"), onb_skip)
    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", cmd_start),
            MessageHandler(filters.Regex(f"^({BTN_PERSON}|{BTN_PERIOD}|{BTN_WORK})$"), analysis_entry),
            MessageHandler(filters.Regex(f"^{BTN_EDIT_NAME}$"), edit_name_start),
            MessageHandler(filters.Regex(f"^{BTN_EDIT_DATE}$"), edit_date_start),
            MessageHandler(filters.Regex(f"^{BTN_EDIT_TIME}$"), edit_time_start),
            MessageHandler(filters.Regex(f"^{BTN_EDIT_PLACE}$"), edit_place_start),
        ],
        states={
            ASK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, onb_name)],
            ASK_BIRTHDATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, onb_birthdate)],
            ASK_TIME: [skip, MessageHandler(filters.TEXT & ~filters.COMMAND, onb_time)],
            ASK_PLACE: [skip, MessageHandler(filters.TEXT & ~filters.COMMAND, onb_place)],
            EDIT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_name_save)],
            EDIT_BIRTHDATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_date_save)],
            EDIT_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_time_save)],
            EDIT_PLACE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_place_save)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    )
    app.add_handler(conv)

    app.add_handler(MessageHandler(filters.Regex(f"^{BTN_DATA}$"), show_data))
    app.add_handler(MessageHandler(filters.Regex(f"^{BTN_BACK}$"), back_to_menu))
    return app


def _token() -> str:
    from . import config

    return config.TELEGRAM_BOT_TOKEN
