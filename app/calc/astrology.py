"""Астро-модули v0.2: западная астрология, джйотиш, Ба Цзы.

Все три считаются детерминированно из реальных эфемерид (Swiss Ephemeris).
Требуют время рождения + место (город → координаты + таймзона). Без них —
статус insufficient_input, и модуль честно сообщает, чего не хватает.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Optional

from . import ephemeris as eph

_CONFIG = json.loads((Path(__file__).resolve().parents[2] / "config" / "astro.json").read_text("utf-8"))
_INSUFFICIENT = {
    "calculation_status": "insufficient_input",
    "needs": "точное время рождения (ЧЧ:ММ) и город рождения",
}


def _have_birth_data(birth_time: Optional[str], geo: Optional[dict]) -> bool:
    return bool(birth_time and geo and geo.get("timezone") and geo.get("lat") is not None)


# ---------- западная астрология ----------
def western(birth: date, birth_time: Optional[str], geo: Optional[dict]) -> dict:
    if not _have_birth_data(birth_time, geo):
        return dict(_INSUFFICIENT)
    jd = eph.to_julian_ut(birth, birth_time, geo["timezone"])
    planets = eph.planet_positions(jd, sidereal=False)
    houses = eph.ascendant_houses(jd, geo["lat"], geo["lon"], sidereal=False)

    elements: dict[str, int] = {e: 0 for e in eph.ELEMENTS_RU}
    for key in eph.PLANETS:  # только 10 планет, без узлов
        elements[planets[key]["element"]] += 1
    dominant = max(elements, key=elements.get)

    return {
        "calculation_status": "calculated",
        "zodiac": _CONFIG["western"]["zodiac"],
        "house_system": _CONFIG["western"]["house_system"],
        "sun": _short(planets["sun"]),
        "moon": _short(planets["moon"]),
        "ascendant": _short(houses["ascendant"]),
        "midheaven": _short(houses["midheaven"]),
        "planets": {k: _short(planets[k]) for k in eph.PLANETS},
        "elements_balance": elements,
        "dominant_element": dominant,
    }


def _short(p: dict) -> dict:
    out = {"sign": p["sign"], "degree": p["degree"], "element": p["element"]}
    if "retrograde" in p:
        out["retrograde"] = p["retrograde"]
    return out


# ---------- джйотиш (ведическая) ----------
_NAK_SPAN = 360.0 / 27.0


def jyotish(birth: date, birth_time: Optional[str], geo: Optional[dict], today: Optional[date] = None) -> dict:
    if not _have_birth_data(birth_time, geo):
        return dict(_INSUFFICIENT)
    today = today or date.today()
    cfg = _CONFIG["jyotish"]
    jd = eph.to_julian_ut(birth, birth_time, geo["timezone"])
    planets = eph.planet_positions(jd, sidereal=True)
    houses = eph.ascendant_houses(jd, geo["lat"], geo["lon"], sidereal=True)

    moon_lon = planets["moon"]["lon"]
    nak_index = int(moon_lon // _NAK_SPAN)
    pos_in_nak = moon_lon - nak_index * _NAK_SPAN
    pada = int(pos_in_nak // (_NAK_SPAN / 4)) + 1
    nakshatra = cfg["nakshatras"][nak_index]

    dasha = _vimshottari(nak_index, pos_in_nak / _NAK_SPAN, birth, today, cfg)

    return {
        "calculation_status": "calculated",
        "zodiac": cfg["zodiac"],
        "ayanamsa": cfg["ayanamsa"],
        "lagna": _short(houses["ascendant"]),
        "sun_rashi": planets["sun"]["sign"],
        "moon_rashi": planets["moon"]["sign"],
        "nakshatra": {"name": nakshatra, "pada": pada},
        "rahu_rashi": planets["rahu"]["sign"],
        "ketu_rashi": planets["ketu"]["sign"],
        "current_dasha": dasha,
    }


def _vimshottari(nak_index: int, fraction: float, birth: date, today: date, cfg: dict) -> dict:
    """Текущая махадаша (период) по системе Вимшоттари — 120-летний цикл от Луны."""
    lords = cfg["vimshottari_lords"]
    years = cfg["vimshottari_years"]
    start_idx = nak_index % 9
    balance = years[lords[start_idx]] * (1.0 - fraction)  # остаток первой даши на момент рождения

    age = (today - birth).days / 365.25
    elapsed = balance
    idx = start_idx
    if age <= balance:
        return {"lord": lords[start_idx], "remaining_years": round(balance - age, 1)}
    while True:
        idx = (idx + 1) % 9
        dur = years[lords[idx]]
        if age <= elapsed + dur:
            return {"lord": lords[idx], "remaining_years": round(elapsed + dur - age, 1)}
        elapsed += dur


# ---------- Ба Цзы (китайские четыре столпа) ----------
def bazi(birth: date, birth_time: Optional[str], geo: Optional[dict]) -> dict:
    if not _have_birth_data(birth_time, geo):
        return dict(_INSUFFICIENT)
    cfg = _CONFIG["bazi"]
    stems, branches = cfg["stems"], cfg["branches"]

    jd = eph.to_julian_ut(birth, birth_time, geo["timezone"])
    sun_lon = eph.planet_positions(jd)["sun"]["lon"]
    hh = int(birth_time.split(":")[0])

    # солнечный год Ба Цзы начинается на Личунь (Солнце = 315°), ~4 февраля
    solar_year = birth.year
    if birth.month == 1 or (birth.month == 2 and sun_lon < 315):
        solar_year -= 1
    y_stem, y_branch = (solar_year - 4) % 10, (solar_year - 4) % 12

    # солнечный месяц: 30° от 315°, m=0 → месяц Тигра (Инь)
    m = int(((sun_lon - 315) % 360) // 30)
    mo_branch = (m + 2) % 12
    first_month_stem = (2 + 2 * (y_stem % 5)) % 10  # правило «пяти тигров»
    mo_stem = (first_month_stem + m) % 10

    # день: шестидесятеричный счёт, опорная дата 1900-01-01 = Цзя-Сюй
    jdn = int(eph.swe.julday(birth.year, birth.month, birth.day, 12.0))
    day_index = (jdn - 2451551) % 60
    d_stem, d_branch = day_index % 10, day_index % 12

    # час: ветвь по 2-часовым отрезкам (Цзы = 23:00–00:59), стем по правилу «пяти крыс»
    h_branch = ((hh + 1) // 2) % 12
    first_hour_stem = (2 * (d_stem % 5)) % 10
    h_stem = (first_hour_stem + h_branch) % 10

    pillars = {
        "year": _pillar(stems[y_stem], branches[y_branch]),
        "month": _pillar(stems[mo_stem], branches[mo_branch]),
        "day": _pillar(stems[d_stem], branches[d_branch]),
        "hour": _pillar(stems[h_stem], branches[h_branch]),
    }
    elements: dict[str, int] = {}
    for p in pillars.values():
        elements[p["stem_element"]] = elements.get(p["stem_element"], 0) + 1
        elements[p["branch_element"]] = elements.get(p["branch_element"], 0) + 1

    return {
        "calculation_status": "calculated",
        "pillars": pillars,
        "day_master": {"stem": stems[d_stem]["name"], "element": stems[d_stem]["element"], "polarity": stems[d_stem]["polarity"]},
        "elements_balance": elements,
        "dominant_element": max(elements, key=elements.get),
    }


def _pillar(stem: dict, branch: dict) -> dict:
    return {
        "stem": stem["name"],
        "branch": branch["name"],
        "animal": branch["animal"],
        "stem_element": stem["element"],
        "branch_element": branch["element"],
        "polarity": stem["polarity"],
    }
