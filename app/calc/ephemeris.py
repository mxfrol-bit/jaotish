"""Эфемериды (Swiss Ephemeris) — реальные положения планет, асцендент, дома.

Это общий «движок спутников» для западной астрологии, джйотиша и Ба Цзы.
Работает на встроенном Moshier-эфемерисе (set_ephe_path(None)) — без файлов данных,
точность до угловых секунд, чего с запасом хватает для профайлинга.

Геокодинг места рождения — через OpenStreetMap/Nominatim (бесплатно, без ключа),
таймзона — офлайн через timezonefinder + историческая DST через zoneinfo.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional
from zoneinfo import ZoneInfo

import requests
import swisseph as swe

swe.set_ephe_path(None)  # встроенный Moshier, без внешних файлов

# --- планеты (id Swiss Ephemeris) ---
PLANETS = {
    "sun": swe.SUN,
    "moon": swe.MOON,
    "mercury": swe.MERCURY,
    "venus": swe.VENUS,
    "mars": swe.MARS,
    "jupiter": swe.JUPITER,
    "saturn": swe.SATURN,
    "uranus": swe.URANUS,
    "neptune": swe.NEPTUNE,
    "pluto": swe.PLUTO,
}
PLANETS_RU = {
    "sun": "Солнце", "moon": "Луна", "mercury": "Меркурий", "venus": "Венера",
    "mars": "Марс", "jupiter": "Юпитер", "saturn": "Сатурн", "uranus": "Уран",
    "neptune": "Нептун", "pluto": "Плутон", "rahu": "Раху", "ketu": "Кету",
}

SIGNS_RU = [
    "Овен", "Телец", "Близнецы", "Рак", "Лев", "Дева",
    "Весы", "Скорпион", "Стрелец", "Козерог", "Водолей", "Рыбы",
]
ELEMENTS_RU = ["огонь", "земля", "воздух", "вода"]  # по индексу знака % 4


def sign_of(lon: float) -> dict:
    """Знак зодиака по эклиптической долготе (0..360)."""
    lon = lon % 360.0
    idx = int(lon // 30)
    return {
        "sign": SIGNS_RU[idx],
        "sign_index": idx,
        "degree": round(lon - idx * 30, 2),
        "element": ELEMENTS_RU[idx % 4],
    }


# ---------- геокодинг и время ----------
def geocode(place: str) -> Optional[dict]:
    """Город → координаты через Nominatim. None, если не нашли/сеть недоступна."""
    try:
        r = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": place, "format": "json", "limit": 1},
            headers={"User-Agent": "MatrixEngine/0.2 (profiling bot)"},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
    except (requests.RequestException, ValueError):
        return None
    if not data:
        return None
    top = data[0]
    return {
        "lat": float(top["lat"]),
        "lon": float(top["lon"]),
        "display_name": top.get("display_name", place),
    }


_TF = None


def _timezone_at(lat: float, lon: float) -> Optional[str]:
    global _TF
    if _TF is None:
        from timezonefinder import TimezoneFinder

        _TF = TimezoneFinder()
    return _TF.timezone_at(lat=lat, lng=lon)


def resolve_geo(place: str) -> Optional[dict]:
    """Полное разрешение места: координаты + IANA-таймзона. None при неудаче."""
    geo = geocode(place)
    if geo is None:
        return None
    tz = _timezone_at(geo["lat"], geo["lon"])
    if tz is None:
        return None
    geo["timezone"] = tz
    return geo


def to_julian_ut(birth: date, birth_time: str, tz_name: str) -> float:
    """Локальные дата+время+таймзона → юлианский день в UT (с учётом исторической DST)."""
    hh, mm = (int(x) for x in birth_time.split(":")[:2])
    local = datetime(birth.year, birth.month, birth.day, hh, mm, tzinfo=ZoneInfo(tz_name))
    ut = local.astimezone(ZoneInfo("UTC"))
    return swe.julday(ut.year, ut.month, ut.day, ut.hour + ut.minute / 60.0)


# ---------- расчёт ----------
def _calc_flags(sidereal: bool) -> int:
    flags = swe.FLG_SWIEPH | swe.FLG_SPEED
    if sidereal:
        swe.set_sid_mode(swe.SIDM_LAHIRI, 0, 0)
        flags |= swe.FLG_SIDEREAL
    return flags


def planet_positions(jd_ut: float, sidereal: bool = False) -> dict:
    """Долготы планет (+ ретроградность). sidereal=True → айянамша Лахири (джйотиш)."""
    flags = _calc_flags(sidereal)
    out = {}
    for key, pid in PLANETS.items():
        xx = swe.calc_ut(jd_ut, pid, flags)[0]
        lon, speed = xx[0], xx[3]
        out[key] = {
            "name": PLANETS_RU[key],
            "lon": round(lon % 360, 3),
            "retrograde": speed < 0,
            **sign_of(lon),
        }
    # лунные узлы Раху/Кету (для джйотиша важны)
    node = swe.calc_ut(jd_ut, swe.MEAN_NODE, flags)[0][0]
    out["rahu"] = {"name": PLANETS_RU["rahu"], "lon": round(node % 360, 3), "retrograde": True, **sign_of(node)}
    out["ketu"] = {"name": PLANETS_RU["ketu"], "lon": round((node + 180) % 360, 3), "retrograde": True, **sign_of(node + 180)}
    return out


def noon_jd(d: date) -> float:
    """Юлианский день на 12:00 UT — для транзитов/прогрессий (без привязки к месту)."""
    return swe.julday(d.year, d.month, d.day, 12.0)


def separation(lon_a: float, lon_b: float) -> float:
    """Угловое расстояние между двумя долготами, 0..180°."""
    diff = abs((lon_a - lon_b) % 360.0)
    return diff if diff <= 180.0 else 360.0 - diff


def match_aspect(lon_a: float, lon_b: float, aspects: list[dict]) -> Optional[dict]:
    """Какой аспект (из конфигурации) образуют две долготы. None — вне орбиса."""
    sep = separation(lon_a, lon_b)
    for asp in aspects:
        if abs(sep - asp["angle"]) <= asp["orb"]:
            return {"name": asp["name"], "angle": asp["angle"], "orb": round(abs(sep - asp["angle"]), 2)}
    return None


def house_of(lon: float, houses: list[dict]) -> int:
    """В каком доме (1..12) лежит долгота lon, по куспидам Placidus."""
    lon = lon % 360.0
    cusps = [h["lon"] % 360.0 for h in houses]  # index 0 = куспид 1-го дома
    for i in range(12):
        a, b = cusps[i], cusps[(i + 1) % 12]
        if a <= b:
            if a <= lon < b:
                return i + 1
        else:  # дом пересекает 0° Овна
            if lon >= a or lon < b:
                return i + 1
    return 12


def ascendant_houses(jd_ut: float, lat: float, lon: float, sidereal: bool = False) -> dict:
    """Асцендент, MC и 12 куспидов домов (Placidus). sidereal=True → айянамша Лахири."""
    flags = swe.FLG_SIDEREAL if sidereal else 0
    if sidereal:
        swe.set_sid_mode(swe.SIDM_LAHIRI, 0, 0)
    cusps, ascmc = swe.houses_ex(jd_ut, lat, lon, b"P", flags)
    asc, mc = ascmc[0], ascmc[1]
    return {
        "ascendant": {"lon": round(asc % 360, 3), **sign_of(asc)},
        "midheaven": {"lon": round(mc % 360, 3), **sign_of(mc)},
        "houses": [{"house": i + 1, "lon": round(c % 360, 3), **sign_of(c)} for i, c in enumerate(cusps)],
    }
