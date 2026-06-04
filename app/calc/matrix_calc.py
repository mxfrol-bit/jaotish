"""Детерминированное расчётное ядро Matrix Engine (v0.1).

Считает нумерологию (пифагорейская система) и центральный крест 22 арканов.
Никакого AI: чистые функции, результат воспроизводим по дате и имени.
Формулы и таблицы вынесены в config/*.json — школо-зависимое меняется без правки кода.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Optional

_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"


def _load(name: str) -> dict:
    with open(_CONFIG_DIR / name, encoding="utf-8") as f:
        return json.load(f)


_NUM = _load("numerology.json")
_ARC = _load("arcana.json")
_MASTER = set(_NUM["keep_master_numbers"])


# ---------- helpers ----------
def sum_digits(n: int) -> int:
    return sum(int(d) for d in str(abs(n)))


def reduce_num(n: int, keep_master: bool = True) -> int:
    """Свести к одной цифре, сохраняя мастер-числа (11/22/33)."""
    while n > 9 and not (keep_master and n in _MASTER):
        n = sum_digits(n)
    return n


def reduce22(n: int) -> int:
    """Свести в диапазон арканов 1..22 (0 -> 22)."""
    n = n % 22
    return 22 if n == 0 else n


# ---------- numerology ----------
def _letter_value(ch: str) -> Optional[int]:
    ch = ch.lower()
    for alphabet in ("latin", "cyrillic"):
        v = _NUM["letter_maps"][alphabet].get(ch)
        if v is not None:
            return v
    return None


def _is_vowel(ch: str) -> bool:
    ch = ch.lower()
    return ch in _NUM["vowels"]["latin"] or ch in _NUM["vowels"]["cyrillic"]


def _name_number(name: str, mode: str) -> Optional[int]:
    """mode: 'all' (судьба) | 'vowels' (душа) | 'consonants' (личность)."""
    total = 0
    counted = False
    for ch in name:
        v = _letter_value(ch)
        if v is None:
            continue
        if mode == "vowels" and not _is_vowel(ch):
            continue
        if mode == "consonants" and _is_vowel(ch):
            continue
        total += v
        counted = True
    if not counted:
        return None
    return reduce_num(total)


def numerology(birth: date, name: str = "", today: Optional[date] = None) -> dict:
    """Числа по дате (и по имени, если есть распознаваемые буквы)."""
    today = today or date.today()
    life_path = reduce_num(sum_digits(birth.year) + sum_digits(birth.month) + sum_digits(birth.day))
    birthday_number = reduce_num(birth.day)
    personal_year = reduce_num(sum_digits(today.year) + birth.month + birth.day)
    personal_month = reduce_num(personal_year + today.month)

    return {
        "calculation_status": "calculated",
        "system": _NUM["system"],
        "life_path": life_path,
        "birthday_number": birthday_number,
        "soul_number": _name_number(name, "vowels"),
        "destiny_number": _name_number(name, "all"),
        "personality_number": _name_number(name, "consonants"),
        "personal_year": personal_year,
        "personal_month": personal_month,
    }


# ---------- 22 arcana: central cross ----------
def _arc_name(n: Optional[int]) -> dict:
    if n is None:
        return {"value": None, "name": "", "keys": ""}
    info = _ARC["names"][str(n)]
    return {"value": n, "name": info["name"], "keys": info["keys"]}


def arcana_22(birth: date, today: Optional[date] = None) -> dict:
    """Центральный крест central_cross_v1 (формулы — в config/arcana.json)."""
    today = today or date.today()
    a = reduce22(birth.day)
    b = reduce22(birth.month)
    c = reduce22(sum_digits(birth.year))
    d = reduce22(a + b + c)
    center = reduce22(a + b + c + d)
    year_arcana = reduce22(birth.day + birth.month + sum_digits(today.year))

    return {
        "calculation_status": "calculated",
        "method": _ARC["method"],
        "cross": {
            "a_day": _arc_name(a),
            "b_month": _arc_name(b),
            "c_year": _arc_name(c),
            "d_sum": _arc_name(d),
            "center": _arc_name(center),
        },
        "core_arcana": _arc_name(center),
        "year_arcana": _arc_name(year_arcana),
    }


def compute_all(
    birth: date,
    name: str = "",
    today: Optional[date] = None,
    birth_time: Optional[str] = None,
    geo: Optional[dict] = None,
    gender: str = "",
) -> dict:
    """Все модули. Астро-модули считаются при наличии времени+места, иначе insufficient_input."""
    today = today or date.today()
    from . import astrology

    return {
        "numerology": numerology(birth, name, today),
        "arcana_22": arcana_22(birth, today),
        "western_astrology": astrology.western(birth, birth_time, geo),
        "transits": astrology.transits(birth, birth_time, geo, today),
        "jyotish": astrology.jyotish(birth, birth_time, geo, today),
        "bazi": astrology.bazi(birth, birth_time, geo, gender),
        "archetypes": {"primary": "", "secondary": "", "shadow": "", "growth_path": ""},
        "psychology_optional": {"enabled": False},
    }


if __name__ == "__main__":
    import sys

    d = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date(1990, 5, 15)
    nm = sys.argv[2] if len(sys.argv) > 2 else "Иван Иванов"
    print(json.dumps(compute_all(d, nm), ensure_ascii=False, indent=2))
