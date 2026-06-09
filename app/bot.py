"""Telegram-бот Matrix Engine. Живёт в одном процессе с FastAPI.

Бот запоминает данные пользователя (имя, дата, опц. время+место) в Supabase,
показывает меню с кнопками и запускает любой разбор одним тапом — без повторного ввода.

Время и место рождения опциональны: без них работают числа и арканы, с ними
разблокируются астрология, джйотиш и Ба Цзы. Если разбор запущен без этих данных,
бот сам предлагает их дать. Во время расчёта показываются живые статусы («спутники»).
"""
from __future__ import annotations

import asyncio
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
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from . import database, imagegen, tts, viz
from .calc import ephemeris
from .engine import build_event, build_profile, build_synastry
from .models import AnalysisType, ProfileRequest
from .synthesis import CREDIBILITY_SHORT, LOADING_MESSAGES, METHOD_BASIS

# --- состояния онбординга/редактирования/добавления партнёра ---
(
    ASK_NAME,
    ASK_BIRTHDATE,
    ASK_TIME,
    ASK_PLACE,
    EDIT_NAME,
    EDIT_BIRTHDATE,
    EDIT_TIME,
    EDIT_PLACE,
    EDIT_GENDER,
    P_NAME,
    P_DATE,
    P_TIME,
    P_PLACE,
    EV_DATE,
    EV_DESC,
) = range(15)

# --- кнопки меню ---
BTN_PERSON = "🧬 Личность"
BTN_PERIOD = "🌗 Текущий период"
BTN_WORK = "💼 Работа и деньги"
BTN_COMPAT = "❤️ Совместимость"
BTN_EVENT = "🤝 Сделка / Событие"
BTN_DATA = "👤 Мои данные"
BTN_EDIT_NAME = "✏️ Изменить имя"
BTN_EDIT_DATE = "✏️ Изменить дату"
BTN_EDIT_TIME = "🕐 Время рождения"
BTN_EDIT_PLACE = "📍 Место рождения"
BTN_EDIT_GENDER = "⚧ Пол"
BTN_BACK = "⬅️ В меню"
BTN_SKIP = "⏭️ Пропустить"
# Совпадает с кнопкой И с ручным вводом: «Пропустить», пропустить, skip — в любом регистре/кавычках.
_SKIP_RE = re.compile(r"пропуст|skip", re.IGNORECASE)

_ANALYSIS = {
    BTN_PERSON: (AnalysisType.personality, "Разбор личности"),
    BTN_PERIOD: (AnalysisType.current_period, "Текущий период"),
    BTN_WORK: (AnalysisType.work, "Работа и деньги"),
}

MAIN_MENU = ReplyKeyboardMarkup(
    [[BTN_PERSON, BTN_PERIOD], [BTN_WORK, BTN_COMPAT], [BTN_EVENT], [BTN_DATA]],
    resize_keyboard=True,
)
DATA_MENU = ReplyKeyboardMarkup(
    [
        [BTN_EDIT_NAME, BTN_EDIT_DATE],
        [BTN_EDIT_TIME, BTN_EDIT_PLACE],
        [BTN_EDIT_GENDER, BTN_BACK],
    ],
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
# Полный набор «спутниковых» статусов — для разбора с астрологией (время+место).
_STAGES_ASTRO = LOADING_MESSAGES
# Без времени/места небесную часть не показываем — берём числовые/поведенческие статусы.
_STAGES_BASIC = [m for m in LOADING_MESSAGES if m[0] not in "🛰📡🪐🌙✴️📐🔭🧭🌌"][:8] or LOADING_MESSAGES[8:14]


# ---------- /start: онбординг или меню ----------
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    user = await asyncio.to_thread(database.get_user, update.effective_user.id)
    if user and user.get("name") and user.get("birth_date"):
        await update.message.reply_text(
            f"С возвращением, {user['name']}.\nВыбери разбор — данные уже сохранены.\n"
            "/about — на чём построена Матрица (метод).",
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
    await _do_analysis(update.message, update.effective_user.id, label, user)
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
        await _do_analysis(update.message, update.effective_user.id, label, user)
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


async def _do_analysis(msg, telegram_id: int, label: str, user: dict, force: bool = False) -> None:
    """Считает профиль с живыми статусами ожидания и присылает отчёт по разделам.

    Перед расчётом проверяет кэш в базе: если такой же разбор уже есть и данные
    не менялись — отдаёт его, не гоняя AI повторно. force=True — пересчитать заново.
    """
    atype, request = _ANALYSIS[label]
    today = date.today()

    if not force:
        rec = await asyncio.to_thread(database.find_recent_profile, telegram_id, atype.value)
        cached = _valid_cache(rec, user, atype, today)
        if cached:
            await msg.reply_text(f"{label}: беру готовый разбор из памяти 📂")
            await _present(msg, cached)
            await msg.reply_text("Выбери следующий разбор:", reply_markup=MAIN_MENU)
            return

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

    status = await msg.reply_text(f"{label}\n{stages[0]}")
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
        await asyncio.to_thread(
            database.log_error, "exception", "build_profile",
            f"{type(e).__name__}: {e}", {"label": label}, telegram_id,
        )
        await status.edit_text(f"Не получилось собрать разбор: {type(e).__name__}: {e}")
        return

    data = profile.model_dump(mode="json")
    try:
        await asyncio.to_thread(database.save_profile, data, telegram_id)
    except Exception:  # noqa: BLE001
        logging.exception("save_profile failed")

    try:
        await status.edit_text(f"{label}: готово ✅")
    except Exception:  # noqa: BLE001
        pass
    await _present(msg, data)
    await msg.reply_text("Выбери следующий разбор:", reply_markup=MAIN_MENU)


# ---------- кэш, разбивка отчёта на разделы-кнопки ----------
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
    ):
        return None
    if atype == AnalysisType.current_period:
        created = (rec.get("created_at") or "")[:10]
        if created != today.isoformat():
            return None
    if not (data.get("report") or {}).get("full_report"):
        return None
    return data


_SECTION_EMOJI = [
    ("резюме", "📋"), ("ядро", "🧬"), ("сильн", "💪"), ("конфликт", "⚔️"),
    ("сценари", "🔁"), ("тень", "🌑"), ("слаб", "🌑"), ("отношени", "❤️"),
    ("любов", "❤️"), ("близост", "❤️"), ("притягива", "🧲"), ("работ", "💼"),
    ("деньг", "💰"), ("карьер", "📈"), ("реализаци", "📈"), ("ресурс", "🔋"),
    ("энерги", "🔋"), ("выгоран", "🔥"), ("риск", "⚠️"), ("игнориров", "🚨"),
    ("период", "🌗"), ("сейчас", "⏳"), ("год", "📅"), ("месяц", "📅"),
    ("недел", "🗓️"), ("7 дней", "✅"), ("30 дней", "🎯"), ("возможност", "✨"),
    ("укрепля", "🤝"), ("проявля", "🎭"), ("итог", "🏁"), ("расчётные", "🔢"),
    ("система пока", "🛰️"),
]


def _emoji(title: str) -> str:
    low = title.lower()
    for key, emo in _SECTION_EMOJI:
        if key in low:
            return emo
    return "▪️"


def parse_sections(full_report: str) -> list[tuple[str, str]]:
    """Режем отчёт на разделы по заголовкам уровня ## → [(заголовок, тело), …]."""
    parts = re.split(r"(?m)^## ", full_report)
    sections: list[tuple[str, str]] = []
    for part in parts[1:]:
        title = part.partition("\n")[0].strip()
        body = "## " + part.strip()
        sections.append((title, body))
    return sections


def _sections_keyboard(pid: str, sections: list[tuple[str, str]], atype_val: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for i, (title, _) in enumerate(sections):
        label = f"{_emoji(title)} {title}"
        if len(label) > 32:
            label = label[:31] + "…"
        row.append(InlineKeyboardButton(label, callback_data=f"s:{pid}:{i}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([
        InlineKeyboardButton("🗺 Карта", callback_data=f"img:{pid}"),
        InlineKeyboardButton("🎨 Обложка", callback_data=f"cov:{pid}"),
    ])
    rows.append([
        InlineKeyboardButton("🔬 Расчёт", callback_data=f"tech:{pid}"),
        InlineKeyboardButton("🔊 Озвучить", callback_data=f"tts:{pid}"),
    ])
    rows.append([InlineKeyboardButton("🔄 Сделать заново", callback_data=f"r:{atype_val}")])
    return InlineKeyboardMarkup(rows)


async def _present(msg, data: dict) -> None:
    """Показать разбор: короткий тизер + кнопки-разделы (отчёт не вываливаем стеной текста)."""
    report = data.get("report") or {}
    full = report.get("full_report") or ""
    pid = data.get("profile_id") or ""
    atype_val = (data.get("user_input") or {}).get("analysis_type") or "personality"
    sections = parse_sections(full)
    if not sections or not pid:
        await _send_long(msg, full)
        return
    teaser = (report.get("short_summary") or "Готово — разбор собран.").strip()
    await msg.reply_text(
        f"{CREDIBILITY_SHORT}\n\n{teaser}\n\n👇 Разбор разложен по разделам — жми, что открыть. "
        "«🔄 Сделать заново» — пересчитать с нуля.",
        reply_markup=_sections_keyboard(pid, sections, atype_val),
    )


async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка инлайн-кнопок: s:<pid>:<idx> — раздел; r:<atype> — пересчёт."""
    q = update.callback_query
    await q.answer()
    data = q.data or ""
    if data.startswith("s:"):
        _, pid, idx = data.split(":", 2)
        profile = await asyncio.to_thread(database.get_profile, pid)
        if not profile:
            await q.message.reply_text("Этот разбор уже устарел — сделай новый из меню.")
            return
        sections = parse_sections((profile.get("report") or {}).get("full_report") or "")
        i = int(idx)
        if 0 <= i < len(sections):
            atype_val = (profile.get("user_input") or {}).get("analysis_type") or "personality"
            kb = _sections_keyboard(pid, sections, atype_val)
            await _send_long(q.message, sections[i][1], reply_markup=kb)
    elif data.startswith("r:"):
        atype_val = data.split(":", 1)[1]
        if atype_val == AnalysisType.event.value:
            await q.message.reply_text("Новую дату/сделку запусти заново через «🤝 Сделка / Событие».")
            return
        label = _LABEL_BY_TYPE.get(atype_val)
        user = await asyncio.to_thread(database.get_user, q.from_user.id)
        if label and user and user.get("birth_date"):
            await _do_analysis(q.message, q.from_user.id, label, user, force=True)
        else:
            await q.message.reply_text("Нет данных для пересчёта — нажми /start.")
    elif data.startswith("tech:"):  # «Подробные методы» — раскрытие источников по запросу
        pid = data.split(":", 1)[1]
        profile = await asyncio.to_thread(database.get_profile, pid)
        tech = ((profile or {}).get("report") or {}).get("tech_methods") if profile else None
        if not tech:
            await q.message.reply_text("Технический слой для этого разбора недоступен.")
        else:
            await q.message.reply_text(
                "🔬 Подробные методы — на чём собран разбор (числовой, архетипический, "
                "временной и восточный слои). Обычно это спрятано за «языком Матрицы»."
            )
            await _send_long(q.message, tech)
    elif data.startswith("tts:"):  # озвучка разбора
        pid = data.split(":", 1)[1]
        await _send_voice(q.message, pid, q.from_user.id)
    elif data.startswith("img:"):  # детерминированная карта-визуал
        pid = data.split(":", 1)[1]
        await _send_chart(q.message, pid)
    elif data.startswith("cov:"):  # AI-обложка через Replicate
        pid = data.split(":", 1)[1]
        await _send_cover(q.message, pid)
    elif data.startswith("pd:"):  # удалить партнёра
        pid = data.split(":", 1)[1]
        await asyncio.to_thread(database.delete_partner, pid)
        await q.message.reply_text("Партнёр удалён.")
        await _send_partner_menu(q.message, q.from_user.id)
    elif data.startswith("p:") and data != "p:add":  # разбор с сохранённым партнёром
        pid = data.split(":", 1)[1]
        await _do_synastry(q.message, q.from_user.id, pid)


_LABEL_BY_TYPE = {atype.value: label for label, (atype, _req) in _ANALYSIS.items()}


# ---------- визуалы: детерминированная карта + AI-обложка ----------
async def _send_voice(msg, profile_id: str, telegram_id: int) -> None:
    """Озвучить разбор (Edge-TTS) и прислать аудио-файлом."""
    profile = await asyncio.to_thread(database.get_profile, profile_id)
    full = ((profile or {}).get("report") or {}).get("full_report") if profile else None
    if not full:
        await msg.reply_text("Этот разбор уже устарел — сделай новый из меню.")
        return
    note = await msg.reply_text("🔊 Озвучиваю разбор (несколько секунд)…")
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
    await msg.reply_audio(bio, title="Разбор", performer="Матрица",
                          caption="Озвучка разбора 🔊")
    try:
        await note.delete()
    except Exception:  # noqa: BLE001
        pass


async def _send_chart(msg, profile_id: str) -> None:
    """Нарисовать карту из посчитанных полей профиля и прислать картинкой."""
    profile = await asyncio.to_thread(database.get_profile, profile_id)
    if not profile:
        await msg.reply_text("Этот разбор уже устарел — сделай новый из меню.")
        return
    note = await msg.reply_text("🗺 Рисую карту по твоим расчётным полям…")
    try:
        png = await asyncio.to_thread(viz.render_chart, profile)
    except Exception as e:  # noqa: BLE001
        logging.exception("render_chart failed")
        await note.edit_text(f"Не получилось нарисовать карту: {type(e).__name__}")
        return
    bio = io.BytesIO(png)
    bio.name = "chart.png"
    await msg.reply_photo(bio, caption="Карта по твоим расчётным полям 🗺")
    try:
        await note.delete()
    except Exception:  # noqa: BLE001
        pass


async def _send_cover(msg, profile_id: str) -> None:
    """AI-обложка через Replicate. Кэшируем URL в профиле, чтобы не платить дважды."""
    profile = await asyncio.to_thread(database.get_profile, profile_id)
    if not profile:
        await msg.reply_text("Этот разбор уже устарел — сделай новый из меню.")
        return
    cached = profile.get("cover_url")
    if cached:
        await msg.reply_photo(cached, caption="Обложка по твоему расчётному профилю 🎨")
        return
    note = await msg.reply_text("🎨 Генерирую обложку (до минуты)…")
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
async def compat_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Кнопка «Совместимость»: список сохранённых партнёров + «Добавить»."""
    user = await asyncio.to_thread(database.get_user, update.effective_user.id)
    if not user or not user.get("birth_date"):
        await update.message.reply_text("Сначала сохрани свои данные — /start.")
        return
    await _send_partner_menu(update.message, update.effective_user.id)


async def _send_partner_menu(msg, telegram_id: int) -> None:
    partners = await asyncio.to_thread(database.list_partners, telegram_id)
    rows = []
    for p in partners:
        title = f"❤️ {p.get('name') or 'без имени'} · {p.get('birth_date') or ''}".strip()
        rows.append([InlineKeyboardButton(title[:40], callback_data=f"p:{p['partner_id']}")])
        rows.append([InlineKeyboardButton("🗑 удалить", callback_data=f"pd:{p['partner_id']}")])
    rows.append([InlineKeyboardButton("➕ Добавить партнёра", callback_data="p:add")])
    text = (
        "С кем проверяем совместимость?\nВыбери партнёра или добавь нового."
        if partners
        else "Партнёров пока нет. Добавь первого — нужны имя и дата (время и место — по желанию)."
    )
    await msg.reply_text(text, reply_markup=InlineKeyboardMarkup(rows))


async def _do_synastry(msg, telegram_id: int, partner_id: str) -> None:
    """Считает синастрию пользователя с сохранённым партнёром и шлёт разбор по разделам."""
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
    label = f"❤️ {user.get('name') or 'Ты'} × {partner.get('name') or 'партнёр'}"

    status = await msg.reply_text(f"{label}\n{stages[0]}")
    task = asyncio.create_task(asyncio.to_thread(build_synastry, user_req, partner_req))
    idx = 0
    while True:
        done, _ = await asyncio.wait({task}, timeout=2.4)
        if task in done:
            break
        idx += 1
        try:
            await status.edit_text(f"{label}\n{stages[idx % len(stages)]}")
        except Exception:  # noqa: BLE001
            pass

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
        await status.edit_text(f"{label}: готово ✅")
    except Exception:  # noqa: BLE001
        pass
    await _present(msg, data)
    await msg.reply_text("Выбери следующий разбор:", reply_markup=MAIN_MENU)


# ---------- событие / сделка (электив на дату) ----------
async def event_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    user = await asyncio.to_thread(database.get_user, update.effective_user.id)
    if not user or not user.get("birth_date"):
        await update.message.reply_text("Сначала сохрани свои данные — /start.")
        return ConversationHandler.END
    ctx.user_data.pop("ev_date", None)
    await update.message.reply_text(
        "📅 На какую дату смотрим? Пришли дату события/сделки в формате ГГГГ-ММ-ДД "
        "(например, 2026-07-15)."
    )
    return EV_DATE


async def event_date_save(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        ev = date.fromisoformat(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("Нужен формат ГГГГ-ММ-ДД, например 2026-07-15.")
        return EV_DATE
    ctx.user_data["ev_date"] = ev.isoformat()
    await update.message.reply_text(
        "Коротко опиши, что за событие: «подписание сделки по аренде», «запуск продукта», "
        "«переговоры о займе», «свадьба»… Чем конкретнее — тем точнее вердикт."
    )
    return EV_DESC


async def event_desc_save(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ev_iso = ctx.user_data.pop("ev_date", None)
    if not ev_iso:
        await update.message.reply_text("Сбой — начни заново через «🤝 Сделка / Событие».", reply_markup=MAIN_MENU)
        return ConversationHandler.END
    desc = update.message.text.strip()
    user = await asyncio.to_thread(database.get_user, update.effective_user.id)
    await _do_event(update.message, update.effective_user.id, user, date.fromisoformat(ev_iso), desc)
    return ConversationHandler.END


async def _do_event(msg, telegram_id: int, user: dict, ev_date: date, desc: str) -> None:
    """Считает разбор события на дату с живыми статусами и шлёт вердикт по разделам."""
    has_astro = bool(user.get("birth_time") and user.get("timezone"))
    stages = _STAGES_ASTRO if has_astro else _STAGES_BASIC
    label = f"🤝 {desc[:40] or 'Событие'} · {ev_date.isoformat()}"

    req = ProfileRequest(
        name=user.get("name", ""), gender=user.get("gender", "") or "",
        birth_date=date.fromisoformat(user["birth_date"]),
        birth_time=user.get("birth_time"), birth_place=user.get("birth_place"),
        lat=user.get("lat"), lon=user.get("lon"), timezone=user.get("timezone"),
        main_request=desc, analysis_type=AnalysisType.event,
    )

    status = await msg.reply_text(f"{label}\n{stages[0]}")
    task = asyncio.create_task(asyncio.to_thread(build_event, req, ev_date, desc))
    idx = 0
    while True:
        done, _ = await asyncio.wait({task}, timeout=2.4)
        if task in done:
            break
        idx += 1
        try:
            await status.edit_text(f"{label}\n{stages[idx % len(stages)]}")
        except Exception:  # noqa: BLE001
            pass

    try:
        profile = task.result()
    except Exception as e:  # noqa: BLE001
        logging.exception("build_event failed")
        await asyncio.to_thread(
            database.log_error, "exception", "build_event",
            f"{type(e).__name__}: {e}", {"event_date": ev_date.isoformat(), "desc": desc}, telegram_id,
        )
        await status.edit_text(f"Не получилось собрать разбор события: {type(e).__name__}: {e}")
        return

    data = profile.model_dump(mode="json")
    try:
        await asyncio.to_thread(database.save_profile, data, telegram_id)
    except Exception:  # noqa: BLE001
        logging.exception("save_profile (event) failed")
    try:
        await status.edit_text(f"{label}: готово ✅")
    except Exception:  # noqa: BLE001
        pass
    await _present(msg, data)
    await msg.reply_text("Выбери следующий разбор:", reply_markup=MAIN_MENU)


# ---------- добавление партнёра ----------
async def partner_add_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    ctx.user_data["np"] = {}
    await update.callback_query.message.reply_text("Имя партнёра:")
    return P_NAME


async def partner_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data.setdefault("np", {})["name"] = update.message.text.strip()
    await update.message.reply_text("Дата рождения партнёра (ГГГГ-ММ-ДД):")
    return P_DATE


async def partner_date(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        bd = date.fromisoformat(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("Нужен формат ГГГГ-ММ-ДД, например 1992-08-20.")
        return P_DATE
    ctx.user_data.setdefault("np", {})["birth_date"] = bd.isoformat()
    await update.message.reply_text(
        "Время рождения партнёра ЧЧ:ММ (для астро) — или «Пропустить».",
        reply_markup=SKIP_MENU,
    )
    return P_TIME


async def partner_time(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    txt = update.message.text.strip()
    try:
        hh, mm = (int(x) for x in txt.split(":")[:2])
        assert 0 <= hh <= 23 and 0 <= mm <= 59
    except (ValueError, IndexError, AssertionError):
        await update.message.reply_text("Нужен формат ЧЧ:ММ, например 09:15. Или «Пропустить».")
        return P_TIME
    ctx.user_data.setdefault("np", {})["birth_time"] = f"{hh:02d}:{mm:02d}"
    await update.message.reply_text(
        "Город рождения партнёра (например: Москва) — или «Пропустить».", reply_markup=SKIP_MENU
    )
    return P_PLACE


async def partner_place(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("📍 Ищу город на карте…")
    geo = await asyncio.to_thread(ephemeris.resolve_geo, update.message.text.strip())
    if geo is None:
        await update.message.reply_text(
            "Не нашёл город. Попробуй иначе или «Пропустить».", reply_markup=SKIP_MENU
        )
        return P_PLACE
    np = ctx.user_data.setdefault("np", {})
    np.update({
        "birth_place": update.message.text.strip(),
        "lat": geo["lat"], "lon": geo["lon"], "timezone": geo["timezone"],
    })
    return await _finish_partner(update, ctx)


async def partner_skip(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    return await _finish_partner(update, ctx)


async def _finish_partner(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Сохранить партнёра и сразу запустить разбор совместимости."""
    np = ctx.user_data.pop("np", {})
    if not np.get("name") or not np.get("birth_date"):
        await update.message.reply_text("Не хватило имени или даты — начни заново.", reply_markup=MAIN_MENU)
        return ConversationHandler.END
    partner_id = await asyncio.to_thread(database.add_partner, update.effective_user.id, np)
    await update.message.reply_text(f"Партнёр «{np['name']}» сохранён. Считаю совместимость…", reply_markup=MAIN_MENU)
    await _do_synastry(update.message, update.effective_user.id, partner_id)
    return ConversationHandler.END


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


async def edit_gender_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "Укажи пол одной буквой: м или ж.\n"
        "Он нужен для направления столпов удачи в Ба Цзы (大運)."
    )
    return EDIT_GENDER


async def edit_gender_save(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    g = update.message.text.strip().lower()[:1]
    if g not in ("м", "ж", "m", "f"):
        await update.message.reply_text("Нужна буква м или ж.")
        return EDIT_GENDER
    gender = "м" if g in ("м", "m") else "ж"
    await asyncio.to_thread(database.upsert_user, update.effective_user.id, {"gender": gender})
    await update.message.reply_text(f"Пол сохранён: {gender}.", reply_markup=MAIN_MENU)
    return ConversationHandler.END


async def back_to_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Меню:", reply_markup=MAIN_MENU)


async def cmd_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Команда /menu — вернуться к главному меню из любого места."""
    ctx.user_data.pop("pending_label", None)
    await update.message.reply_text("Главное меню:", reply_markup=MAIN_MENU)
    return ConversationHandler.END


async def cmd_data(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Команда /data — данные пользователя из любого места."""
    ctx.user_data.pop("pending_label", None)
    await show_data(update, ctx)
    return ConversationHandler.END


async def cmd_about(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Команда /about — на чём построена Матрица (метод, по-взрослому)."""
    ctx.user_data.pop("pending_label", None)
    await _send_long(update.message, METHOD_BASIS)
    return ConversationHandler.END


async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data.pop("pending_label", None)
    ctx.user_data.pop("np", None)
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


async def _send_long(msg, text: str, reply_markup=None) -> None:
    """Telegram лимит ~4096 символов — режем по абзацам.

    reply_markup вешаем на ПОСЛЕДНЕЕ сообщение — так меню разделов всегда оказывается
    внизу под открытым текстом, и не нужно скроллить вверх к старым кнопкам.
    """
    limit = 3800
    while text:
        if len(text) <= limit:
            await msg.reply_text(text, reply_markup=reply_markup)
            break
        cut = text.rfind("\n", 0, limit)
        cut = cut if cut > 0 else limit
        await msg.reply_text(text[:cut])
        text = text[cut:].lstrip("\n")


async def on_error(update: object, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Глобальный перехват ошибок бота: пишем в лог багов (видно в админке)."""
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

    skip = MessageHandler(filters.Regex(_SKIP_RE), onb_skip)
    common_fallbacks = [
        CommandHandler("cancel", cmd_cancel),
        CommandHandler("menu", cmd_menu),
        CommandHandler("data", cmd_data),
        CommandHandler("about", cmd_about),
    ]
    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", cmd_start),
            MessageHandler(filters.Regex(f"^({BTN_PERSON}|{BTN_PERIOD}|{BTN_WORK})$"), analysis_entry),
            MessageHandler(filters.Regex(f"^{BTN_EVENT}$"), event_start),
            MessageHandler(filters.Regex(f"^{BTN_EDIT_NAME}$"), edit_name_start),
            MessageHandler(filters.Regex(f"^{BTN_EDIT_DATE}$"), edit_date_start),
            MessageHandler(filters.Regex(f"^{BTN_EDIT_TIME}$"), edit_time_start),
            MessageHandler(filters.Regex(f"^{BTN_EDIT_PLACE}$"), edit_place_start),
            MessageHandler(filters.Regex(f"^{BTN_EDIT_GENDER}$"), edit_gender_start),
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
            EDIT_GENDER: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_gender_save)],
            EV_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, event_date_save)],
            EV_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, event_desc_save)],
        },
        fallbacks=common_fallbacks,
    )
    app.add_handler(conv)

    # Добавление партнёра — отдельная беседа, вход по инлайн-кнопке «Добавить».
    pskip = MessageHandler(filters.Regex(_SKIP_RE), partner_skip)
    partner_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(partner_add_start, pattern="^p:add$")],
        states={
            P_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, partner_name)],
            P_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, partner_date)],
            P_TIME: [pskip, MessageHandler(filters.TEXT & ~filters.COMMAND, partner_time)],
            P_PLACE: [pskip, MessageHandler(filters.TEXT & ~filters.COMMAND, partner_place)],
        },
        fallbacks=common_fallbacks,
    )
    app.add_handler(partner_conv)

    app.add_handler(CommandHandler("menu", cmd_menu))
    app.add_handler(CommandHandler("data", cmd_data))
    app.add_handler(CommandHandler("about", cmd_about))
    app.add_handler(MessageHandler(filters.Regex(f"^{BTN_COMPAT}$"), compat_menu))
    app.add_handler(MessageHandler(filters.Regex(f"^{BTN_DATA}$"), show_data))
    app.add_handler(MessageHandler(filters.Regex(f"^{BTN_BACK}$"), back_to_menu))
    app.add_handler(CallbackQueryHandler(on_callback))
    return app


def _token() -> str:
    from . import config

    return config.TELEGRAM_BOT_TOKEN
