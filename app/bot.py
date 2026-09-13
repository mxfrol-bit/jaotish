"""Telegram-бот Matrix Engine. Живёт в одном процессе с FastAPI.

Логика (после правок 2026-09-13):
  - онбординг: имя → пол (мужчина/женщина — от этого зависят формы обращения и подача) →
    дата ДД.ММ.ГГГГ → насколько точно известно время (точно/приблизительно/не знаю) →
    время → город → компактное подтверждение «Всё верно / Изменить» → что важнее сейчас;
  - меню по темам (Обо мне, Отношения, Совместимость, Работа и деньги, Мой период,
    Выбор даты, Мои данные, Как это работает), порядок тем — по интересу пользователя;
  - тема → выбор конкретного вопроса → короткий ответ («Коротко») + «Подробная расшифровка»;
  - вместо «Сделать заново»: «Изменить данные», «Объяснить проще», «Подробнее»;
  - под каждым разбором — строка «Основа разбора: …» (какие системы реально посчитаны).

Время и место рождения опциональны: без них работает нумерология, с ними — астрология,
джйотиш и Ба Цзы. Если человек сказал «не знаю время», бот больше не переспрашивает.
"""
from __future__ import annotations

import asyncio
import calendar
import html
import io
import logging
import re
from datetime import date

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from . import database, imagegen, synthesis, tts, viz
from .calc import ephemeris
from .engine import build_event, build_profile, build_synastry
from .models import AnalysisType, ProfileRequest
from .synthesis import ANALYSIS_TITLES, LOADING_MESSAGES, METHOD_BASIS, date_ru, is_female

# --- состояния онбординга/редактирования/добавления партнёра ---
(
    ASK_NAME,
    ASK_GENDER,
    ASK_BIRTHDATE,
    ASK_TIME_KNOWN,
    ASK_TIME,
    ASK_PLACE,
    ASK_CONFIRM,
    ASK_FOCUS,
    EDIT_NAME,
    EDIT_BIRTHDATE,
    EDIT_PLACE,
    EDIT_GENDER,
    EDIT_FOCUS,
    P_NAME,
    P_DATE,
    P_TIME,
    P_PLACE,
    EV_DATE,
    EV_DESC,
) = range(19)

# --- кнопки главного меню (без пёстрых эмодзи — спокойное оформление) ---
BTN_ME = "Обо мне"
BTN_REL = "Отношения"
BTN_COMPAT = "Совместимость"
BTN_WORK = "Работа и деньги"
BTN_PERIOD = "Мой период"
BTN_EVENT = "Выбор даты"
BTN_DATA = "Мои данные"
BTN_HOW = "Как это работает"
# --- «Мои данные» ---
BTN_EDIT_NAME = "Имя"
BTN_EDIT_DATE = "Дата рождения"
BTN_EDIT_TIME = "Время рождения"
BTN_EDIT_PLACE = "Город рождения"
BTN_EDIT_GENDER = "Пол"
BTN_EDIT_FOCUS = "Что важнее сейчас"
BTN_BACK = "← В меню"
# --- варианты ответов в анкете ---
BTN_SKIP = "Пропустить"
BTN_MALE = "Мужчина"
BTN_FEMALE = "Женщина"
BTN_T_EXACT = "Знаю точно"
BTN_T_APPROX = "Знаю приблизительно"
BTN_T_UNKNOWN = "Не знаю"
BTN_OK = "Всё верно"
BTN_FIX = "Изменить"
BTN_F_SELF = "Понять себя"
BTN_F_REL = "Отношения и семья"
BTN_F_WORK = "Работа и деньги"
BTN_F_PERIOD = "Текущий период"

# Совпадает с кнопкой И с ручным вводом: «Пропустить», пропустить, skip — в любом регистре/кавычках.
_SKIP_RE = re.compile(r"пропуст|skip", re.IGNORECASE)

# Тема меню → тип разбора.
_TOPIC_TYPE = {
    BTN_ME: AnalysisType.personality,
    BTN_REL: AnalysisType.relationships,
    BTN_WORK: AnalysisType.work,
    BTN_PERIOD: AnalysisType.current_period,
}
_TOPIC_BY_TYPE = {v.value: k for k, v in _TOPIC_TYPE.items()}

# Вопросы внутри темы: (id, текст кнопки, формулировка вопроса для AI).
# Один ответ — один вопрос; «Общий разбор» — обзор темы целиком.
QUESTIONS: dict[str, list[tuple[str, str, str]]] = {
    "personality": [
        ("all", "Общий разбор", "Общий разбор характера: сильные стороны, привычные реакции, что мешает."),
        ("strong", "Мои сильные стороны", "Какие мои сильные стороны и как на них опираться?"),
        ("confid", "Что даёт мне уверенность", "Что мне важно для ощущения уверенности и опоры?"),
        ("block", "Что мне мешает", "Что мне обычно мешает и как это проявляется в жизни?"),
    ],
    "relationships": [
        ("all", "Общий разбор", "Общий разбор темы отношений: потребности, сценарии, подходящий партнёр."),
        ("partner", "Какой партнёр мне подходит", "Какой партнёр мне подходит и почему?"),
        ("needs", "Что мне нужно в отношениях", "Что мне нужно в отношениях, чтобы чувствовать себя хорошо?"),
        ("repeat", "Какие сценарии повторяются", "Какие сценарии у меня повторяются в отношениях и как их замечать?"),
        ("talk", "На что смотреть в общении", "На что мне обратить внимание в общении с близкими?"),
    ],
    "work": [
        ("all", "Общий разбор", "Общий разбор темы работы и денег: способности, формат работы, отношение к деньгам."),
        ("skills", "Какие способности развивать", "Какие способности мне стоит развивать?"),
        ("format", "Какой формат работы мне ближе", "Какой формат работы мне ближе: команда или соло, найм или своё дело, ритм?"),
        ("money", "Привычки с деньгами", "Какие мои привычки помогают или мешают в обращении с деньгами?"),
    ],
}

# Периоды для «Мой период»: (id, кнопка) → границы считает _period_range.
PERIODS = [("month", "Ближайший месяц"), ("next", "Следующий месяц"), ("year", "Этот год")]

_FOCUS_BY_BTN = {BTN_F_SELF: "self", BTN_F_REL: "relationships", BTN_F_WORK: "work", BTN_F_PERIOD: "period"}
_FOCUS_TITLE = {v: k for k, v in _FOCUS_BY_BTN.items()}

DATA_MENU = ReplyKeyboardMarkup(
    [
        [BTN_EDIT_NAME, BTN_EDIT_DATE],
        [BTN_EDIT_TIME, BTN_EDIT_PLACE],
        [BTN_EDIT_GENDER, BTN_EDIT_FOCUS],
        [BTN_BACK],
    ],
    resize_keyboard=True,
)
SKIP_MENU = ReplyKeyboardMarkup([[BTN_SKIP]], resize_keyboard=True)
GENDER_MENU = ReplyKeyboardMarkup([[BTN_FEMALE, BTN_MALE]], resize_keyboard=True, one_time_keyboard=True)
TIME_KNOWN_MENU = ReplyKeyboardMarkup(
    [[BTN_T_EXACT, BTN_T_APPROX], [BTN_T_UNKNOWN]], resize_keyboard=True, one_time_keyboard=True
)
CONFIRM_MENU = ReplyKeyboardMarkup([[BTN_OK, BTN_FIX]], resize_keyboard=True, one_time_keyboard=True)
FOCUS_MENU = ReplyKeyboardMarkup(
    [[BTN_F_SELF, BTN_F_REL], [BTN_F_WORK, BTN_F_PERIOD]], resize_keyboard=True, one_time_keyboard=True
)


def main_menu(user: dict | None) -> ReplyKeyboardMarkup:
    """Главное меню. Порядок тем — от интереса пользователя, все темы доступны каждому."""
    focus = (user or {}).get("focus") or ""
    first = {"self": BTN_ME, "relationships": BTN_REL, "work": BTN_WORK, "period": BTN_PERIOD}.get(focus)
    order = [BTN_ME, BTN_REL, BTN_COMPAT, BTN_WORK, BTN_PERIOD, BTN_EVENT]
    if first and first in order:
        order.remove(first)
        order.insert(0, first)
        if first == BTN_REL:  # к отношениям — рядом совместимость
            order.remove(BTN_COMPAT)
            order.insert(1, BTN_COMPAT)
    rows = [order[0:2], order[2:4], order[4:6], [BTN_DATA, BTN_HOW]]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


# Живые статусы ожидания: крутятся, пока идёт расчёт + AI-синтез.
_STAGES_ASTRO = LOADING_MESSAGES
_STAGES_BASIC = [m for m in LOADING_MESSAGES if m[0] not in "🛰📡🪐🌙✴️📐🔭🧭🌌"][:8] or LOADING_MESSAGES[8:14]

WELCOME = (
    "Привет. Здесь можно получить персональный разбор по дате рождения:\n\n"
    "• Обо мне — характер, сильные стороны, что мешает\n"
    "• Отношения — что тебе нужно, какой партнёр подходит, что повторяется\n"
    "• Работа и деньги — способности, формат работы, привычки с деньгами\n"
    "• Мой период — о чём ближайший месяц или год\n"
    "• Совместимость с конкретным человеком и оценка выбранной даты\n\n"
    "Чтобы начать, нужны имя и дата рождения. Время и город рождения — по желанию: "
    "с ними разбор точнее.\n\nКак тебя зовут?"
)

TIME_KNOWN_TEXT = (
    "Знаешь ли ты время своего рождения?\n\n"
    "Точное время делает разбор заметно глубже: от него зависят темы периодов и часть черт "
    "характера. Приблизительное (±1–2 часа) тоже подходит — такие выводы я буду подавать "
    "осторожнее. Если не знаешь — разбор построится по дате: базовые черты и ритм года."
)


def _g(user: dict | None, male: str, female: str) -> str:
    """Форма рода для текстов бота: _g(user, 'готов', 'готова')."""
    return female if is_female((user or {}).get("gender") or "") else male


# ---------- /start: онбординг или меню ----------
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data.pop("pending", None)
    user = await asyncio.to_thread(database.get_user, update.effective_user.id)
    if user and user.get("name") and user.get("birth_date"):
        if not user.get("gender"):
            # старые пользователи без пола: спрашиваем один раз до меню
            ctx.user_data["gender_then_menu"] = True
            await update.message.reply_text(
                f"С возвращением, {user['name']}. Один вопрос, чтобы правильно к тебе обращаться:",
                reply_markup=GENDER_MENU,
            )
            return EDIT_GENDER
        await update.message.reply_text(
            f"С возвращением, {user['name']}. Выбери тему — данные уже сохранены.",
            reply_markup=main_menu(user),
        )
        return ConversationHandler.END

    await update.message.reply_text(WELCOME)
    return ASK_NAME


async def onb_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data["name"] = update.message.text.strip()
    await update.message.reply_text("Ты мужчина или женщина? От этого зависит, как я буду обращаться.",
                                    reply_markup=GENDER_MENU)
    return ASK_GENDER


async def onb_gender(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    gender = _parse_gender(update.message.text)
    if not gender:
        await update.message.reply_text("Выбери кнопкой: Женщина или Мужчина.", reply_markup=GENDER_MENU)
        return ASK_GENDER
    ctx.user_data["gender"] = gender
    user = await asyncio.to_thread(database.get_user, update.effective_user.id)
    if user and user.get("birth_date"):
        # данные уже были, не хватало только пола — дальше без повторной анкеты
        await asyncio.to_thread(database.upsert_user, update.effective_user.id, {"gender": gender})
        return await _finish_or_run(update, ctx)
    await update.message.reply_text("Дата рождения в формате ДД.ММ.ГГГГ (например, 15.05.1990):")
    return ASK_BIRTHDATE


async def onb_birthdate(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    bd = parse_date(update.message.text)
    if not bd:
        await update.message.reply_text("Нужен формат ДД.ММ.ГГГГ, например 03.11.1988.")
        return ASK_BIRTHDATE
    fields = {"name": ctx.user_data.get("name", ""), "birth_date": bd.isoformat()}
    if ctx.user_data.get("gender"):
        fields["gender"] = ctx.user_data["gender"]
    saved = await asyncio.to_thread(database.upsert_user, update.effective_user.id, fields)
    if not saved:
        await update.message.reply_text(
            "База сейчас недоступна — данные держу только на время сессии, "
            "после перезапуска бота их придётся ввести заново. Разборы при этом работают."
        )
    await update.message.reply_text(TIME_KNOWN_TEXT, reply_markup=TIME_KNOWN_MENU)
    return ASK_TIME_KNOWN


async def onb_time_known(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    txt = (update.message.text or "").strip()
    if txt == BTN_T_UNKNOWN or _SKIP_RE.search(txt):
        await asyncio.to_thread(
            database.upsert_user, update.effective_user.id,
            {"birth_time": None, "time_precision": "unknown"},
        )
        return await _confirm(update, ctx)
    if txt == BTN_T_APPROX:
        ctx.user_data["time_precision"] = "approx"
        await update.message.reply_text(
            "Напиши примерное время в формате ЧЧ:ММ (например, 14:00). Если знаешь только «утром» — "
            "поставь середину: утро 09:00, день 14:00, вечер 19:00, ночь 02:00.",
            reply_markup=SKIP_MENU,
        )
        return ASK_TIME
    if txt == BTN_T_EXACT:
        ctx.user_data["time_precision"] = "exact"
        await update.message.reply_text("Время рождения в формате ЧЧ:ММ (например, 14:30):",
                                        reply_markup=SKIP_MENU)
        return ASK_TIME
    # ввели время сразу, минуя кнопки
    if _save_time(update.effective_user.id, txt, "exact"):
        await update.message.reply_text("Город рождения (например: Москва):", reply_markup=SKIP_MENU)
        return ASK_PLACE
    await update.message.reply_text("Выбери вариант кнопкой.", reply_markup=TIME_KNOWN_MENU)
    return ASK_TIME_KNOWN


async def onb_time(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    precision = ctx.user_data.pop("time_precision", "exact")
    if not _save_time(update.effective_user.id, update.message.text, precision):
        ctx.user_data["time_precision"] = precision
        await update.message.reply_text("Нужен формат ЧЧ:ММ, например 09:05. Или «Пропустить».")
        return ASK_TIME
    await update.message.reply_text("Город рождения (например: Москва):", reply_markup=SKIP_MENU)
    return ASK_PLACE


async def onb_place(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Ищу город на карте…")
    ok, msg = await _save_place(update.effective_user.id, update.message.text)
    if not ok:
        await update.message.reply_text(msg, reply_markup=SKIP_MENU)
        return ASK_PLACE
    return await _confirm(update, ctx)


async def onb_skip(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """«Пропустить» на вопросе о времени/городе."""
    ctx.user_data.pop("time_precision", None)
    user = await asyncio.to_thread(database.get_user, update.effective_user.id)
    if user and not user.get("birth_time") and not user.get("time_precision"):
        await asyncio.to_thread(
            database.upsert_user, update.effective_user.id, {"time_precision": "unknown"}
        )
    return await _confirm(update, ctx)


def _data_summary(user: dict) -> str:
    """Компактное подтверждение: имя, дата, время, город — без таймзон и админ-названий."""
    tp = user.get("time_precision") or ""
    t = user.get("birth_time")
    if t and tp == "approx":
        time_s = f"{t} (приблизительно)"
    elif t:
        time_s = t
    else:
        time_s = "не указано"
    gender = "женщина" if is_female(user.get("gender") or "") else ("мужчина" if user.get("gender") else "—")
    return (
        f"Имя: {user.get('name') or '—'}\n"
        f"Пол: {gender}\n"
        f"Дата рождения: {_date_out(user.get('birth_date'))}\n"
        f"Время рождения: {time_s}\n"
        f"Город рождения: {user.get('birth_place') or 'не указан'}"
    )


async def _confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    user = await asyncio.to_thread(database.get_user, update.effective_user.id) or {}
    await update.message.reply_text("Проверь, всё ли верно:\n\n" + _data_summary(user), reply_markup=CONFIRM_MENU)
    return ASK_CONFIRM


async def onb_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    txt = (update.message.text or "").strip()
    if txt == BTN_FIX:
        await update.message.reply_text("Что изменить?", reply_markup=DATA_MENU)
        return ConversationHandler.END
    if txt != BTN_OK:
        await update.message.reply_text("Нажми «Всё верно» или «Изменить».", reply_markup=CONFIRM_MENU)
        return ASK_CONFIRM
    user = await asyncio.to_thread(database.get_user, update.effective_user.id) or {}
    if user.get("focus") or ctx.user_data.get("pending"):
        return await _finish_or_run(update, ctx)
    await update.message.reply_text(
        "И последнее: что тебе сейчас интереснее всего? Я поставлю эту тему первой, "
        "остальные тоже будут доступны.",
        reply_markup=FOCUS_MENU,
    )
    return ASK_FOCUS


async def onb_focus(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    focus = _FOCUS_BY_BTN.get((update.message.text or "").strip())
    if focus:
        await asyncio.to_thread(database.upsert_user, update.effective_user.id, {"focus": focus})
    return await _finish_or_run(update, ctx)


async def _finish_or_run(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """После сбора данных: либо запускаем отложенный разбор, либо просто показываем меню."""
    user = await asyncio.to_thread(database.get_user, update.effective_user.id) or {}
    pending = ctx.user_data.pop("pending", None)
    if not pending:
        await update.message.reply_text(
            "Готово. Выбери тему — данные всегда можно поправить в «Мои данные».",
            reply_markup=main_menu(user),
        )
        return ConversationHandler.END
    await update.message.reply_text("Данные сохранены.", reply_markup=main_menu(user))
    kind = pending.get("kind")
    if kind == "topic":
        await _send_questions(update.message, pending["atype"])
    elif kind == "compat":
        await _send_partner_menu(update.message, update.effective_user.id)
    elif kind == "event":
        return await event_start(update, ctx)
    return ConversationHandler.END


# ---------- нажатие кнопки меню внутри анкеты ----------
_MENU_BUTTONS = (
    BTN_ME, BTN_REL, BTN_COMPAT, BTN_WORK, BTN_PERIOD, BTN_EVENT, BTN_DATA, BTN_HOW,
    BTN_EDIT_NAME, BTN_EDIT_DATE, BTN_EDIT_TIME, BTN_EDIT_PLACE, BTN_EDIT_GENDER, BTN_EDIT_FOCUS, BTN_BACK,
)
MENU_RE = "^(" + "|".join(re.escape(b) for b in _MENU_BUTTONS) + ")$"
_MENU_ROUTES: dict = {}  # кнопка → хендлер; заполняется в build_application


async def partner_menu_interrupt(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Анкета партнёра: выходим из неё, не записывая кнопку как имя/дату."""
    user = await asyncio.to_thread(database.get_user, update.effective_user.id)
    await update.message.reply_text(
        "Добавление партнёра прервано. Нажми нужную кнопку ещё раз.", reply_markup=main_menu(user)
    )
    return ConversationHandler.END


async def unknown_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Последний хендлер: бот никогда не молчит."""
    user = await asyncio.to_thread(database.get_user, update.effective_user.id)
    if user and user.get("name") and user.get("birth_date"):
        await update.message.reply_text("Выбери тему кнопкой ниже:", reply_markup=main_menu(user))
    else:
        await update.message.reply_text(
            "Я работаю по кнопкам. Нажми /start — познакомимся и сохраню твои данные.",
        )


async def menu_interrupt(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Внутри анкеты нажали кнопку меню — выполняем её, а не записываем как имя/дату."""
    ctx.user_data.pop("pending", None)
    ctx.user_data.pop("time_precision", None)
    fn = _MENU_ROUTES.get((update.message.text or "").strip())
    if fn is None:
        return ConversationHandler.END
    res = await fn(update, ctx)
    return res if isinstance(res, int) else ConversationHandler.END


# ---------- тема → вопросы ----------
def _has_basic(user: dict | None) -> bool:
    return bool(user and user.get("name") and user.get("birth_date"))


async def _collect_missing(update: Update, ctx: ContextTypes.DEFAULT_TYPE, user: dict | None, pending: dict) -> int:
    """Нет базовых данных — собираем, потом продолжаем с того места (pending)."""
    ctx.user_data["pending"] = pending
    if not user or not user.get("name"):
        await update.message.reply_text("Сначала познакомимся. Как тебя зовут?")
        return ASK_NAME
    ctx.user_data["name"] = user["name"]
    if not user.get("gender"):
        await update.message.reply_text("Ты мужчина или женщина?", reply_markup=GENDER_MENU)
        return ASK_GENDER
    ctx.user_data["gender"] = user["gender"]
    if not user.get("birth_date"):
        await update.message.reply_text("Дата рождения в формате ДД.ММ.ГГГГ (например, 15.05.1990):")
        return ASK_BIRTHDATE
    return await _finish_or_run(update, ctx)


async def topic_entry(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Тап по теме. Если данных нет — сначала собираем; иначе предлагаем выбрать вопрос."""
    atype = _TOPIC_TYPE[update.message.text.strip()]
    user = await asyncio.to_thread(database.get_user, update.effective_user.id)
    if not _has_basic(user) or not user.get("gender"):
        return await _collect_missing(update, ctx, user, {"kind": "topic", "atype": atype.value})
    # время неизвестно и человек об этом не говорил (старые данные) — спросим один раз
    if not user.get("birth_time") and not user.get("time_precision"):
        ctx.user_data["pending"] = {"kind": "topic", "atype": atype.value}
        await update.message.reply_text(TIME_KNOWN_TEXT, reply_markup=TIME_KNOWN_MENU)
        return ASK_TIME_KNOWN
    await _send_questions(update.message, atype.value)
    return ConversationHandler.END


async def _send_questions(msg, atype_val: str) -> None:
    title = ANALYSIS_TITLES.get(atype_val, atype_val)
    if atype_val == AnalysisType.current_period.value:
        rows = [[InlineKeyboardButton(label, callback_data=f"per:{pid}")] for pid, label in PERIODS]
        await msg.reply_text(f"{title}. Какой период смотрим?", reply_markup=InlineKeyboardMarkup(rows))
        return
    rows = [
        [InlineKeyboardButton(label, callback_data=f"q:{atype_val}:{qid}")]
        for qid, label, _ in QUESTIONS.get(atype_val, [])
    ]
    await msg.reply_text(f"{title}. Что именно разобрать?", reply_markup=InlineKeyboardMarkup(rows))


def _period_range(pid: str, today: date) -> tuple[date, date, str]:
    """Границы периода + подпись: ближайший месяц / следующий месяц / этот год."""
    if pid == "year":
        return date(today.year, 1, 1), date(today.year, 12, 31), f"{today.year} год"
    y, m = today.year, today.month
    if pid == "next":
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        start = date(y, m, 1)
    else:
        start = today
    end = date(y, m, calendar.monthrange(y, m)[1])
    return start, end, f"с {date_ru(start.isoformat())} по {date_ru(end.isoformat())}"


async def _do_analysis(
    msg, telegram_id: int, user: dict, atype: AnalysisType, question: str,
    period: tuple[date, date] | None = None, force: bool = False,
) -> None:
    """Считает разбор с живыми статусами и присылает короткий ответ + кнопки.

    Перед расчётом смотрит кэш: тот же вопрос при тех же данных — отдаём готовый, не гоняя AI.
    """
    title = ANALYSIS_TITLES.get(atype.value, atype.value)
    today = date.today()

    if not force:
        rec = await asyncio.to_thread(database.find_recent_profile, telegram_id, atype.value, question)
        cached = _valid_cache(rec, user, atype, today)
        if cached:
            await _present(msg, cached, user)
            return

    has_astro = bool(user.get("birth_time") and user.get("timezone"))
    stages = _STAGES_ASTRO if has_astro else _STAGES_BASIC

    req = ProfileRequest(
        name=user.get("name", ""),
        gender=user.get("gender", "") or "",
        birth_date=date.fromisoformat(user["birth_date"]),
        birth_time=user.get("birth_time"),
        time_precision=user.get("time_precision") or "",
        birth_place=user.get("birth_place"),
        lat=user.get("lat"),
        lon=user.get("lon"),
        timezone=user.get("timezone"),
        period_from=period[0] if period else None,
        period_to=period[1] if period else None,
        focus=user.get("focus") or "",
        main_request=question,
        analysis_type=atype,
    )
    # транзиты/прогрессии считаем внутри периода: сегодня, если период его включает, иначе — его начало
    calc_day = today if (period and period[0] <= today <= period[1]) else (period[0] if period else today)

    status = await msg.reply_text(f"{title}\n{stages[0]}")
    task = asyncio.create_task(asyncio.to_thread(build_profile, req, calc_day))
    await _spin(status, title, stages, task)

    try:
        profile = task.result()
    except Exception as e:  # noqa: BLE001
        logging.exception("build_profile failed")
        await asyncio.to_thread(
            database.log_error, "exception", "build_profile",
            f"{type(e).__name__}: {e}", {"atype": atype.value, "question": question}, telegram_id,
        )
        await status.edit_text(f"Не получилось собрать разбор: {type(e).__name__}: {e}")
        return

    data = profile.model_dump(mode="json")
    try:
        await asyncio.to_thread(database.save_profile, data, telegram_id)
    except Exception:  # noqa: BLE001
        logging.exception("save_profile failed")

    try:
        await status.delete()
    except Exception:  # noqa: BLE001
        pass
    await _present(msg, data, user)


async def _spin(status, title: str, stages: list[str], task: asyncio.Task) -> None:
    idx = 0
    while True:
        done, _ = await asyncio.wait({task}, timeout=2.4)
        if task in done:
            return
        idx += 1
        try:
            await status.edit_text(f"{title}\n{stages[idx % len(stages)]}")
        except Exception:  # noqa: BLE001 — «message is not modified» и т.п. не важны
            pass


# ---------- кэш, разбивка отчёта на разделы ----------
def _valid_cache(rec: dict | None, user: dict, atype: AnalysisType, today: date) -> dict | None:
    """Годен ли сохранённый разбор: данные не менялись, а период — ещё и свежий (сегодня)."""
    if not rec:
        return None
    data = rec.get("data") or {}
    ui = data.get("user_input") or {}
    if (
        ui.get("birth_date") != user.get("birth_date")
        or (ui.get("birth_time") or None) != (user.get("birth_time") or None)
        or (ui.get("birth_place") or None) != (user.get("birth_place") or None)
        or (ui.get("gender") or "") != (user.get("gender") or "")
    ):
        return None
    if atype == AnalysisType.current_period:
        created = (rec.get("created_at") or "")[:10]
        if created != today.isoformat():
            return None
    if not (data.get("report") or {}).get("full_report"):
        return None
    return data


def parse_sections(full_report: str) -> list[tuple[str, str]]:
    """Режем отчёт на разделы по заголовкам уровня ## → [(заголовок, тело), …]."""
    parts = re.split(r"(?m)^## ", full_report)
    sections: list[tuple[str, str]] = []
    for part in parts[1:]:
        title, _, body = part.partition("\n")
        sections.append((title.strip(), body.strip()))
    return sections


def _detail_sections(sections: list[tuple[str, str]]) -> list[tuple[int, str, str]]:
    """Разделы подробной расшифровки — всё, кроме «Коротко»."""
    return [(i, t, b) for i, (t, b) in enumerate(sections) if t.strip().lower() != synthesis.SHORT_SECTION.lower()]


def _short_text(data: dict) -> str:
    report = data.get("report") or {}
    return synthesis.short_section(report.get("full_report") or "") or (report.get("short_summary") or "").strip()


def _basis(data: dict) -> str:
    mods = data.get("calculation_modules") or {}
    if "person_a" in mods:  # синастрия: модули каждого
        mods = mods.get("person_a") or {}
    return synthesis.basis_line(mods)


def _main_keyboard(pid: str, atype_val: str) -> InlineKeyboardMarkup:
    """Кнопки под коротким ответом: подробнее, обратная связь, проще, данные, дополнительно."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Подробная расшифровка", callback_data=f"more:{pid}")],
        [InlineKeyboardButton("Откликается", callback_data=f"fb:{pid}:1"),
         InlineKeyboardButton("Не совсем", callback_data=f"fb:{pid}:0")],
        [InlineKeyboardButton("Объяснить проще", callback_data=f"simple:{pid}"),
         InlineKeyboardButton("Изменить данные", callback_data="data")],
        [InlineKeyboardButton("Дополнительно: карта, расчёт, озвучка", callback_data=f"extra:{pid}")],
    ])


def _sections_keyboard(pid: str, sections: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(title[:40], callback_data=f"s:{pid}:{i}")]
        for i, title, _ in _detail_sections(sections)
    ]
    rows.append([InlineKeyboardButton("К краткому ответу", callback_data=f"short:{pid}")])
    return InlineKeyboardMarkup(rows)


def _extra_keyboard(pid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Карта", callback_data=f"img:{pid}"),
         InlineKeyboardButton("Арт-обложка", callback_data=f"cov:{pid}")],
        [InlineKeyboardButton("Расчёт (что посчитано)", callback_data=f"tech:{pid}"),
         InlineKeyboardButton("Озвучить", callback_data=f"tts:{pid}")],
    ])


def _subject(data: dict) -> str:
    """Заголовок разбора: тема · вопрос/период/партнёр."""
    ui = data.get("user_input") or {}
    atype_val = ui.get("analysis_type") or "personality"
    title = ANALYSIS_TITLES.get(atype_val, atype_val)
    if atype_val == AnalysisType.current_period.value and ui.get("period_from"):
        return f"{title} · {date_ru(ui['period_from'])} — {date_ru(ui['period_to'])}"
    if atype_val == AnalysisType.event.value:
        ev = (data.get("meta") or {}).get("event") or {}
        return f"{title} · {date_ru(ev.get('date', ''))}"
    if atype_val == AnalysisType.compatibility.value:
        p = (data.get("meta") or {}).get("partner") or {}
        return f"{title} · {ui.get('name') or 'ты'} и {p.get('name') or 'партнёр'}"
    q = ui.get("main_request") or ""
    label = next((lbl for qid, lbl, text in QUESTIONS.get(atype_val, []) if text == q), "")
    return f"{title} · {label}" if label else title


async def _present(msg, data: dict, user: dict | None = None) -> None:
    """Короткий законченный ответ + строка «Основа разбора» + кнопки. Подробности — по кнопке."""
    report = data.get("report") or {}
    full = report.get("full_report") or ""
    pid = data.get("profile_id") or ""
    atype_val = (data.get("user_input") or {}).get("analysis_type") or "personality"
    short = _short_text(data)
    if not short or not pid:
        await send_md(msg, full)
        return
    basis = _basis(data)
    text = f"## {_subject(data)}\n\n{short}"
    if basis:
        text += f"\n\n_{basis}_"
    await send_md(msg, text, reply_markup=_main_keyboard(pid, atype_val))


async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Инлайн-кнопки: вопросы, периоды, разделы, обратная связь, дополнительно."""
    q = update.callback_query
    await q.answer()
    data = q.data or ""
    tid = q.from_user.id

    if data.startswith("q:"):  # вопрос внутри темы
        _, atype_val, qid = data.split(":", 2)
        question = next((text for i, _, text in QUESTIONS.get(atype_val, []) if i == qid), None)
        user = await asyncio.to_thread(database.get_user, tid)
        if not question or not _has_basic(user):
            await q.message.reply_text("Нет данных для разбора — нажми /start.")
            return
        await _do_analysis(q.message, tid, user, AnalysisType(atype_val), question)
    elif data.startswith("per:"):  # период
        pid_ = data.split(":", 1)[1]
        user = await asyncio.to_thread(database.get_user, tid)
        if not _has_basic(user):
            await q.message.reply_text("Нет данных для разбора — нажми /start.")
            return
        start, end, label = _period_range(pid_, date.today())
        question = f"Какие темы выделяются в период {label}? Что усиливается, где терять силы, что делать."
        await _do_analysis(q.message, tid, user, AnalysisType.current_period, question, period=(start, end))
    elif data.startswith("more:"):  # подробная расшифровка — список разделов
        pid = data.split(":", 1)[1]
        profile = await asyncio.to_thread(database.get_profile, pid)
        if not profile:
            await q.message.reply_text("Этот разбор уже устарел — сделай новый из меню.")
            return
        sections = parse_sections((profile.get("report") or {}).get("full_report") or "")
        await q.message.reply_text(
            "Подробная расшифровка — выбери раздел:", reply_markup=_sections_keyboard(pid, sections)
        )
    elif data.startswith("s:"):  # раздел
        _, pid, idx = data.split(":", 2)
        profile = await asyncio.to_thread(database.get_profile, pid)
        if not profile:
            await q.message.reply_text("Этот разбор уже устарел — сделай новый из меню.")
            return
        sections = parse_sections((profile.get("report") or {}).get("full_report") or "")
        i = int(idx)
        if 0 <= i < len(sections):
            title, body = sections[i]
            await send_md(q.message, f"## {title}\n\n{body}", reply_markup=_sections_keyboard(pid, sections))
    elif data.startswith("short:"):  # вернуться к краткому ответу
        pid = data.split(":", 1)[1]
        profile = await asyncio.to_thread(database.get_profile, pid)
        if profile:
            await _present(q.message, profile)
    elif data.startswith("simple:"):  # объяснить проще
        pid = data.split(":", 1)[1]
        await _send_simple(q.message, pid, tid)
    elif data.startswith("fb:"):  # откликается / не совсем
        _, pid, val = data.split(":", 2)
        await asyncio.to_thread(database.add_feedback, pid, "bot:resonates" if val == "1" else "bot:not_quite",
                                1 if val == "1" else 0)
        user = await asyncio.to_thread(database.get_user, tid)
        if val == "1":
            await q.message.reply_text("Спасибо, учту. Если хочешь копнуть глубже — открой подробную расшифровку.")
        else:
            await q.message.reply_text(
                "Спасибо, что " + _g(user, "сказал", "сказала") + ". Это интерпретация, а не вывод о тебе — "
                "она может не совпасть. Попробуй другой вопрос по теме или уточни данные "
                "(время и город рождения делают разбор точнее)."
            )
    elif data == "data":  # изменить данные
        user = await asyncio.to_thread(database.get_user, tid)
        if user:
            await q.message.reply_text(_data_summary(user) + "\n\nЧто изменить?", reply_markup=DATA_MENU)
        else:
            await q.message.reply_text("Данных пока нет — нажми /start.")
    elif data.startswith("extra:"):
        pid = data.split(":", 1)[1]
        await q.message.reply_text(
            "Дополнительно к разбору:\n"
            "• Карта — схема расчётных положений на момент рождения\n"
            "• Арт-обложка — абстрактная картинка по твоему профилю (на память)\n"
            "• Расчёт — что именно посчитано и по каким системам\n"
            "• Озвучить — аудио-версия полного разбора",
            reply_markup=_extra_keyboard(pid),
        )
    elif data.startswith("tech:"):
        pid = data.split(":", 1)[1]
        profile = await asyncio.to_thread(database.get_profile, pid)
        tech = ((profile or {}).get("report") or {}).get("tech_methods") if profile else None
        if not tech:
            await q.message.reply_text("Расчёт для этого разбора недоступен.")
        else:
            await send_md(q.message, tech)
    elif data.startswith("tts:"):
        pid = data.split(":", 1)[1]
        await _send_voice(q.message, pid, tid)
    elif data.startswith("img:"):
        pid = data.split(":", 1)[1]
        await _send_chart(q.message, pid)
    elif data.startswith("cov:"):
        pid = data.split(":", 1)[1]
        await _send_cover(q.message, pid)
    elif data.startswith("pd:"):  # удалить партнёра
        pid = data.split(":", 1)[1]
        await asyncio.to_thread(database.delete_partner, pid)
        await q.message.reply_text("Партнёр удалён.")
        await _send_partner_menu(q.message, tid)
    elif data.startswith("p:") and data != "p:add":  # разбор с сохранённым партнёром
        pid = data.split(":", 1)[1]
        await _do_synastry(q.message, tid, pid)


async def _send_simple(msg, profile_id: str, telegram_id: int) -> None:
    """«Объяснить проще»: краткий ответ ещё проще. Результат кэшируем в профиле."""
    profile = await asyncio.to_thread(database.get_profile, profile_id)
    if not profile:
        await msg.reply_text("Этот разбор уже устарел — сделай новый из меню.")
        return
    cached = profile.get("simple_text")
    if cached:
        await send_md(msg, cached)
        return
    short = _short_text(profile)
    note = await msg.reply_text("Переформулирую проще…")
    gender = (profile.get("user_input") or {}).get("gender") or ""
    simple = await asyncio.to_thread(synthesis.simplify, short, gender, telegram_id)
    try:
        await note.delete()
    except Exception:  # noqa: BLE001
        pass
    if not simple:
        await msg.reply_text("Сейчас не получилось — попробуй чуть позже.")
        return
    await asyncio.to_thread(database.set_profile_field, profile_id, "simple_text", simple)
    await send_md(msg, simple)


# ---------- визуалы, озвучка ----------
async def _send_voice(msg, profile_id: str, telegram_id: int) -> None:
    profile = await asyncio.to_thread(database.get_profile, profile_id)
    full = ((profile or {}).get("report") or {}).get("full_report") if profile else None
    if not full:
        await msg.reply_text("Этот разбор уже устарел — сделай новый из меню.")
        return
    note = await msg.reply_text("Озвучиваю разбор (несколько секунд)…")
    try:
        audio = await tts.synth(full)
    except Exception as e:  # noqa: BLE001
        logging.exception("tts.synth failed")
        await asyncio.to_thread(
            database.log_error, "exception", "tts", f"{type(e).__name__}: {e}", None, telegram_id
        )
        await note.edit_text("Не получилось озвучить — попробуй ещё раз.")
        return
    if not audio:
        await note.edit_text("Нечего озвучивать.")
        return
    bio = io.BytesIO(audio)
    bio.name = "razbor.mp3"
    await msg.reply_audio(bio, title="Разбор", performer="Разбор", caption="Аудио-версия разбора")
    try:
        await note.delete()
    except Exception:  # noqa: BLE001
        pass


async def _send_chart(msg, profile_id: str) -> None:
    profile = await asyncio.to_thread(database.get_profile, profile_id)
    if not profile:
        await msg.reply_text("Этот разбор уже устарел — сделай новый из меню.")
        return
    note = await msg.reply_text("Рисую карту по расчётным данным…")
    try:
        png = await asyncio.to_thread(viz.render_chart, profile)
    except Exception as e:  # noqa: BLE001
        logging.exception("render_chart failed")
        await note.edit_text(f"Не получилось нарисовать карту: {type(e).__name__}")
        return
    bio = io.BytesIO(png)
    bio.name = "chart.png"
    await msg.reply_photo(bio, caption="Карта по расчётным данным на момент рождения")
    try:
        await note.delete()
    except Exception:  # noqa: BLE001
        pass


async def _send_cover(msg, profile_id: str) -> None:
    profile = await asyncio.to_thread(database.get_profile, profile_id)
    if not profile:
        await msg.reply_text("Этот разбор уже устарел — сделай новый из меню.")
        return
    cached = profile.get("cover_url")
    if cached:
        await msg.reply_photo(cached, caption="Арт-обложка по твоему профилю")
        return
    note = await msg.reply_text("Генерирую обложку (до минуты)…")
    url, caption = await asyncio.to_thread(imagegen.cover, profile)
    if not url:
        await note.edit_text(caption)
        return
    try:
        await asyncio.to_thread(database.set_cover, profile_id, url)
    except Exception:  # noqa: BLE001
        logging.exception("set_cover failed")
    await msg.reply_photo(url, caption=caption)
    try:
        await note.delete()
    except Exception:  # noqa: BLE001
        pass


# ---------- совместимость (синастрия) ----------
async def compat_entry(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    user = await asyncio.to_thread(database.get_user, update.effective_user.id)
    if not _has_basic(user) or not user.get("gender"):
        return await _collect_missing(update, ctx, user, {"kind": "compat"})
    await _send_partner_menu(update.message, update.effective_user.id)
    return ConversationHandler.END


async def _send_partner_menu(msg, telegram_id: int) -> None:
    partners = await asyncio.to_thread(database.list_partners, telegram_id)
    rows = []
    for p in partners:
        title = f"{p.get('name') or 'без имени'} · {_date_out(p.get('birth_date'))}".strip()
        rows.append([InlineKeyboardButton(title[:40], callback_data=f"p:{p['partner_id']}"),
                     InlineKeyboardButton("удалить", callback_data=f"pd:{p['partner_id']}")])
    rows.append([InlineKeyboardButton("Добавить человека", callback_data="p:add")])
    text = (
        "С кем смотрим совместимость? Выбери из сохранённых или добавь нового."
        if partners
        else "Совместимость — разбор с конкретным человеком. Нужны его имя и дата рождения, "
             "время и город — по желанию."
    )
    await msg.reply_text(text, reply_markup=InlineKeyboardMarkup(rows))


async def _do_synastry(msg, telegram_id: int, partner_id: str) -> None:
    user = await asyncio.to_thread(database.get_user, telegram_id)
    partner = await asyncio.to_thread(database.get_partner, partner_id)
    if not user or not partner:
        await msg.reply_text("Не нашёл данные — попробуй ещё раз из меню.")
        return

    user_req = ProfileRequest(
        name=user.get("name", ""), gender=user.get("gender", "") or "",
        birth_date=date.fromisoformat(user["birth_date"]),
        birth_time=user.get("birth_time"), birth_place=user.get("birth_place"),
        lat=user.get("lat"), lon=user.get("lon"), timezone=user.get("timezone"),
        analysis_type=AnalysisType.compatibility,
    )
    partner_req = ProfileRequest(
        name=partner.get("name", ""), birth_date=date.fromisoformat(partner["birth_date"]),
        birth_time=partner.get("birth_time"), birth_place=partner.get("birth_place"),
        lat=partner.get("lat"), lon=partner.get("lon"), timezone=partner.get("timezone"),
        analysis_type=AnalysisType.compatibility,
    )

    both_astro = bool(user.get("birth_time") and user.get("timezone")
                      and partner.get("birth_time") and partner.get("timezone"))
    stages = _STAGES_ASTRO if both_astro else _STAGES_BASIC
    title = f"Совместимость · {user.get('name') or 'ты'} и {partner.get('name') or 'партнёр'}"

    status = await msg.reply_text(f"{title}\n{stages[0]}")
    task = asyncio.create_task(asyncio.to_thread(build_synastry, user_req, partner_req))
    await _spin(status, title, stages, task)

    try:
        profile = task.result()
    except Exception as e:  # noqa: BLE001
        logging.exception("build_synastry failed")
        await asyncio.to_thread(
            database.log_error, "exception", "build_synastry",
            f"{type(e).__name__}: {e}", {"partner_id": partner_id}, telegram_id,
        )
        await status.edit_text(f"Не получилось собрать совместимость: {type(e).__name__}: {e}")
        return

    data = profile.model_dump(mode="json")
    try:
        await asyncio.to_thread(database.save_profile, data, telegram_id)
    except Exception:  # noqa: BLE001
        logging.exception("save_profile (synastry) failed")
    try:
        await status.delete()
    except Exception:  # noqa: BLE001
        pass
    await _present(msg, data, user)


# ---------- выбор даты (событие) ----------
async def event_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    user = await asyncio.to_thread(database.get_user, update.effective_user.id)
    if not _has_basic(user) or not user.get("gender"):
        return await _collect_missing(update, ctx, user, {"kind": "event"})
    ctx.user_data.pop("ev_date", None)
    await update.message.reply_text(
        "Выбор даты — оценка, насколько конкретный день подходит тебе для дела: сделки, "
        "переговоров, запуска, важного разговора.\n\nКакую дату смотрим? Формат ДД.ММ.ГГГГ."
    )
    return EV_DATE


async def event_date_save(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ev = parse_date(update.message.text)
    if not ev:
        await update.message.reply_text("Нужен формат ДД.ММ.ГГГГ, например 15.07.2026.")
        return EV_DATE
    ctx.user_data["ev_date"] = ev.isoformat()
    await update.message.reply_text(
        "Коротко опиши, что за событие: «подписание договора аренды», «запуск продукта», "
        "«разговор о повышении», «свадьба»… Чем конкретнее — тем точнее ответ."
    )
    return EV_DESC


async def event_desc_save(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ev_iso = ctx.user_data.pop("ev_date", None)
    user = await asyncio.to_thread(database.get_user, update.effective_user.id)
    if not ev_iso:
        await update.message.reply_text("Сбой — начни заново через «Выбор даты».", reply_markup=main_menu(user))
        return ConversationHandler.END
    desc = update.message.text.strip()
    await _do_event(update.message, update.effective_user.id, user, date.fromisoformat(ev_iso), desc)
    return ConversationHandler.END


async def _do_event(msg, telegram_id: int, user: dict, ev_date: date, desc: str) -> None:
    has_astro = bool(user.get("birth_time") and user.get("timezone"))
    stages = _STAGES_ASTRO if has_astro else _STAGES_BASIC
    title = f"Выбор даты · {date_ru(ev_date.isoformat())}"

    req = ProfileRequest(
        name=user.get("name", ""), gender=user.get("gender", "") or "",
        birth_date=date.fromisoformat(user["birth_date"]),
        birth_time=user.get("birth_time"), birth_place=user.get("birth_place"),
        lat=user.get("lat"), lon=user.get("lon"), timezone=user.get("timezone"),
        main_request=desc, analysis_type=AnalysisType.event,
    )

    status = await msg.reply_text(f"{title}\n{stages[0]}")
    task = asyncio.create_task(asyncio.to_thread(build_event, req, ev_date, desc))
    await _spin(status, title, stages, task)

    try:
        profile = task.result()
    except Exception as e:  # noqa: BLE001
        logging.exception("build_event failed")
        await asyncio.to_thread(
            database.log_error, "exception", "build_event",
            f"{type(e).__name__}: {e}", {"event_date": ev_date.isoformat(), "desc": desc}, telegram_id,
        )
        await status.edit_text(f"Не получилось собрать разбор даты: {type(e).__name__}: {e}")
        return

    data = profile.model_dump(mode="json")
    try:
        await asyncio.to_thread(database.save_profile, data, telegram_id)
    except Exception:  # noqa: BLE001
        logging.exception("save_profile (event) failed")
    try:
        await status.delete()
    except Exception:  # noqa: BLE001
        pass
    await _present(msg, data, user)


# ---------- добавление партнёра ----------
async def partner_add_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    ctx.user_data["np"] = {}
    await update.callback_query.message.reply_text("Имя человека:")
    return P_NAME


async def partner_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data.setdefault("np", {})["name"] = update.message.text.strip()
    await update.message.reply_text("Дата рождения (ДД.ММ.ГГГГ):")
    return P_DATE


async def partner_date(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    bd = parse_date(update.message.text)
    if not bd:
        await update.message.reply_text("Нужен формат ДД.ММ.ГГГГ, например 20.08.1992.")
        return P_DATE
    ctx.user_data.setdefault("np", {})["birth_date"] = bd.isoformat()
    await update.message.reply_text(
        "Время рождения ЧЧ:ММ, если известно — или «Пропустить».", reply_markup=SKIP_MENU
    )
    return P_TIME


async def partner_time(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    t = parse_time(update.message.text)
    if not t:
        await update.message.reply_text("Нужен формат ЧЧ:ММ, например 09:15. Или «Пропустить».")
        return P_TIME
    ctx.user_data.setdefault("np", {})["birth_time"] = t
    await update.message.reply_text("Город рождения (например: Москва) — или «Пропустить».", reply_markup=SKIP_MENU)
    return P_PLACE


async def partner_place(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Ищу город на карте…")
    geo = await asyncio.to_thread(ephemeris.resolve_geo, update.message.text.strip())
    if geo is None:
        await update.message.reply_text("Не нашёл город. Попробуй иначе или «Пропустить».", reply_markup=SKIP_MENU)
        return P_PLACE
    np = ctx.user_data.setdefault("np", {})
    np.update({"birth_place": update.message.text.strip(),
               "lat": geo["lat"], "lon": geo["lon"], "timezone": geo["timezone"]})
    return await _finish_partner(update, ctx)


async def partner_skip(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    return await _finish_partner(update, ctx)


async def _finish_partner(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    np = ctx.user_data.pop("np", {})
    user = await asyncio.to_thread(database.get_user, update.effective_user.id)
    if not np.get("name") or not np.get("birth_date"):
        await update.message.reply_text("Не хватило имени или даты — начни заново.", reply_markup=main_menu(user))
        return ConversationHandler.END
    partner_id = await asyncio.to_thread(database.add_partner, update.effective_user.id, np)
    await update.message.reply_text(f"«{np['name']}» — сохранено. Считаю совместимость…",
                                    reply_markup=main_menu(user))
    await _do_synastry(update.message, update.effective_user.id, partner_id)
    return ConversationHandler.END


# ---------- мои данные ----------
async def show_data(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = await asyncio.to_thread(database.get_user, update.effective_user.id)
    if not user:
        await update.message.reply_text("Данных пока нет. Нажми /start — познакомимся.")
        return
    focus = _FOCUS_TITLE.get(user.get("focus") or "", "не выбрано")
    await update.message.reply_text(
        _data_summary(user) + f"\nЧто важнее сейчас: {focus}\n\nЧто изменить?",
        reply_markup=DATA_MENU,
    )


async def edit_name_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Новое имя:")
    return EDIT_NAME


async def edit_name_save(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await asyncio.to_thread(database.upsert_user, update.effective_user.id, {"name": update.message.text.strip()})
    return await _edited(update, "Имя обновлено.")


async def edit_date_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Новая дата рождения (ДД.ММ.ГГГГ):")
    return EDIT_BIRTHDATE


async def edit_date_save(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    bd = parse_date(update.message.text)
    if not bd:
        await update.message.reply_text("Нужен формат ДД.ММ.ГГГГ.")
        return EDIT_BIRTHDATE
    await asyncio.to_thread(database.upsert_user, update.effective_user.id, {"birth_date": bd.isoformat()})
    return await _edited(update, "Дата обновлена.")


async def edit_time_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Изменить время: тот же сценарий, что в анкете (точно/приблизительно/не знаю → город → проверка)."""
    await update.message.reply_text(TIME_KNOWN_TEXT, reply_markup=TIME_KNOWN_MENU)
    return ASK_TIME_KNOWN


async def edit_place_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Новый город рождения (например: Москва):")
    return EDIT_PLACE


async def edit_place_save(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Ищу город на карте…")
    ok, msg = await _save_place(update.effective_user.id, update.message.text)
    if not ok:
        await update.message.reply_text(msg)
        return EDIT_PLACE
    return await _edited(update, msg)


async def edit_gender_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Ты мужчина или женщина?", reply_markup=GENDER_MENU)
    return EDIT_GENDER


async def edit_gender_save(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    gender = _parse_gender(update.message.text)
    if not gender:
        await update.message.reply_text("Выбери кнопкой: Женщина или Мужчина.", reply_markup=GENDER_MENU)
        return EDIT_GENDER
    await asyncio.to_thread(database.upsert_user, update.effective_user.id, {"gender": gender})
    if ctx.user_data.pop("gender_then_menu", None):
        return await _edited(update, "Спасибо. Выбери тему — данные уже сохранены.")
    return await _edited(update, "Сохранено.")


async def edit_focus_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Что тебе сейчас интереснее всего? Эта тема встанет первой в меню.",
                                    reply_markup=FOCUS_MENU)
    return EDIT_FOCUS


async def edit_focus_save(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    focus = _FOCUS_BY_BTN.get((update.message.text or "").strip())
    if not focus:
        await update.message.reply_text("Выбери вариант кнопкой.", reply_markup=FOCUS_MENU)
        return EDIT_FOCUS
    await asyncio.to_thread(database.upsert_user, update.effective_user.id, {"focus": focus})
    return await _edited(update, "Сохранено.")


async def _edited(update: Update, text: str) -> int:
    user = await asyncio.to_thread(database.get_user, update.effective_user.id)
    await update.message.reply_text(text, reply_markup=main_menu(user))
    return ConversationHandler.END


async def back_to_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = await asyncio.to_thread(database.get_user, update.effective_user.id)
    await update.message.reply_text("Меню:", reply_markup=main_menu(user))


async def how_it_works(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data.pop("pending", None)
    await send_md(update.message, METHOD_BASIS)
    return ConversationHandler.END


async def cmd_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data.pop("pending", None)
    user = await asyncio.to_thread(database.get_user, update.effective_user.id)
    await update.message.reply_text("Главное меню:", reply_markup=main_menu(user))
    return ConversationHandler.END


async def cmd_data(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data.pop("pending", None)
    await show_data(update, ctx)
    return ConversationHandler.END


async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data.pop("pending", None)
    ctx.user_data.pop("np", None)
    user = await asyncio.to_thread(database.get_user, update.effective_user.id)
    await update.message.reply_text("Отменено.", reply_markup=main_menu(user))
    return ConversationHandler.END


# ---------- парсеры и сохранялки ----------
_DATE_RE = re.compile(r"^\s*(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})\s*$")


def parse_date(raw: str) -> date | None:
    """ДД.ММ.ГГГГ (также ДД/ММ/ГГГГ, ДД-ММ-ГГГГ) и старый ГГГГ-ММ-ДД."""
    txt = (raw or "").strip()
    m = _DATE_RE.match(txt)
    try:
        if m:
            d, mo, y = (int(x) for x in m.groups())
            return date(y, mo, d)
        return date.fromisoformat(txt)
    except ValueError:
        return None


def parse_time(raw: str) -> str | None:
    txt = (raw or "").strip().replace(".", ":").replace("-", ":")
    try:
        hh, mm = (int(x) for x in txt.split(":")[:2])
    except (ValueError, IndexError):
        return None
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        return None
    return f"{hh:02d}:{mm:02d}"


def _parse_gender(raw: str) -> str:
    t = (raw or "").strip().lower()
    if t in (BTN_FEMALE.lower(), "ж", "женский", "девушка", "f", "female"):
        return "ж"
    if t in (BTN_MALE.lower(), "м", "мужской", "парень", "m", "male"):
        return "м"
    return ""


def _date_out(iso: str | None) -> str:
    """ГГГГ-ММ-ДД → ДД.ММ.ГГГГ для показа."""
    if not iso:
        return "—"
    try:
        y, m, d = (int(x) for x in str(iso)[:10].split("-"))
        return f"{d:02d}.{m:02d}.{y}"
    except ValueError:
        return str(iso)


def _save_time(telegram_id: int, raw: str, precision: str = "exact") -> bool:
    t = parse_time(raw)
    if not t:
        return False
    database.upsert_user(telegram_id, {"birth_time": t, "time_precision": precision})
    return True


async def _save_place(telegram_id: int, raw: str) -> tuple[bool, str]:
    """Геокодировать город → координаты+таймзона и сохранить. Возвращает (ok, текст)."""
    place = raw.strip()
    geo = await asyncio.to_thread(ephemeris.resolve_geo, place)
    if geo is None:
        return False, "Не нашёл такой город. Попробуй иначе (например: «Москва, Россия») или «Пропустить»."
    await asyncio.to_thread(
        database.upsert_user, telegram_id,
        {"birth_place": place, "lat": geo["lat"], "lon": geo["lon"], "timezone": geo["timezone"]},
    )
    return True, f"Город сохранён: {place}."


# ---------- вывод текста: Markdown → аккуратный Telegram-HTML ----------
def md_to_html(text: str) -> str:
    """«## Заголовок» → жирный, «**x**» → жирный, «_x_» → курсив, «- » → «• », без ---/###."""
    out: list[str] = []
    for line in (text or "").splitlines():
        s = line.rstrip()
        if s.strip() in ("---", "***", "___"):
            continue
        m = re.match(r"^\s*#{1,6}\s*(.+?)\s*#*\s*$", s)
        if m:
            out.append(f"<b>{html.escape(m.group(1))}</b>")
            continue
        m = re.match(r"^(\s*)[-*•]\s+(.*)$", s)
        if m:
            s = f"{m.group(1)}• {m.group(2)}"
        esc = html.escape(s)
        esc = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", esc)
        esc = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", esc)
        esc = re.sub(r"(?<!\w)_(?!\s)(.+?)(?<!\s)_(?!\w)", r"<i>\1</i>", esc)
        out.append(esc)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def md_to_plain(text: str) -> str:
    return re.sub(r"</?[bi]>", "", html.unescape(md_to_html(text)))


async def send_md(msg, text: str, reply_markup=None) -> None:
    """Отправить Markdown-текст читаемо: HTML-разметка, при сбое — чистый текст."""
    try:
        await _send_long(msg, md_to_html(text), reply_markup=reply_markup, parse_mode="HTML")
    except BadRequest:
        await _send_long(msg, md_to_plain(text), reply_markup=reply_markup)


async def _send_long(msg, text: str, reply_markup=None, parse_mode=None) -> None:
    """Telegram лимит ~4096 символов — режем по абзацам; кнопки — на последнем куске."""
    limit = 3800
    while text:
        if len(text) <= limit:
            await msg.reply_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
            break
        cut = text.rfind("\n", 0, limit)
        cut = cut if cut > 0 else limit
        await msg.reply_text(text[:cut], parse_mode=parse_mode)
        text = text[cut:].lstrip("\n")


async def on_error(update: object, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    logging.exception("PTB handler error", exc_info=ctx.error)
    tid = None
    if isinstance(update, Update) and update.effective_user:
        tid = update.effective_user.id
    await asyncio.to_thread(
        database.log_error, "exception", "ptb_handler",
        f"{type(ctx.error).__name__}: {ctx.error}", None, tid,
    )


def build_application() -> Application:
    app = Application.builder().token(_token()).build()
    app.add_error_handler(on_error)

    global _MENU_ROUTES
    _MENU_ROUTES = {
        BTN_ME: topic_entry,
        BTN_REL: topic_entry,
        BTN_WORK: topic_entry,
        BTN_PERIOD: topic_entry,
        BTN_COMPAT: compat_entry,
        BTN_EVENT: event_start,
        BTN_DATA: show_data,
        BTN_HOW: how_it_works,
        BTN_EDIT_NAME: edit_name_start,
        BTN_EDIT_DATE: edit_date_start,
        BTN_EDIT_TIME: edit_time_start,
        BTN_EDIT_PLACE: edit_place_start,
        BTN_EDIT_GENDER: edit_gender_start,
        BTN_EDIT_FOCUS: edit_focus_start,
        BTN_BACK: back_to_menu,
    }
    menu_break = MessageHandler(filters.Regex(MENU_RE), menu_interrupt)
    skip = MessageHandler(filters.Regex(_SKIP_RE), onb_skip)
    text = filters.TEXT & ~filters.COMMAND
    common_fallbacks = [
        CommandHandler("cancel", cmd_cancel),
        CommandHandler("menu", cmd_menu),
        CommandHandler("data", cmd_data),
        CommandHandler("about", how_it_works),
        CommandHandler("start", cmd_start),
    ]
    topics = "|".join(re.escape(b) for b in _TOPIC_TYPE)
    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", cmd_start),
            MessageHandler(filters.Regex(f"^({topics})$"), topic_entry),
            MessageHandler(filters.Regex(f"^{re.escape(BTN_COMPAT)}$"), compat_entry),
            MessageHandler(filters.Regex(f"^{re.escape(BTN_EVENT)}$"), event_start),
            MessageHandler(filters.Regex(f"^{re.escape(BTN_EDIT_NAME)}$"), edit_name_start),
            MessageHandler(filters.Regex(f"^{re.escape(BTN_EDIT_DATE)}$"), edit_date_start),
            MessageHandler(filters.Regex(f"^{re.escape(BTN_EDIT_TIME)}$"), edit_time_start),
            MessageHandler(filters.Regex(f"^{re.escape(BTN_EDIT_PLACE)}$"), edit_place_start),
            MessageHandler(filters.Regex(f"^{re.escape(BTN_EDIT_GENDER)}$"), edit_gender_start),
            MessageHandler(filters.Regex(f"^{re.escape(BTN_EDIT_FOCUS)}$"), edit_focus_start),
        ],
        states={
            ASK_NAME: [menu_break, MessageHandler(text, onb_name)],
            ASK_GENDER: [menu_break, MessageHandler(text, onb_gender)],
            ASK_BIRTHDATE: [menu_break, MessageHandler(text, onb_birthdate)],
            ASK_TIME_KNOWN: [menu_break, MessageHandler(text, onb_time_known)],
            ASK_TIME: [menu_break, skip, MessageHandler(text, onb_time)],
            ASK_PLACE: [menu_break, skip, MessageHandler(text, onb_place)],
            ASK_CONFIRM: [menu_break, MessageHandler(text, onb_confirm)],
            ASK_FOCUS: [menu_break, MessageHandler(text, onb_focus)],
            EDIT_NAME: [menu_break, MessageHandler(text, edit_name_save)],
            EDIT_BIRTHDATE: [menu_break, MessageHandler(text, edit_date_save)],
            EDIT_PLACE: [menu_break, MessageHandler(text, edit_place_save)],
            EDIT_GENDER: [menu_break, MessageHandler(text, edit_gender_save)],
            EDIT_FOCUS: [menu_break, MessageHandler(text, edit_focus_save)],
            EV_DATE: [menu_break, MessageHandler(text, event_date_save)],
            EV_DESC: [menu_break, MessageHandler(text, event_desc_save)],
        },
        fallbacks=common_fallbacks,
    )
    app.add_handler(conv)

    # Добавление партнёра — отдельная беседа, вход по инлайн-кнопке.
    pmenu_break = MessageHandler(filters.Regex(MENU_RE), partner_menu_interrupt)
    pskip = MessageHandler(filters.Regex(_SKIP_RE), partner_skip)
    partner_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(partner_add_start, pattern="^p:add$")],
        states={
            P_NAME: [pmenu_break, MessageHandler(text, partner_name)],
            P_DATE: [pmenu_break, MessageHandler(text, partner_date)],
            P_TIME: [pmenu_break, pskip, MessageHandler(text, partner_time)],
            P_PLACE: [pmenu_break, pskip, MessageHandler(text, partner_place)],
        },
        fallbacks=common_fallbacks,
    )
    app.add_handler(partner_conv)

    app.add_handler(CommandHandler("menu", cmd_menu))
    app.add_handler(CommandHandler("data", cmd_data))
    app.add_handler(CommandHandler("about", how_it_works))
    app.add_handler(MessageHandler(filters.Regex(f"^{re.escape(BTN_DATA)}$"), show_data))
    app.add_handler(MessageHandler(filters.Regex(f"^{re.escape(BTN_HOW)}$"), how_it_works))
    app.add_handler(MessageHandler(filters.Regex(f"^{re.escape(BTN_BACK)}$"), back_to_menu))
    app.add_handler(CallbackQueryHandler(on_callback))
    # Самым последним: любой текст, не подошедший никуда, — вместо тишины показываем меню.
    app.add_handler(MessageHandler(filters.TEXT, unknown_text))
    return app


def _token() -> str:
    from . import config

    return config.TELEGRAM_BOT_TOKEN
