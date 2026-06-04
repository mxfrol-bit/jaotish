"""AI-обложка образа через Replicate (Flux). Опционально: без токена — мягкая заглушка.

Промпт собирается из УЖЕ посчитанных полей (доминирующая стихия, знак Солнца, ядро
арканов) в абстрактно-редакционном, чистом стиле — без зодиакального китча и мистики.
"""
from __future__ import annotations

import time
from typing import Any, Optional

import requests

from . import config

# Палитра-настроение по стихии (для промпта, не для рендера).
_ELEMENT_MOOD = {
    "огонь": "warm amber and terracotta palette, energetic",
    "земля": "olive, sand and clay palette, grounded and solid",
    "воздух": "soft pale blue and grey palette, light and airy",
    "вода": "deep teal and indigo palette, calm and fluid",
}


def _modules_of(profile_data: dict) -> dict:
    mods = profile_data.get("calculation_modules") or {}
    return mods.get("person_a", mods)


def _build_prompt(profile_data: dict) -> str:
    mods = _modules_of(profile_data)
    west = mods.get("western_astrology") or {}
    arc = mods.get("arcana_22") or {}
    element = west.get("dominant_element") or ""
    mood = _ELEMENT_MOOD.get(element, "muted minimal palette")
    keys = (arc.get("core_arcana") or {}).get("keys") or ""
    return (
        "Minimalist editorial abstract portrait, clean modern composition, "
        f"{mood}. Subtle geometric forms, soft light, premium magazine aesthetic, "
        "calm and professional. No text, no letters, no zodiac symbols, no astrology cliches. "
        f"Mood keywords: {keys}." if keys else
        "Minimalist editorial abstract portrait, clean modern composition, "
        f"{mood}. Subtle geometric forms, soft light, premium magazine aesthetic. "
        "No text, no letters, no zodiac symbols."
    )


def cover(profile_data: dict) -> tuple[Optional[str], str]:
    """Сгенерировать обложку. Возвращает (url | None, текст-подпись/причина)."""
    if not config.image_ready():
        return None, "AI-обложка не настроена (задай REPLICATE_API_TOKEN в окружении)."

    prompt = _build_prompt(profile_data)
    headers = {
        "Authorization": f"Token {config.REPLICATE_API_TOKEN}",
        "Content-Type": "application/json",
        "Prefer": "wait",  # Replicate подождёт результат до ~60с и вернёт его сразу
    }
    body = {"input": {"prompt": prompt, "aspect_ratio": "1:1", "output_format": "png"}}
    url = f"https://api.replicate.com/v1/models/{config.IMAGE_MODEL}/predictions"
    try:
        resp = requests.post(url, headers=headers, json=body, timeout=90)
    except requests.RequestException as e:
        return None, f"сеть/таймаут Replicate: {e}"
    if resp.status_code not in (200, 201):
        return None, f"Replicate {resp.status_code}: {resp.text[:200]}"

    data = resp.json()
    out = _extract_output(data)
    if out:
        return out, "Обложка по твоему расчётному профилю 🎨"

    # если не дождались синхронно — добираем по ссылке get
    get_url = (data.get("urls") or {}).get("get")
    for _ in range(20):
        if not get_url:
            break
        time.sleep(1.5)
        try:
            poll = requests.get(get_url, headers=headers, timeout=30).json()
        except (requests.RequestException, ValueError):
            break
        if poll.get("status") == "succeeded":
            out = _extract_output(poll)
            if out:
                return out, "Обложка по твоему расчётному профилю 🎨"
        if poll.get("status") in ("failed", "canceled"):
            return None, "Replicate не справился — попробуй ещё раз."
    return None, "Обложка ещё генерится — попробуй через минуту."


def _extract_output(data: dict[str, Any]) -> Optional[str]:
    out = data.get("output")
    if isinstance(out, list) and out:
        return out[0]
    if isinstance(out, str):
        return out
    return None
