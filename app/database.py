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


def get_user(telegram_id: int) -> Optional[dict[str, Any]]:
    """Сохранённые данные пользователя (имя, дата) — чтобы не вводить заново."""
    if supabase is None:
        return None
    res = supabase.table("me_users").select("*").eq("telegram_id", telegram_id).limit(1).execute()
    return res.data[0] if res.data else None


def upsert_user(telegram_id: int, fields: dict[str, Any]) -> None:
    """Создать/обновить данные пользователя."""
    if supabase is None:
        return
    from datetime import datetime, timezone

    row = {"telegram_id": telegram_id, "updated_at": datetime.now(timezone.utc).isoformat(), **fields}
    supabase.table("me_users").upsert(row, on_conflict="telegram_id").execute()


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


def find_recent_profile(telegram_id: int, analysis_type: str) -> Optional[dict[str, Any]]:
    """Последний сохранённый разбор этого типа — для кэша (не гонять AI повторно).
    Возвращает {data, created_at} или None."""
    if supabase is None:
        return None
    res = (
        supabase.table("me_profiles")
        .select("data,created_at")
        .eq("telegram_id", telegram_id)
        .eq("analysis_type", analysis_type)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    return res.data[0] if res.data else None


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


def add_partner(telegram_id: int, fields: dict[str, Any]) -> Optional[str]:
    """Сохранить партнёра для синастрии. Возвращает partner_id (или None без БД)."""
    import uuid

    partner_id = str(uuid.uuid4())
    if supabase is None:
        return partner_id
    row = {"partner_id": partner_id, "telegram_id": telegram_id, **fields}
    supabase.table("me_partners").insert(row).execute()
    return partner_id


def list_partners(telegram_id: int, limit: int = 20) -> list[dict[str, Any]]:
    if supabase is None:
        return []
    res = (
        supabase.table("me_partners")
        .select("partner_id,name,birth_date,birth_time,birth_place,lat,lon,timezone")
        .eq("telegram_id", telegram_id)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return res.data or []


def get_partner(partner_id: str) -> Optional[dict[str, Any]]:
    if supabase is None:
        return None
    res = supabase.table("me_partners").select("*").eq("partner_id", partner_id).limit(1).execute()
    return res.data[0] if res.data else None


def delete_partner(partner_id: str) -> None:
    if supabase is None:
        return
    supabase.table("me_partners").delete().eq("partner_id", partner_id).execute()


def set_cover(profile_id: str, url: str) -> None:
    """Запомнить URL AI-обложки в data профиля (чтобы не платить Replicate повторно)."""
    if supabase is None:
        return
    res = supabase.table("me_profiles").select("data").eq("profile_id", profile_id).limit(1).execute()
    if not res.data:
        return
    data = res.data[0]["data"] or {}
    data["cover_url"] = url
    supabase.table("me_profiles").update({"data": data}).eq("profile_id", profile_id).execute()


def log_error(
    kind: str,
    where: str = "",
    message: str = "",
    context: Optional[dict[str, Any]] = None,
    telegram_id: Optional[int] = None,
) -> None:
    """Записать баг/ошибку в me_errors. Никогда не падает сам (иначе скроет первичную ошибку)."""
    if supabase is None:
        return
    try:
        supabase.table("me_errors").insert(
            {
                "kind": kind,
                "where_": where[:200] if where else None,
                "message": (message or "")[:2000],
                "context": context,
                "telegram_id": telegram_id,
            }
        ).execute()
    except Exception:  # noqa: BLE001 — логирование ошибки не должно ронять основной поток
        import logging

        logging.exception("log_error failed")


def list_errors(limit: int = 100, only_unresolved: bool = False) -> list[dict[str, Any]]:
    if supabase is None:
        return []
    q = supabase.table("me_errors").select("*")
    if only_unresolved:
        q = q.eq("resolved", False)
    res = q.order("created_at", desc=True).limit(limit).execute()
    return res.data or []


def resolve_error(error_id: int) -> None:
    if supabase is None:
        return
    supabase.table("me_errors").update({"resolved": True}).eq("id", error_id).execute()


def error_stats() -> dict[str, int]:
    """Сводка для админки: всего и по типам (по последним записям)."""
    rows = list_errors(limit=500)
    stats: dict[str, int] = {"total": len(rows), "unresolved": 0}
    for r in rows:
        if not r.get("resolved"):
            stats["unresolved"] += 1
        k = r.get("kind") or "unknown"
        stats[k] = stats.get(k, 0) + 1
    return stats


def add_feedback(profile_id: str, text: str, rating: Optional[int] = None) -> None:
    if supabase is None:
        return
    supabase.table("me_feedback").insert(
        {"profile_id": profile_id, "text": text, "rating": rating}
    ).execute()
