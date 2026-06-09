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

    # Готовый к показу список положений (как у Co-Star: знак, градус, дом, ретроградность).
    cusps = houses["houses"]
    positions = []
    for key in eph.PLANETS:
        p = planets[key]
        positions.append({
            "key": key,
            "name": p["name"],
            "sign": p["sign"],
            "degree": p["degree"],
            "minute": int(round((float(p["degree"]) % 1) * 60)),
            "house": eph.house_of(p["lon"], cusps),
            "retrograde": bool(p.get("retrograde")),
        })

    asc = houses["ascendant"]
    return {
        "calculation_status": "calculated",
        "zodiac": _CONFIG["western"]["zodiac"],
        "house_system": _CONFIG["western"]["house_system"],
        "sun": _short(planets["sun"]),
        "moon": _short(planets["moon"]),
        "ascendant": {**_short(asc), "house": 1,
                      "minute": int(round((float(asc["degree"]) % 1) * 60))},
        "midheaven": _short(houses["midheaven"]),
        "planets": {k: _short(planets[k]) for k in eph.PLANETS},
        "positions": positions,
        "elements_balance": elements,
        "dominant_element": dominant,
    }


def _short(p: dict) -> dict:
    out = {"sign": p["sign"], "degree": p["degree"], "element": p["element"]}
    if "retrograde" in p:
        out["retrograde"] = p["retrograde"]
    return out


# ---------- транзиты и прогрессии (текущие влияния) ----------
# Медленные планеты дают «погоду» периода; быстрые транзиты шумят и их опускаем.
_TRANSIT_FROM = ("jupiter", "saturn", "uranus", "neptune", "pluto")
_TRANSIT_TO = ("sun", "moon", "mercury", "venus", "mars")


def transits(birth: date, birth_time: Optional[str], geo: Optional[dict], today: Optional[date] = None) -> dict:
    """Транзиты медленных планет к наталу + вторичные прогрессии Солнца/Луны на сегодня."""
    if not _have_birth_data(birth_time, geo):
        return dict(_INSUFFICIENT)
    today = today or date.today()
    aspects_cfg = _CONFIG["western"]["aspects"]

    natal_jd = eph.to_julian_ut(birth, birth_time, geo["timezone"])
    natal = eph.planet_positions(natal_jd, sidereal=False)
    natal_houses = eph.ascendant_houses(natal_jd, geo["lat"], geo["lon"], sidereal=False)
    natal_points = {k: natal[k]["lon"] for k in _TRANSIT_TO}
    natal_points["ascendant"] = natal_houses["ascendant"]["lon"]
    natal_points["midheaven"] = natal_houses["midheaven"]["lon"]

    transit_now = eph.planet_positions(eph.noon_jd(today), sidereal=False)

    hits: list[dict] = []
    for t_key in _TRANSIT_FROM:
        t_lon = transit_now[t_key]["lon"]
        for n_key, n_lon in natal_points.items():
            asp = eph.match_aspect(t_lon, n_lon, aspects_cfg)
            if asp:
                hits.append({
                    "transit": eph.PLANETS_RU[t_key],
                    "aspect": asp["name"],
                    "natal": _POINT_RU.get(n_key, n_key),
                    "orb": asp["orb"],
                    "transit_sign": transit_now[t_key]["sign"],
                })
    hits.sort(key=lambda h: h["orb"])

    # вторичные прогрессии: «день за год» — natal_jd сдвигаем на возраст в днях
    age_years = (today - birth).days / 365.25
    prog = eph.planet_positions(natal_jd + age_years, sidereal=False)

    return {
        "calculation_status": "calculated",
        "as_of": today.isoformat(),
        "transit_aspects": hits[:8],
        "progressed": {
            "sun": _short(prog["sun"]),
            "moon": _short(prog["moon"]),
        },
    }


_POINT_RU = {
    "sun": "Солнце", "moon": "Луна", "mercury": "Меркурий", "venus": "Венера",
    "mars": "Марс", "ascendant": "Асцендент", "midheaven": "MC",
}


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

    # Навамса (D9) — дробная карта брака и дхармы. Непрерывный счёт от 0° Овна
    # математически эквивалентен классическому правилу по стихии знака.
    navamsa = {"lagna": _navamsa_sign(houses["ascendant"]["lon"])}
    for k in ("sun", "moon", "venus", "mars", "jupiter", "saturn"):
        navamsa[k] = _navamsa_sign(planets[k]["lon"])

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
        "navamsa_d9": navamsa,
    }


_NAVAMSA_SPAN = 10.0 / 3.0  # 3°20' — девятая часть знака


def _navamsa_sign(lon: float) -> str:
    return eph.SIGNS_RU[int((lon % 360.0) // _NAVAMSA_SPAN) % 12]


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
def bazi(birth: date, birth_time: Optional[str], geo: Optional[dict], gender: str = "") -> dict:
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

    luck = _luck_pillars(stems, branches, mo_stem, mo_branch, y_stem, sun_lon, birth, gender)

    return {
        "calculation_status": "calculated",
        "pillars": pillars,
        "day_master": {"stem": stems[d_stem]["name"], "element": stems[d_stem]["element"], "polarity": stems[d_stem]["polarity"]},
        "elements_balance": elements,
        "dominant_element": max(elements, key=elements.get),
        "luck_pillars": luck,
    }


def _luck_pillars(stems, branches, mo_stem, mo_branch, y_stem, sun_lon, birth, gender, count=8):
    """Большие столпы удачи (大運): 10-летние периоды от месячного столпа.

    Направление: ян-год+муж или инь-год+жен → вперёд, иначе назад. Возраст входа —
    по «3 дня = 1 год» (≈3° долготы Солнца до соседнего месячного рубежа).
    """
    male = gender.strip().lower()[:1] in ("м", "m")  # «мужской»/«male»
    assumed = not gender.strip()
    year_yang = (y_stem % 2 == 0)  # чётный индекс стема = ян
    forward = (year_yang == male)

    offset = (sun_lon - 315.0) % 30.0  # положение Солнца внутри текущего месячного сектора
    deg_to_boundary = (30.0 - offset) if forward else offset
    start_age = round(deg_to_boundary / 3.0 * 2) / 2  # шаг 0.5 года

    step = 1 if forward else -1
    pillars = []
    for i in range(1, count + 1):
        s = stems[(mo_stem + step * i) % 10]
        b = branches[(mo_branch + step * i) % 12]
        pillars.append({
            "from_age": round(start_age + (i - 1) * 10, 1),
            "stem": s["name"],
            "branch": b["name"],
            "animal": b["animal"],
            "stem_element": s["element"],
            "branch_element": b["element"],
        })
    return {
        "direction": "вперёд" if forward else "назад",
        "assumed_direction": assumed,
        "start_age": start_age,
        "pillars": pillars,
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
