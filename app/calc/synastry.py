"""Синастрия — детерминированная совместимость двух человек.

Сравнивает УЖЕ посчитанные модули двух людей: числа, стихии, межкарточные аспекты
(если у обоих есть астро) и связь господ дня в Ба Цзы. AI поверх собирает портрет пары —
сам ничего не выдумывает, опирается только на calculated-поля.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from . import ephemeris as eph

_ASPECTS = json.loads(
    (Path(__file__).resolve().parents[2] / "config" / "astro.json").read_text("utf-8")
)["western"]["aspects"]

# Совместимость стихий западной астрологии.
_ELEMENT_HARMONY = {
    ("огонь", "воздух"), ("воздух", "огонь"),
    ("земля", "вода"), ("вода", "земля"),
}
_ELEMENT_TENSION = {
    ("огонь", "вода"), ("вода", "огонь"),
    ("земля", "воздух"), ("воздух", "земля"),
}

# Порождающий и контролирующий циклы У-син (Ба Цзы).
_WUXING_GENERATES = {"дерево": "огонь", "огонь": "земля", "земля": "металл", "металл": "вода", "вода": "дерево"}
_WUXING_CONTROLS = {"дерево": "земля", "земля": "вода", "вода": "огонь", "огонь": "металл", "металл": "дерево"}

# Какие пары точек ищем между картами (A-точка, B-точка, ярлык).
_CROSS_PAIRS = [
    ("sun", "moon", "Солнце ↔ Луна"),
    ("moon", "sun", "Луна ↔ Солнце"),
    ("venus", "mars", "Венера ↔ Марс"),
    ("mars", "venus", "Марс ↔ Венера"),
    ("sun", "sun", "Солнце ↔ Солнце"),
    ("moon", "moon", "Луна ↔ Луна"),
    ("venus", "venus", "Венера ↔ Венера"),
    ("ascendant", "sun", "Асцендент ↔ Солнце"),
]


def _lon_from(short: dict) -> Optional[float]:
    """Восстановить эклиптическую долготу из знака+градуса (для аспектов хватает точности)."""
    try:
        return eph.SIGNS_RU.index(short["sign"]) * 30.0 + float(short["degree"])
    except (KeyError, ValueError, TypeError):
        return None


def _point(west: dict, key: str) -> Optional[dict]:
    if key in ("sun", "moon", "ascendant"):
        return west.get(key)
    return (west.get("planets") or {}).get(key)


def _element_relation(a: str, b: str) -> str:
    if a == b:
        return "резонанс (одна стихия)"
    if (a, b) in _ELEMENT_HARMONY:
        return "дополняют друг друга"
    if (a, b) in _ELEMENT_TENSION:
        return "напряжение/притяжение противоположностей"
    return "нейтрально"


def _bazi_link(dm_a: str, dm_b: str) -> str:
    if dm_a == dm_b:
        return "одна стихия господ дня — похожесть и конкуренция"
    if _WUXING_GENERATES.get(dm_a) == dm_b:
        return f"{dm_a} порождает {dm_b}: A питает и поддерживает B"
    if _WUXING_GENERATES.get(dm_b) == dm_a:
        return f"{dm_b} порождает {dm_a}: B питает и поддерживает A"
    if _WUXING_CONTROLS.get(dm_a) == dm_b:
        return f"{dm_a} контролирует {dm_b}: A структурирует/давит на B"
    if _WUXING_CONTROLS.get(dm_b) == dm_a:
        return f"{dm_b} контролирует {dm_a}: B структурирует/давит на A"
    return "нейтральная связь стихий"


def compute(modules_a: dict, modules_b: dict, name_a: str, name_b: str) -> dict:
    """Синастрия двух наборов модулей. Числа есть всегда; астро-блоки — если у обоих calculated."""
    out: dict = {"calculation_status": "calculated", "person_a": name_a, "person_b": name_b}

    num_a, num_b = modules_a.get("numerology", {}), modules_b.get("numerology", {})
    lp_a, lp_b = num_a.get("life_path"), num_b.get("life_path")
    out["numerology"] = {
        "life_path_a": lp_a,
        "life_path_b": lp_b,
        "same_life_path": lp_a is not None and lp_a == lp_b,
        "life_path_sum": (lp_a + lp_b) if (lp_a and lp_b) else None,
    }

    west_a, west_b = modules_a.get("western_astrology", {}), modules_b.get("western_astrology", {})
    both_astro = west_a.get("calculation_status") == "calculated" and west_b.get("calculation_status") == "calculated"
    if both_astro:
        out["sun_moon_elements"] = {
            "a_sun": west_a["sun"]["sign"], "b_sun": west_b["sun"]["sign"],
            "a_moon": west_a["moon"]["sign"], "b_moon": west_b["moon"]["sign"],
            "sun_relation": _element_relation(west_a["sun"]["element"], west_b["sun"]["element"]),
            "moon_relation": _element_relation(west_a["moon"]["element"], west_b["moon"]["element"]),
        }
        hits = []
        for a_key, b_key, label in _CROSS_PAIRS:
            pa, pb = _point(west_a, a_key), _point(west_b, b_key)
            if not pa or not pb:
                continue
            la, lb = _lon_from(pa), _lon_from(pb)
            if la is None or lb is None:
                continue
            asp = eph.match_aspect(la, lb, _ASPECTS)
            if asp:
                hits.append({"pair": label, "aspect": asp["name"], "orb": asp["orb"]})
        hits.sort(key=lambda h: h["orb"])
        out["synastry_aspects"] = hits[:8]
    else:
        out["astro_note"] = "Межкарточные аспекты доступны, когда у обоих есть время и место рождения."

    baz_a, baz_b = modules_a.get("bazi", {}), modules_b.get("bazi", {})
    if baz_a.get("calculation_status") == "calculated" and baz_b.get("calculation_status") == "calculated":
        out["bazi_link"] = {
            "a_day_master": baz_a["day_master"]["element"],
            "b_day_master": baz_b["day_master"]["element"],
            "relation": _bazi_link(baz_a["day_master"]["element"], baz_b["day_master"]["element"]),
        }

    return out
