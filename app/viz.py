"""Детерминированные карты-визуалы (Pillow-free, через matplotlib/Agg).

Рисуем то, что УЖЕ посчитано кодом: натальное колесо с реальными положениями планет,
либо карточку чисел/арканов, если астро не подключено. Никаких выдумок — только
calculated-поля. Чистый светлый стиль, без эзотерического китча.
"""
from __future__ import annotations

import io
import math
from typing import Any, Optional

import matplotlib

matplotlib.use("Agg")  # без дисплея, рендер в память
import matplotlib.pyplot as plt  # noqa: E402

# Палитра по стихии — приглушённая, «редакционная».
_ELEMENT_COLOR = {
    "огонь": "#c0573b", "земля": "#8a7a4e", "воздух": "#5b7a99", "вода": "#3f7d86",
}
_INK = "#222222"
_MUTED = "#9a9a9a"
_LINE = "#d9d9d9"

_SIGNS = [
    "Овен", "Телец", "Близнецы", "Рак", "Лев", "Дева",
    "Весы", "Скорпион", "Стрелец", "Козерог", "Водолей", "Рыбы",
]
_PLANET_ABBR = {
    "sun": "Сол", "moon": "Лун", "mercury": "Мер", "venus": "Вен", "mars": "Мар",
    "jupiter": "Юп", "saturn": "Сат", "uranus": "Ур", "neptune": "Неп", "pluto": "Плу",
}


def _modules_of(profile_data: dict) -> dict:
    """Плоские модули человека: для синастрии берём person_a."""
    mods = profile_data.get("calculation_modules") or {}
    if "person_a" in mods:
        return mods["person_a"]
    return mods


def _lon(short: dict) -> Optional[float]:
    try:
        return _SIGNS.index(short["sign"]) * 30.0 + float(short.get("degree", 0))
    except (KeyError, ValueError, TypeError):
        return None


def _xy(lon: float, r: float) -> tuple[float, float]:
    """0° Овна — наверху, ход по часовой (как циферблат)."""
    a = math.radians(90.0 - lon)
    return r * math.cos(a), r * math.sin(a)


def render_chart(profile_data: dict) -> bytes:
    """PNG-байты: натальное колесо (если есть западная астрология) или карточка чисел."""
    mods = _modules_of(profile_data)
    west = mods.get("western_astrology") or {}
    ui = profile_data.get("user_input") or {}
    title = (ui.get("name") or "Профиль").strip()
    subtitle = ui.get("birth_date") or ""
    if west.get("calculation_status") == "calculated":
        return _natal_wheel(west, mods, title, subtitle)
    return _numbers_card(mods, title, subtitle)


def _natal_wheel(west: dict, mods: dict, title: str, subtitle: str) -> bytes:
    fig, ax = plt.subplots(figsize=(6, 6.6), dpi=150)
    ax.set_xlim(-1.15, 1.15)
    ax.set_ylim(-1.25, 1.15)
    ax.set_aspect("equal")
    ax.axis("off")

    r_out, r_in, r_planet = 1.0, 0.80, 0.63
    ax.add_patch(plt.Circle((0, 0), r_out, fill=False, color=_LINE, lw=1.4))
    ax.add_patch(plt.Circle((0, 0), r_in, fill=False, color=_LINE, lw=1.0))

    for i in range(12):
        x0, y0 = _xy(i * 30, r_in)
        x1, y1 = _xy(i * 30, r_out)
        ax.plot([x0, x1], [y0, y1], color=_LINE, lw=0.8)
        lx, ly = _xy(i * 30 + 15, (r_in + r_out) / 2)
        ax.text(lx, ly, _SIGNS[i], ha="center", va="center", fontsize=7,
                color=_MUTED, rotation=0)

    # планеты + асцендент
    points: list[tuple[float, str, str]] = []
    for key in _PLANET_ABBR:
        p = (west.get("planets") or {}).get(key) or west.get(key)
        lon = _lon(p) if p else None
        if lon is not None:
            points.append((lon, _PLANET_ABBR[key], west.get("dominant_element", "воздух")))
    asc_lon = _lon(west.get("ascendant") or {})
    if asc_lon is not None:
        ax.annotate("ASC", xy=_xy(asc_lon, r_out), xytext=_xy(asc_lon, r_out + 0.1),
                    ha="center", va="center", fontsize=7.5, color=_INK, fontweight="bold")
        x0, y0 = _xy(asc_lon, r_in)
        x1, y1 = _xy(asc_lon, r_out)
        ax.plot([x0, x1], [y0, y1], color=_INK, lw=1.2)

    dom = west.get("dominant_element", "воздух")
    pcolor = _ELEMENT_COLOR.get(dom, _INK)
    for lon, abbr, _ in points:
        x, y = _xy(lon, r_planet)
        ax.plot(x, y, "o", ms=6, color=pcolor)
        lx, ly = _xy(lon, r_planet - 0.13)
        ax.text(lx, ly, abbr, ha="center", va="center", fontsize=7, color=_INK)

    sun = west.get("sun", {}).get("sign", "")
    moon = west.get("moon", {}).get("sign", "")
    asc = west.get("ascendant", {}).get("sign", "")
    ax.text(0, 1.18, title, ha="center", va="center", fontsize=14, color=_INK, fontweight="bold")
    if subtitle:
        ax.text(0, 1.06, subtitle, ha="center", va="center", fontsize=8.5, color=_MUTED)
    foot = f"☉ {sun}   ☾ {moon}   ASC {asc}   ·   стихия: {dom}"
    ax.text(0, -1.16, foot, ha="center", va="center", fontsize=8.5, color=_INK)

    return _to_png(fig)


def _numbers_card(mods: dict, title: str, subtitle: str) -> bytes:
    num = mods.get("numerology") or {}
    arc = mods.get("arcana_22") or {}
    core = (arc.get("core_arcana") or {})
    fig, ax = plt.subplots(figsize=(6, 6.6), dpi=150)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    ax.text(0.5, 0.93, title, ha="center", fontsize=15, color=_INK, fontweight="bold")
    if subtitle:
        ax.text(0.5, 0.875, subtitle, ha="center", fontsize=9, color=_MUTED)

    lp = num.get("life_path")
    ax.add_patch(plt.Circle((0.5, 0.66), 0.13, fill=False, color=_LINE, lw=1.6))
    ax.text(0.5, 0.66, str(lp if lp is not None else "—"), ha="center", va="center",
            fontsize=34, color=_INK, fontweight="bold")
    ax.text(0.5, 0.51, "число жизненного пути", ha="center", fontsize=9, color=_MUTED)

    rows = [
        ("Число судьбы", num.get("destiny_number")),
        ("Число души", num.get("soul_number")),
        ("Число личности", num.get("personality_number")),
        ("Число дня", num.get("birthday_number")),
        ("Личный год", num.get("personal_year")),
    ]
    y = 0.40
    for label, val in rows:
        ax.text(0.30, y, label, ha="left", fontsize=10, color=_INK)
        ax.text(0.70, y, str(val if val is not None else "—"), ha="right", fontsize=10,
                color=_INK, fontweight="bold")
        ax.plot([0.30, 0.70], [y - 0.018, y - 0.018], color=_LINE, lw=0.6)
        y -= 0.058
    if core.get("name"):
        ax.text(0.5, y - 0.02, f"Ядро арканов: {core.get('value')} · {core.get('name')}",
                ha="center", fontsize=10, color=_INK)
    return _to_png(fig)


def _to_png(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor="white")
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()
