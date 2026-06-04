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

Пиши по-русски, в стиле: {style}. Обращение к человеку — на «ты».
"""

USER_TEMPLATE = """Запрос пользователя: {main_request}
Тип анализа: {analysis_type}
Имя: {name}; пол: {gender}; дата рождения: {birth_date}.

РАССЧИТАННЫЕ МОДУЛИ (JSON со статусами):
{modules_json}

Сформируй отчёт строго по этой структуре (Markdown, заголовки уровня ##):
1. Короткое резюме (до 10 строк)
2. Ядро личности
3. Сильные стороны
4. Главный внутренний конфликт
5. Повторяющиеся сценарии
6. Тень / слабые места
7. Отношения и любовь
8. Работа и деньги
9. Энергия и ресурс
10. Текущий период
11. Что делать 7 дней
12. Что делать 30 дней
13. Что нельзя игнорировать
14. Итоговый портрет в 5–10 фразах

В конце — блок "## Что система пока не видит" с перечислением неподключённых методик
и тем, что нужно (например: время и место рождения) для их подключения.
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

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT.format(style=config.REPORT_STYLE)},
        {
            "role": "user",
            "content": USER_TEMPLATE.format(
                main_request=user_input.get("main_request") or "общий разбор личности",
                analysis_type=user_input.get("analysis_type", "personality"),
                name=user_input.get("name", ""),
                gender=user_input.get("gender", ""),
                birth_date=user_input.get("birth_date", ""),
                modules_json=json.dumps(filtered, ensure_ascii=False, indent=2),
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
