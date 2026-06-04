"""Доступ к Supabase. Хранение профилей и обратной связи.

Если креды не заданы — модуль работает в no-op режиме (расчёт и отчёт всё равно отдаются).
"""
from __future__ import annotations

from typing import Any, Optional

from . import config

supabase = None
if config.db_ready():
    from supabase import create_client

    supabase = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)


def save_profile(profile: dict[str, Any], telegram_id: Optional[int] = None) -> Optional[str]:
    """Сохранить профиль, вернуть profile_id (или None, если БД не настроена)."""
    if supabase is None:
        return None
    row = {
        "profile_id": profile.get("profile_id"),
        "telegram_id": telegram_id,
        "name": profile.get("user_input", {}).get("name"),
        "birth_date": profile.get("user_input", {}).get("birth_date"),
        "analysis_type": profile.get("user_input", {}).get("analysis_type"),
        "method_version": profile.get("meta", {}).get("method_version"),
        "data": profile,
    }
    supabase.table("me_profiles").insert(row).execute()
    return profile.get("profile_id")


def get_profile(profile_id: str) -> Optional[dict[str, Any]]:
    if supabase is None:
        return None
    res = supabase.table("me_profiles").select("data").eq("profile_id", profile_id).limit(1).execute()
    return res.data[0]["data"] if res.data else None


def list_profiles(telegram_id: int, limit: int = 10) -> list[dict[str, Any]]:
    if supabase is None:
        return []
    res = (
        supabase.table("me_profiles")
        .select("profile_id,name,birth_date,analysis_type,created_at")
        .eq("telegram_id", telegram_id)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return res.data or []


def add_feedback(profile_id: str, text: str, rating: Optional[int] = None) -> None:
    if supabase is None:
        return
    supabase.table("me_feedback").insert(
        {"profile_id": profile_id, "text": text, "rating": rating}
    ).execute()
