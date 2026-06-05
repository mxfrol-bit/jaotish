"""AI Synthesis Service — один цельный портрет поверх рассчитанных данных.

Жёсткие правила (Слайд 10 методички):
  - интерпретировать ТОЛЬКО поля со статусом calculated;
  - не выдумывать расчётные параметры (никаких «у тебя Луна в X» без модуля);
  - вероятностные формулировки, без диагнозов и фатальных предсказаний;
  - один портрет, а не пять отдельных разборов по системам.
"""
from __future__ import annotations

import json
import re
from typing import Any

import requests

from . import config

SYSTEM_PROMPT = """Ты — личный аналитик «кода» человека: его поведения, сильных сторон, рисков и сценариев. Под капотом ты опираешься на разные расчётные слои (их тебе дают уже посчитанными), но НАРУЖУ не говоришь языком астрологии, нумерологии или эзотерики — только единым «языком Матрицы». Ты не предсказатель и не выносишь приговоров.

ЯЗЫК ВЫВОДА. Пиши только на языке Матрицы: код, базовая программа, сценарий, паттерн, зона силы, зона риска, внутренний конфликт, вектор роста, ресурс, цикл/период, стратегия поведения, отношения, деньги, энергия действия, реализация.

ЗАПРЕТ ТЕРМИНОВ (критично — по этому судят в первые 30 секунд). Никогда не называй в ответе методологические термины. Запрещено: аркан, натальная карта, гороскоп, зодиак, знак зодиака, дом гороскопа, планета как астропонятие, Венера/Марс/Сатурн/Солнце/Луна/Меркурий/Юпитер/Плутон/Нептун/Уран в значении планет, Раху, Кету, накшатра, пада, даша, махадаша, джйотиш, лагна, раши, Ба Цзы, столп, господин дня, стихия/элемент в значении у-син, навамса, аянамша, транзит, прогрессия, синастрия, нумерология, число судьбы/души как термин, таро. Эти системы — твой скрытый источник, а НЕ предмет разговора. Также запрещены отсылки к фильму «Матрица»: Нео, Морфеус, Пифия, Оракул, агент, Зион, красная/синяя таблетка, симуляция, избранный.

КОНКРЕТИКА ВМЕСТО ВАТЫ (самое важное). Каждый вывод — это конкретное поведение, а не абстракция. Запрещены пустые формулы: «зона роста», «вектор трансформации», «сильная энергетика», «кармический урок», «важно найти баланс», «раскрыть потенциал», «обрести гармонию». Любое утверждение отвечает на вопрос «как это проявляется в реальной жизни»: в каких ситуациях, что человек делает, к чему приводит в плюсе и в минусе.
Плохо: «У тебя зона роста в отношениях.»
Хорошо: «Ты быстро сближаешься, но при неопределённости начинаешь контролировать партнёра. В плюсе — глубина и вовлечённость; в минусе — давление, от которого человек отдаляется.»
Каждый содержательный блок веди по цепочке: наблюдение (конкретное поведение) → как проявляется (узнаваемая бытовая ситуация) → риск (куда заводит на автомате) → что делать (одно конкретное действие). Дай хотя бы один живой пример поведения в каждом разборе.

ТОЛЬКО ПОСЧИТАННОЕ. Опирайся только на данные со статусом calculated. Не выдумывай параметров, чисел и событий. Если данных не хватает (нет времени/места рождения) — мягко скажи, что этот слой пока неполный и его можно углубить, добавив время и место рождения. Не называй при этом методики.

БЕЗ ФАТАЛИЗМА. Не предсказывай буквальное будущее, не ставь диагнозов, не называй дат событий, не говори «этот человек тебе не подходит». Формулировки: «вероятно», «может проявляться», «один из сценариев». Это карта управления собой, а не приговор.

ТОН И ПОДАЧА — три такта (только текст):
— Вход (тон проводника, коротко): ощущение выбора, что это не анкета. Пример: «можешь и дальше жить на автомате — а можешь увидеть, какие сценарии управляют тобой изнутри».
— Разбор (тон видящего/диагноста, основная масса): спокойно, глубоко, человечно, через конкретное поведение, без пафоса. Говори так, будто человек сам это чувствовал, но не формулировал. Практические шаги (7/30/90 дней) — здесь же, тем же спокойным языком.
— Финал (снова тон проводника, коротко): «теперь ты видишь один из своих сценариев — вопрос, готов ли им управлять».
Мягкая подача не отменяет конкретику: тон делает конкретное наблюдение человечным, а не расплывчатое — загадочным.

РАСКРЫТИЕ ИСТОЧНИКОВ. По умолчанию методы не упоминаются. Если человек сам спросит, на чём основан разбор — обобщённо: «вывод собран из нескольких слоёв анализа — архетипического, числового, временного, восточного и психологического». Конкретные названия систем — только в отдельном техническом разделе по явному запросу.

ЦЕЛЬНОСТЬ. Один цельный портрет, а не разбор «по системам». Ищи, где слои сходятся, и делай это темой.

Пиши по-русски, обращение на «ты», в стиле: {style}.
"""

# Разделы — от ПОЛЬЗЫ и от поведения, а не от метода. Без жаргона в названиях.
ANALYSIS_PLANS: dict[str, list[str]] = {
    "personality": [
        "Первый слой кода", "Твоё ядро силы", "Где ты сам себе мешаешь",
        "Главный внутренний конфликт", "Повторяющийся сценарий", "Деньги и реализация",
        "Отношения и близость", "Что делать: 7 / 30 / 90 дней",
        "Хороший сценарий против плохого", "Итог",
    ],
    "current_period": [
        "С чего начать", "Главная тема периода", "Что усиливается сейчас",
        "Где легко слить силу", "Окно возможностей", "Что делать ближайшие 30 дней",
        "Хороший сценарий против плохого", "Итог периода",
    ],
    "work": [
        "С чего начать", "Как ты зарабатываешь", "Как теряешь и саботируешь",
        "Твой денежный риск", "Где твоя профессиональная сила", "Что усиливать",
        "Что делать: 7 / 30 / 90 дней", "Итог по деньгам и реализации",
    ],
    "compatibility": [
        "С чего начать", "Как ты сближаешься", "Что тебя триггерит",
        "Твой паттерн в конфликте", "Где ты разрушаешь связь",
        "Что даёт партнёру безопасность", "Итог",
    ],
}

EVENT_PLAN = [
    "Вердикт: стоит или нет",
    "Что эта дата включает у тебя",
    "Аргументы ЗА",
    "Аргументы ПРОТИВ",
    "Главные риски этого дня",
    "Как снизить риск, если идёшь",
    "На что смотреть в людях и условиях",
    "Итог и рекомендация по таймингу",
]

EVENT_TEMPLATE = """Это разбор КОНКРЕТНОГО события/сделки на заданную дату.
Человек: {name}, дата рождения {birth_date}.
Дата события: {event_date}.
Суть события (своими словами от человека): {event_desc}

РАССЧИТАННЫЙ СНИМОК НА ЭТУ ДАТУ (детерминированный код, JSON со статусами) — это твой
скрытый источник: числовые слои этой даты, архетипы, а при наличии времени+места рождения —
временной и восточный слои именно на эту дату. НАЗВАНИЯ ЭТИХ СЛОЁВ НАРУЖУ НЕ УПОТРЕБЛЯЙ.
{modules_json}

Дай практичный разбор СТРОГО про эту дату и это событие — не общий портрет.
Опирайся только на посчитанные поля. Если глубинный слой неполон (нет времени/места) —
мягко скажи и работай по числовому слою даты, не называя методик.

ВАЖНО про вердикт:
- Дай ясную позицию: «скорее благоприятно» / «нейтрально, со страховкой» / «лучше перенести» —
  в вероятностной рамке, без гарантий.
- Это НЕ финансовый и НЕ юридический совет; финальное решение и ответственность — за человеком.
- Никаких предсказаний катастроф. Риск — это «на что заранее постелить соломку», а не приговор.

Подача по тактам: вход (проводник, коротко) → разбор и шаги (спокойно, через конкретное
поведение) → финал (проводник, коротко). Markdown, каждый раздел — заголовок уровня ## с точным названием:
{sections}
"""

SYNASTRY_PLAN = [
    "С чего начать", "Где вы усиливаете друг друга", "Где вы триггерите друг друга",
    "Сценарий вашего конфликта", "Сценарий восстановления", "Повторяющийся сценарий пары",
    "Как общаться и чего не делать", "Итог — без приговора «вместе или нет»",
]

SYNASTRY_TEMPLATE = """Это разбор РЕЗОНАНСА двух кодов (динамика пары, а не «совместимость по знаку»).
Человек A: {name_a}, дата {birth_a}.
Человек B: {name_b}, дата {birth_b}.

РАССЧИТАННЫЙ РЕЗОНАНС (детерминированный код, JSON) — скрытый источник: числовые слои обоих,
взаимодействие энергий, точки притяжения и трения. НАЗВАНИЯ МЕТОДИК НАРУЖУ НЕ УПОТРЕБЛЯЙ.
{synastry_json}

Собери ОДИН цельный портрет ПАРЫ (не два отдельных разбора). Только посчитанные поля.
Если глубинный слой неполон (у кого-то нет времени/места) — мягко скажи и работай по числовому слою.
ЗАПРЕЩЕНО «вам не стоит быть вместе»: только «в этой паре сильно/триггерно то-то, управляется так-то».
Пиши на «вы» о паре, подача по тактам (вход-проводник → разбор → финал-проводник).
Markdown, каждый раздел — заголовок уровня ## с точным названием:
{sections}
"""

USER_TEMPLATE = """Запрос пользователя: {main_request}
Тип разбора: {analysis_type}
Имя: {name}; пол: {gender}; дата рождения: {birth_date}.

РАССЧИТАННЫЕ СЛОИ (JSON со статусами) — это твой скрытый источник. НАЗВАНИЯ СЛОЁВ/МЕТОДИК
НАРУЖУ НЕ УПОТРЕБЛЯЙ, переводи их выводы на язык Матрицы.
{modules_json}

Это разбор типа «{analysis_type}» — держи фокус именно на нём.
Подача по тактам: вход (проводник, коротко) → разбор и шаги (спокойно, через конкретное
поведение, цепочка наблюдение→проявление→риск→действие) → финал (проводник, коротко).
Сформируй отчёт строго по этой структуре (Markdown, каждый раздел — заголовок уровня ## с точным названием):
{sections}

Если для более глубокого слоя не хватает времени/места рождения — в самом конце добавь короткий
раздел "## Как углубить разбор" и мягко скажи, что добавив точное время и город рождения,
можно раскрыть более тонкий слой. Без названий методик.
"""


def _teaser(text: str) -> str:
    """Короткий тизер: первый НЕ-заголовочный абзац (а не строка «## …»)."""
    for para in (text or "").split("\n\n"):
        p = para.strip()
        if p and not p.startswith("#") and p != "---":
            return p[:600]
    return (text or "").strip()[:600]


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

    ok, full = _generate(messages, where=f"synthesize:{atype}")
    if not ok:
        return _error_report(filtered, full)

    short = _teaser(full)
    return {"short_summary": short, "full_report": full, "action_plan": ""}


def _post_openrouter(messages: list[dict]) -> tuple[bool, str]:
    """Вызов OpenRouter со СТРИМОМ. Возвращает (ok, content) или (False, причина).

    Стрим важен для длинных отчётов: при медленной генерации в нестримовое тело
    прилетают только keep-alive байты, и resp.json() видит пустоту
    ('Expecting value: line N column 1'). Стрим собирает текст по дельтам — это надёжно.
    """
    try:
        resp = requests.post(
            f"{config.OPENROUTER_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
                "X-Title": "Matrix Engine",
            },
            json={
                "model": config.OPENROUTER_MODEL,
                "messages": messages,
                "temperature": 0.7,
                "stream": True,
            },
            timeout=180,
            stream=True,
        )
    except requests.RequestException as e:
        return False, f"сеть/таймаут OpenRouter: {e}"
    if resp.status_code != 200:
        return False, f"OpenRouter {resp.status_code}: {resp.text[:400]}"

    parts: list[str] = []
    err: str | None = None
    try:
        for raw in resp.iter_lines(decode_unicode=True):
            if not raw or not raw.startswith("data:"):
                continue  # пустые строки и ': OPENROUTER PROCESSING' пропускаем
            payload = raw[5:].strip()
            if payload == "[DONE]":
                break
            try:
                obj = json.loads(payload)
            except ValueError:
                continue
            if obj.get("error"):
                err = str(obj["error"])[:300]
                break
            delta = (obj.get("choices") or [{}])[0].get("delta") or {}
            piece = delta.get("content")
            if piece:
                parts.append(piece)
    except requests.RequestException as e:
        if not parts:
            return False, f"обрыв стрима OpenRouter: {e}"
    finally:
        resp.close()

    content = "".join(parts).strip()
    if content:
        return True, content
    return False, err or "пустой ответ OpenRouter (стрим без контента)"


# --- Валидатор языка: ловит протечки методологического жаргона и слов фильма ---
# Hard — однозначный жаргон: протёк → перегенерация. По границам слова + морфология.
_HARD_RE = re.compile(
    "|".join([
        r"аркан\w*", r"\bраху\b", r"\bкету\b", r"накшатр\w*", r"\bпада\b",
        r"\bдаш[аи]\b", r"махадаш\w*", r"джйотиш\w*", r"ба\s*цзы", r"навамс\w*",
        r"аянамш\w*", r"натальн\w*\s+карт\w*", r"синастри\w*", r"\bтаро\b",
        r"столп\w*", r"\bлагн\w+", r"\bраши\b", r"господин\w*\s+дня",
        # маркеры фильма
        r"\bнео\b", r"морфеус\w*", r"пифи[яи]\w*", r"\bоракул\w*", r"\bзион\b",
        r"красн\w*\s+таблетк\w*", r"син\w*\s+таблетк\w*", r"симуляц\w*", r"избранн\w+",
    ]),
    re.IGNORECASE,
)
# Soft — контекстно-зависимое: логируем для ревью, НЕ перегенерируем (риск сломать текст).
_SOFT_RE = re.compile(
    r"карм\w*|чакр\w*|гороскоп\w*|зодиак\w*|\bстихи[яюйеёи]\w*|транзит\w*|прогресс\w+",
    re.IGNORECASE,
)


def _lang_leaks(text: str) -> tuple[list[str], list[str]]:
    hard = sorted({m.group(0).lower() for m in _HARD_RE.finditer(text)})
    soft = sorted({m.group(0).lower() for m in _SOFT_RE.finditer(text)})
    return hard, soft


def _generate(messages: list[dict], where: str = "", telegram_id: int | None = None) -> tuple[bool, str]:
    """Вызов модели + валидатор языка Матрицы.

    При протечке hard-жаргона перегенерируем (до 2 раз) со строгим напоминанием; soft —
    только логируем. Все протечки пишем в me_errors (видно в админке). После ретраев
    отдаём лучший имеющийся текст (безопасный фолбэк), чтобы не зависнуть.
    """
    from . import database

    base = list(messages)
    msgs = base
    last = ""
    for attempt in range(3):
        ok, content = _post_openrouter(msgs)
        if not ok:
            database.log_error("ai_failure", where, content, {"attempt": attempt}, telegram_id)
            return False, content
        last = content
        hard, soft = _lang_leaks(content)
        if soft:
            database.log_error(
                "lang_leak", where, "soft-термины: " + ", ".join(soft),
                {"soft": soft, "attempt": attempt}, telegram_id,
            )
        if not hard:
            return True, content
        database.log_error(
            "lang_leak", where, f"hard-протечка (попытка {attempt + 1}): " + ", ".join(hard),
            {"hard": hard, "attempt": attempt}, telegram_id,
        )
        msgs = base + [
            {"role": "assistant", "content": content},
            {
                "role": "user",
                "content": (
                    "В ответе протекли запрещённые термины: " + ", ".join(hard) + ". "
                    "Перепиши ТОТ ЖЕ разбор полностью на языке Матрицы, без единого из этих слов, "
                    "сохранив всю конкретику, примеры и структуру разделов."
                ),
            },
        ]
    return True, last  # уже залогировано; отдаём что есть, чтобы пользователь не остался без разбора


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
    ok, content = _generate(messages, where="synastry")
    if not ok:
        return _error_report({"synastry": synastry}, content)
    short = _teaser(content)
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
    ok, content = _generate(messages, where="event")
    if not ok:
        return _error_report(filtered, content)
    short = _teaser(content)
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
