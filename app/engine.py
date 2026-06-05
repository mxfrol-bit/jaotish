"""Сборка профиля end-to-end: расчёт → AI-синтез → итоговый отчёт."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any

from . import config, synthesis
from .calc import matrix_calc
from .models import AnalysisType, Profile, ProfileRequest, UserInput

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

    tr = modules.get("transits", {})
    if tr.get("calculation_status") == "calculated":
        asp = tr.get("transit_aspects", [])
        pr = tr.get("progressed", {})
        lines += [
            f"**Транзиты и прогрессии (на {tr.get('as_of')}):**",
            "- Ключевые транзиты: "
            + (", ".join(f"{h['transit']} {h['aspect']} {h['natal']}" for h in asp[:5]) or "значимых нет"),
            f"- Прогрессивные Солнце/Луна: **{pr.get('sun', {}).get('sign', '—')}** / "
            f"**{pr.get('moon', {}).get('sign', '—')}**",
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
            f"- Навамса (D9): лагна **{jyo['navamsa_d9']['lagna']}**, "
            f"Луна **{jyo['navamsa_d9']['moon']}**, Венера **{jyo['navamsa_d9']['venus']}**",
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
        lp = baz.get("luck_pillars")
        if lp:
            approx = " (направление приблизительно — уточни пол)" if lp.get("assumed_direction") else ""
            nxt = ", ".join(
                f"{p['from_age']:g}+: {p['stem']}-{p['branch']} ({p['branch_element']})"
                for p in lp["pillars"][:4]
            )
            lines += [
                f"- Столпы удачи (大運, {lp['direction']}{approx}), вход с ~{lp['start_age']:g} лет: {nxt}",
                "",
            ]

    lines += ["**Статусы методик:**"]
    for key in ("numerology", "arcana_22", "western_astrology", "transits", "jyotish", "bazi"):
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


def _synastry_header(syn: dict, name_a: str, name_b: str) -> str:
    lines = [f"## Совместимость: {name_a or 'Ты'} и {name_b or 'партнёр'}", ""]
    num = syn.get("numerology", {})
    lines += [
        "**Нумерология пары:**",
        f"- Числа пути: **{num.get('life_path_a')}** и **{num.get('life_path_b')}**"
        + (" — совпадают" if num.get("same_life_path") else ""),
        "",
    ]
    sm = syn.get("sun_moon_elements")
    if sm:
        lines += [
            "**Светила (западная астрология):**",
            f"- Солнца: {sm['a_sun']} / {sm['b_sun']} — {sm['sun_relation']}",
            f"- Луны: {sm['a_moon']} / {sm['b_moon']} — {sm['moon_relation']}",
            "",
        ]
    asp = syn.get("synastry_aspects")
    if asp:
        lines += [
            "**Межкарточные аспекты:**",
            "- " + "; ".join(f"{h['pair']} — {h['aspect']} (орб {h['orb']}°)" for h in asp[:6]),
            "",
        ]
    bz = syn.get("bazi_link")
    if bz:
        lines += ["**Ба Цзы (господа дня):**", f"- {bz['relation']}", ""]
    if syn.get("astro_note"):
        lines += [f"_{syn['astro_note']}_", ""]
    return "\n".join(lines)


def build_synastry(
    user_req: ProfileRequest, partner_req: ProfileRequest, today: date | None = None
) -> Profile:
    """Разбор совместимости двух людей: модули обоих → синастрия → AI-портрет пары."""
    from .calc import synastry as syn_calc

    today = today or date.today()
    geo_a = _resolve_geo(user_req)
    geo_b = _resolve_geo(partner_req)
    modules_a = matrix_calc.compute_all(user_req.birth_date, user_req.name, today, user_req.birth_time, geo_a, user_req.gender)
    modules_b = matrix_calc.compute_all(partner_req.birth_date, partner_req.name, today, partner_req.birth_time, geo_b, partner_req.gender)

    syn = syn_calc.compute(modules_a, modules_b, user_req.name, partner_req.name)

    user_input = UserInput(
        name=user_req.name,
        gender=user_req.gender,
        birth_date=user_req.birth_date,
        birth_time=user_req.birth_time,
        birth_place=user_req.birth_place,
        main_request=f"совместимость с {partner_req.name or 'партнёром'}",
        analysis_type=AnalysisType.compatibility,
    )

    ai = synthesis.synthesize_synastry(
        user_input.model_dump(mode="json"),
        {"name": partner_req.name, "birth_date": partner_req.birth_date.isoformat()},
        syn,
    )

    header = _synastry_header(syn, user_req.name, partner_req.name)
    full_report = header + "\n---\n\n" + ai["full_report"]

    return Profile(
        profile_id=str(uuid.uuid4()),
        user_input=user_input,
        geo=geo_a or {"lat": None, "lon": None, "timezone": ""},
        calculation_modules={"person_a": modules_a, "person_b": modules_b, "synastry": syn},
        synthesis={"engine": "ai", "model": config.OPENROUTER_MODEL if config.ai_ready() else None},
        report={
            "short_summary": ai["short_summary"],
            "full_report": full_report,
            "action_plan": ai.get("action_plan", ""),
        },
        meta={
            "created_at": datetime.now(timezone.utc).isoformat(),
            "method_version": config.METHOD_VERSION,
            "partner": {"name": partner_req.name, "birth_date": partner_req.birth_date.isoformat()},
            "feedback": [],
        },
    )


def _event_header(event_date: date, event_desc: str, num: dict[str, Any]) -> str:
    """Шапка разбора события: дата + числовая энергия именно этой даты."""
    lines = [f"## Событие: {event_desc or 'без описания'}", "", f"**Дата:** {event_date.isoformat()}", ""]
    if num.get("calculation_status") == "calculated":
        lines += [
            "**Числовая энергия даты:**",
            f"- Личный день: **{num.get('personal_day')}**, личный месяц: **{num.get('personal_month')}**, "
            f"личный год: **{num.get('personal_year')}**",
            f"- Универсальный день: **{num.get('universal_day')}**",
            "",
        ]
    return "\n".join(lines)


def build_event(
    req: ProfileRequest, event_date: date, event_desc: str, today: date | None = None
) -> Profile:
    """Разбор конкретной даты/сделки: снимок натала НА ЭТУ ДАТУ → AI-вердикт «стоит ли»."""
    geo = _resolve_geo(req)
    # today = event_date: транзиты, прогрессии, личный день считаются именно на дату события.
    modules = matrix_calc.compute_all(req.birth_date, req.name, event_date, req.birth_time, geo, req.gender)

    user_input = UserInput(
        name=req.name,
        gender=req.gender,
        birth_date=req.birth_date,
        birth_time=req.birth_time,
        birth_place=req.birth_place,
        event_date=event_date,
        main_request=event_desc,
        analysis_type=AnalysisType.event,
    )

    ai = synthesis.synthesize_event(
        user_input.model_dump(mode="json"), modules, event_date.isoformat(), event_desc
    )

    header = _event_header(event_date, event_desc, modules.get("numerology", {})) + _calc_header(modules)
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
            "event": {"date": event_date.isoformat(), "desc": event_desc},
            "feedback": [],
        },
    )


def build_profile(req: ProfileRequest, today: date | None = None) -> Profile:
    today = today or date.today()
    birth = req.birth_date

    geo = _resolve_geo(req)
    modules = matrix_calc.compute_all(birth, req.name, today, req.birth_time, geo, req.gender)

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
