"""AI Synthesis Service — один цельный портрет поверх рассчитанных данных.

Жёсткие правила (Слайд 10 методички):
  - интерпретировать ТОЛЬКО поля со статусом calculated;
  - не выдумывать расчётные параметры (никаких «у тебя Луна в X» без модуля);
  - вероятностные формулировки, без диагнозов и фатальных предсказаний;
  - один портрет, а не пять отдельных разборов по системам.
"""
from __future__ import annotations

import json
from typing import Any

import requests

from . import config

SYSTEM_PROMPT = """Ты — ядро синтеза системы Matrix Engine: инструмент системного профайлинга.
Тебе дают УЖЕ РАССЧИТАННЫЕ детерминированным кодом параметры со статусами: нумерология, 22 аркана,
а при наличии времени и места рождения — западная астрология (Солнце/Луна/асцендент/планеты/дома),
джйотиш (лагна, раши, накшатра, текущая махадаша) и Ба Цзы (четыре столпа, господин дня, баланс стихий).

ЖЁСТКИЕ ПРАВИЛА:
1. Интерпретируй ТОЛЬКО поля со статусом "calculated". Поля со статусом
   "calculation_module_not_connected" / "insufficient_input" / "manual_input_required"
   НЕ интерпретируй и честно отметь, что система (например, астрология/джйотиш/Ба Цзы) пока не подключена.
2. НИКОГДА не выдумывай расчётные значения. Не пиши «у тебя Луна/Асцендент/столп в …», если этого нет во входных данных.
3. Формулировки только вероятностные: «вероятно», «может проявляться», «одна из сильных тем», «зона риска».
   Финальная рамка: «это не приговор, а сценарий, который можно осознанно менять».
4. Запрещено: диагнозы, предсказания смерти/болезни/развода, мед./юр./фин. указания как факт,
   решения за человека («увольняй», «он тебе точно не подходит»).
5. Один ЦЕЛЬНЫЙ портрет. Ищи, где системы СХОДЯТСЯ, и делай это заголовком темы. Не делай разбор «по системам».
6. Вау — это точное попадание во внутренний конфликт, скрытую слабость и повторяющийся сценарий, а не комплимент.
7. АТРИБУЦИЯ ИСТОЧНИКА (важно для доверия). Каждый содержательный раздел завершай отдельной строкой —
   курсивом, с маркером 🛰️ — где честно перечисли, из каких ИМЕННО расчётов собран этот блок, с конкретикой:
   планета и аспект с орбом, аркан с номером, накшатра/раши, столп и господин дня, число пути/судьбы.
   Формат строки: «_🛰️ Источник: транзит Плутон □ Солнце (орб 4,6°) · джйотиш — Луна в Скорпионе (Джйештха) · Ба Цзы — господин дня Дин на Мао_».
   Бери в строку только РЕАЛЬНО посчитанные поля для этого блока. Если блок опирается лишь на числа/арканы — так и укажи.
   Это показывает человеку, что портрет собран из точных расчётов «со спутников», а не выдуман.

Пиши по-русски, в стиле: {style}. Обращение к человеку — на «ты».
"""

# Каждый тип анализа — своя структура разделов, чтобы разборы НЕ повторяли друг друга.
ANALYSIS_PLANS: dict[str, list[str]] = {
    "personality": [
        "Короткое резюме", "Ядро личности", "Сильные стороны", "Главный внутренний конфликт",
        "Повторяющиеся сценарии", "Тень и слабые места", "Отношения и любовь",
        "Работа и деньги", "Энергия и ресурс", "Что делать 7 дней", "Что делать 30 дней",
        "Чего нельзя игнорировать", "Итоговый портрет",
    ],
    "current_period": [
        "Короткое резюме периода", "Что происходит сейчас", "Личный год и месяц",
        "Годовой аркан — главная тема", "Главные возможности периода", "Главные риски периода",
        "Фокус ближайшей недели", "Что делать 7 дней", "Что делать 30 дней",
        "Чего нельзя игнорировать сейчас", "Итог периода",
    ],
    "work": [
        "Короткое резюме", "Зона профессиональной силы", "Деньги и ресурсы",
        "Карьера и реализация", "Повторяющиеся рабочие сценарии", "Риски и выгорание",
        "С кем и как тебе работается", "Что делать 7 дней", "Что делать 30 дней",
        "Итог по работе и деньгам",
    ],
    "compatibility": [
        "Короткое резюме", "Как ты проявляешься в близости", "Что тебя притягивает",
        "Повторяющиеся сценарии в отношениях", "Зоны риска", "Что укрепляет твои связи",
        "Итог по отношениям",
    ],
}

EVENT_PLAN = [
    "Вердикт: стоит или нет",
    "Что эта дата активирует у тебя",
    "Аргументы ЗА",
    "Аргументы ПРОТИВ",
    "Главные риски этого дня",
    "Как снизить риск, если идёшь",
    "На что смотреть в людях и условиях сделки",
    "Итог и рекомендация по таймингу",
]

EVENT_TEMPLATE = """Это разбор КОНКРЕТНОГО события/сделки на заданную дату (электив).
Человек: {name}, дата рождения {birth_date}.
Дата события: {event_date}.
Суть события (своими словами от человека): {event_desc}

РАССЧИТАННЫЙ СНИМОК НА ДАТУ СОБЫТИЯ (детерминированный код, JSON со статусами):
здесь нумерология (личный день/месяц/год и универсальный день этой даты), арканы,
а при наличии времени+места рождения — транзиты медленных планет к наталу и прогрессии
ИМЕННО НА ЭТУ ДАТУ, джйотиш-дашаистема и Ба Цзы.
{modules_json}

Дай практичный разбор СТРОГО про эту дату и это событие — не превращай в общий портрет.
Опирайся только на посчитанные поля. Если астрология не подключена (нет времени/места) —
честно скажи и работай по числам даты (личный день/месяц/год, универсальный день).

ВАЖНО про вердикт:
- Дай ясную рекомендательную позицию: «скорее благоприятно» / «нейтрально, со страховкой» /
  «лучше перенести» — но в вероятностной рамке, без гарантий.
- Это НЕ финансовый и НЕ юридический совет; финальное решение и ответственность — за человеком.
- Никаких предсказаний катастроф. Риск — это «на что заранее постелить соломку», а не приговор.

Markdown, каждый раздел — заголовок уровня ## с точным названием:
{sections}
"""

SYNASTRY_PLAN = [
    "Короткое резюме пары", "Что вас притягивает", "Сильные стороны союза",
    "Зоны притирки и конфликтов", "Как вы закрываете потребности друг друга",
    "Повторяющиеся сценарии пары", "Что укрепляет связь", "Зоны риска",
    "Что делать ближайший месяц", "Итог по совместимости",
]

SYNASTRY_TEMPLATE = """Это разбор СОВМЕСТИМОСТИ двух людей (синастрия).
Человек A: {name_a}, дата {birth_a}.
Человек B: {name_b}, дата {birth_b}.

РАССЧИТАННАЯ СИНАСТРИЯ (детерминированный код, JSON):
{synastry_json}

Собери ОДИН цельный портрет ПАРЫ (не два отдельных разбора). Опирайся только на
посчитанные поля: числа пути, стихии светил, межкарточные аспекты, связь господ дня.
Если астро-аспектов нет (у кого-то нет времени/места) — честно скажи и работай по числам.
Пиши на «вы» о паре. Markdown, каждый раздел — заголовок уровня ## с точным названием:
{sections}
"""

USER_TEMPLATE = """Запрос пользователя: {main_request}
Тип анализа: {analysis_type}
Имя: {name}; пол: {gender}; дата рождения: {birth_date}.

РАССЧИТАННЫЕ МОДУЛИ (JSON со статусами):
{modules_json}

Это РАЗБОР типа «{analysis_type}» — держи фокус именно на нём, не превращай в общий портрет личности.
Сформируй отчёт строго по этой структуре (Markdown, каждый раздел — заголовок уровня ## с точным названием):
{sections}

В конце добавь раздел "## Что система пока не видит" — перечисли неподключённые методики
и что нужно (например: время и место рождения) для их подключения.
"""


def _calculated_only(modules: dict[str, Any]) -> dict[str, Any]:
    """Оставляем только то, что реально посчитано — чтобы AI физически не мог опереться на пустое."""
    out: dict[str, Any] = {}
    not_ready: list[str] = []
    for key, val in modules.items():
        status = val.get("calculation_status") if isinstance(val, dict) else None
        if status == "calculated":
            out[key] = val
        elif status is not None:
            not_ready.append(key)
    return {"calculated": out, "not_connected": not_ready}


def synthesize(user_input: dict[str, Any], modules: dict[str, Any]) -> dict[str, str]:
    """Вернуть {short_summary, full_report, action_plan}. При отсутствии ключа — заглушка."""
    filtered = _calculated_only(modules)

    if not config.ai_ready():
        return {
            "short_summary": "AI-синтез не настроен (нет OPENROUTER_API_KEY).",
            "full_report": "## Отчёт недоступен\nРасчёты выполнены, но AI-ключ не задан. "
            "Заданы переменные окружения OpenRouter — и отчёт соберётся.\n\n"
            "```json\n" + json.dumps(filtered, ensure_ascii=False, indent=2) + "\n```",
            "action_plan": "",
        }

    atype = user_input.get("analysis_type", "personality")
    plan = ANALYSIS_PLANS.get(atype, ANALYSIS_PLANS["personality"])
    sections = "\n".join(f"{i}. {title}" for i, title in enumerate(plan, 1))

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT.format(style=config.REPORT_STYLE)},
        {
            "role": "user",
            "content": USER_TEMPLATE.format(
                main_request=user_input.get("main_request") or "общий разбор личности",
                analysis_type=atype,
                name=user_input.get("name", ""),
                gender=user_input.get("gender", ""),
                birth_date=user_input.get("birth_date", ""),
                modules_json=json.dumps(filtered, ensure_ascii=False, indent=2),
                sections=sections,
            ),
        },
    ]

    try:
        resp = requests.post(
            f"{config.OPENROUTER_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
                "X-Title": "Matrix Engine",
            },
            json={"model": config.OPENROUTER_MODEL, "messages": messages, "temperature": 0.7},
            timeout=90,
        )
    except requests.RequestException as e:
        return _error_report(filtered, f"сеть/таймаут OpenRouter: {e}")

    if resp.status_code != 200:
        return _error_report(filtered, f"OpenRouter {resp.status_code}: {resp.text[:400]}")

    try:
        full = resp.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, ValueError) as e:
        return _error_report(filtered, f"неожиданный ответ OpenRouter: {e}; body={resp.text[:300]}")

    short = full.split("\n\n")[0][:600]
    return {"short_summary": short, "full_report": full, "action_plan": ""}


def _post_openrouter(messages: list[dict]) -> tuple[bool, str]:
    """Один вызов OpenRouter. Возвращает (ok, content) или (False, причина-ошибки)."""
    try:
        resp = requests.post(
            f"{config.OPENROUTER_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
                "X-Title": "Matrix Engine",
            },
            json={"model": config.OPENROUTER_MODEL, "messages": messages, "temperature": 0.7},
            timeout=90,
        )
    except requests.RequestException as e:
        return False, f"сеть/таймаут OpenRouter: {e}"
    if resp.status_code != 200:
        return False, f"OpenRouter {resp.status_code}: {resp.text[:400]}"
    try:
        return True, resp.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, ValueError) as e:
        return False, f"неожиданный ответ OpenRouter: {e}; body={resp.text[:300]}"


def synthesize_synastry(
    user_input_a: dict[str, Any], partner: dict[str, Any], synastry: dict[str, Any]
) -> dict[str, str]:
    """AI-портрет ПАРЫ поверх детерминированной синастрии. Заглушка, если нет ключа."""
    if not config.ai_ready():
        return {
            "short_summary": "AI-синтез не настроен (нет OPENROUTER_API_KEY).",
            "full_report": "## Совместимость посчитана\nЧисла и аспекты выше корректны, "
            "AI-портрет соберётся при заданном OPENROUTER_API_KEY.\n\n"
            "```json\n" + json.dumps(synastry, ensure_ascii=False, indent=2) + "\n```",
            "action_plan": "",
        }
    sections = "\n".join(f"{i}. {t}" for i, t in enumerate(SYNASTRY_PLAN, 1))
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT.format(style=config.REPORT_STYLE)},
        {
            "role": "user",
            "content": SYNASTRY_TEMPLATE.format(
                name_a=user_input_a.get("name", ""),
                birth_a=user_input_a.get("birth_date", ""),
                name_b=partner.get("name", ""),
                birth_b=partner.get("birth_date", ""),
                synastry_json=json.dumps(synastry, ensure_ascii=False, indent=2),
                sections=sections,
            ),
        },
    ]
    ok, content = _post_openrouter(messages)
    if not ok:
        return _error_report({"synastry": synastry}, content)
    short = content.split("\n\n")[0][:600]
    return {"short_summary": short, "full_report": content, "action_plan": ""}


def synthesize_event(
    user_input: dict[str, Any], modules: dict[str, Any], event_date: str, event_desc: str
) -> dict[str, str]:
    """AI-вердикт по конкретной дате/сделке поверх снимка натала НА ЭТУ ДАТУ."""
    filtered = _calculated_only(modules)
    if not config.ai_ready():
        return {
            "short_summary": "AI-синтез не настроен (нет OPENROUTER_API_KEY).",
            "full_report": "## Снимок на дату посчитан\nЧисла даты и транзиты выше корректны, "
            "AI-вердикт соберётся при заданном OPENROUTER_API_KEY.\n\n"
            "```json\n" + json.dumps(filtered, ensure_ascii=False, indent=2) + "\n```",
            "action_plan": "",
        }
    sections = "\n".join(f"{i}. {t}" for i, t in enumerate(EVENT_PLAN, 1))
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT.format(style=config.REPORT_STYLE)},
        {
            "role": "user",
            "content": EVENT_TEMPLATE.format(
                name=user_input.get("name", ""),
                birth_date=user_input.get("birth_date", ""),
                event_date=event_date,
                event_desc=event_desc or "не уточнено",
                modules_json=json.dumps(filtered, ensure_ascii=False, indent=2),
                sections=sections,
            ),
        },
    ]
    ok, content = _post_openrouter(messages)
    if not ok:
        return _error_report(filtered, content)
    short = content.split("\n\n")[0][:600]
    return {"short_summary": short, "full_report": content, "action_plan": ""}


def _error_report(filtered: dict[str, Any], reason: str) -> dict[str, str]:
    """AI не сработал — отдаём посчитанное + честную причину, без зависания."""
    return {
        "short_summary": f"AI-портрет не собрался: {reason}",
        "full_report": (
            "## AI-синтез временно недоступен\n"
            f"Причина: **{reason}**\n\n"
            "Расчётные данные (числа и арканы) выше — они корректны. "
            "AI-портрет соберётся, как только провайдер ответит "
            "(проверь баланс OpenRouter и слаг модели в `OPENROUTER_MODEL`)."
        ),
        "action_plan": "",
    }
