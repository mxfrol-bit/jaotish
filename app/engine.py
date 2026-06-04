"""Сборка профиля end-to-end: расчёт → AI-синтез → итоговый отчёт."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any

from . import config, synthesis
from .calc import matrix_calc
from .models import Profile, ProfileRequest, UserInput

_STATUS_RU = {
    "calculated": "✅ посчитано",
    "calculation_module_not_connected": "⛔ модуль не подключён",
    "insufficient_input": "⚠️ не хватает данных (время/место рождения)",
    "manual_input_required": "✋ нужен ручной ввод",
}


def _calc_header(modules: dict[str, Any]) -> str:
    """Честная сводка статусов + посчитанные числа и арканы (без AI)."""
    num = modules.get("numerology", {})
    arc = modules.get("arcana_22", {})
    lines = ["## Расчётные данные (детерминированы кодом)", ""]

    if num.get("calculation_status") == "calculated":
        lines += [
            "**Нумерология (пифагорейская):**",
            f"- Число жизненного пути: **{num.get('life_path')}**",
            f"- Число дня рождения: **{num.get('birthday_number')}**",
            f"- Число души: **{num.get('soul_number')}**",
            f"- Число судьбы: **{num.get('destiny_number')}**",
            f"- Число личности: **{num.get('personality_number')}**",
            f"- Личный год: **{num.get('personal_year')}**, личный месяц: **{num.get('personal_month')}**",
            "",
        ]

    if arc.get("calculation_status") == "calculated":
        center = arc.get("core_arcana", {})
        yr = arc.get("year_arcana", {})
        cross = arc.get("cross", {})
        lines += [
            "**22 аркана (центральный крест):**",
            f"- Ядро (центр): **{center.get('value')} · {center.get('name')}** — {center.get('keys')}",
            f"- Годовой аркан: **{yr.get('value')} · {yr.get('name')}**",
            "- Крест: "
            + ", ".join(
                f"{k}={cross[k]['value']} {cross[k]['name']}"
                for k in ("a_day", "b_month", "c_year", "d_sum")
                if k in cross
            ),
            "",
        ]

    west = modules.get("western_astrology", {})
    if west.get("calculation_status") == "calculated":
        lines += [
            "**Западная астрология (тропический зодиак):**",
            f"- Солнце: **{west['sun']['sign']}**, Луна: **{west['moon']['sign']}**, "
            f"Асцендент: **{west['ascendant']['sign']}**",
            f"- Доминирующая стихия: **{west['dominant_element']}**",
            "",
        ]

    jyo = modules.get("jyotish", {})
    if jyo.get("calculation_status") == "calculated":
        nk = jyo["nakshatra"]
        ds = jyo["current_dasha"]
        lines += [
            "**Джйотиш (сидерический зодиак, айянамша Лахири):**",
            f"- Лагна: **{jyo['lagna']['sign']}**, Луна (раши): **{jyo['moon_rashi']}**",
            f"- Накшатра Луны: **{nk['name']}** (пада {nk['pada']})",
            f"- Текущая махадаша: **{ds['lord']}** (осталось ~{ds['remaining_years']} лет)",
            "",
        ]

    baz = modules.get("bazi", {})
    if baz.get("calculation_status") == "calculated":
        p = baz["pillars"]
        dm = baz["day_master"]
        lines += [
            "**Ба Цзы (четыре столпа):**",
            "- Столпы: "
            + ", ".join(f"{k}={p[k]['stem']}-{p[k]['branch']}" for k in ("year", "month", "day", "hour")),
            f"- Господин дня: **{dm['stem']}** ({dm['element']}, {dm['polarity']}), "
            f"доминирующая стихия: **{baz['dominant_element']}**",
            "",
        ]

    lines += ["**Статусы методик:**"]
    for key in ("numerology", "arcana_22", "western_astrology", "jyotish", "bazi"):
        st = modules.get(key, {}).get("calculation_status")
        if st:
            lines.append(f"- {key}: {_STATUS_RU.get(st, st)}")
    lines.append("")
    return "\n".join(lines)


def _resolve_geo(req: ProfileRequest) -> dict | None:
    """Координаты+таймзона: берём предрассчитанные из запроса, иначе геокодим город."""
    if req.lat is not None and req.lon is not None and req.timezone:
        return {"lat": req.lat, "lon": req.lon, "timezone": req.timezone}
    if req.birth_place:
        from .calc import ephemeris

        return ephemeris.resolve_geo(req.birth_place)
    return None


def build_profile(req: ProfileRequest, today: date | None = None) -> Profile:
    today = today or date.today()
    birth = req.birth_date

    geo = _resolve_geo(req)
    modules = matrix_calc.compute_all(birth, req.name, today, req.birth_time, geo)

    user_input = UserInput(
        name=req.name,
        gender=req.gender,
        birth_date=birth,
        birth_time=req.birth_time,
        birth_place=req.birth_place,
        main_request=req.main_request,
        analysis_type=req.analysis_type,
    )

    ai = synthesis.synthesize(user_input.model_dump(mode="json"), modules)

    header = _calc_header(modules)
    full_report = header + "\n---\n\n" + ai["full_report"]

    return Profile(
        profile_id=str(uuid.uuid4()),
        user_input=user_input,
        geo=geo or {"lat": None, "lon": None, "timezone": ""},
        calculation_modules=modules,
        synthesis={"engine": "ai", "model": config.OPENROUTER_MODEL if config.ai_ready() else None},
        report={
            "short_summary": ai["short_summary"],
            "full_report": full_report,
            "action_plan": ai.get("action_plan", ""),
        },
        meta={
            "created_at": datetime.now(timezone.utc).isoformat(),
            "method_version": config.METHOD_VERSION,
            "feedback": [],
        },
    )
