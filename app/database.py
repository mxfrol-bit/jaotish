"""Доступ к Supabase. Хранение профилей и обратной связи.

Если креды не заданы ИЛИ база недоступна (пауза проекта, сеть, сбой) — модуль работает
в no-op режиме: расчёт и отчёт всё равно отдаются, теряется только память (кэш, история).
Ни один вызов отсюда не должен ронять бота.
"""
from __future__ import annotations

import functools
import logging
import time
from collections import OrderedDict
from typing import Any, Optional

from . import config

log = logging.getLogger("matrix-engine.db")

# Таймаут запроса: с упавшей БД лучше быстро отвалиться, чем держать хендлер бота.
DB_TIMEOUT = 10

supabase = None
if config.db_ready():
    from supabase import ClientOptions, create_client

    try:
        supabase = create_client(
            config.SUPABASE_URL,
            config.SUPABASE_KEY,
            options=ClientOptions(postgrest_client_timeout=DB_TIMEOUT),
        )
    except Exception as exc:  # noqa: BLE001 — битый URL/ключ не должен ронять весь сервис
        logging.getLogger("matrix-engine.db").error(
            "Не удалось создать клиент Supabase (%s: %s) — работаем без БД", type(exc).__name__, exc
        )

# --- предохранитель: после сбоя не долбим мёртвую базу каждым запросом ---
_FAIL_COOLDOWN = 60.0  # сек
_down_until = 0.0
_last_db_error = ""


def _note_failure(where: str, exc: BaseException) -> None:
    global _down_until, _last_db_error
    _last_db_error = f"{type(exc).__name__}: {exc}"[:300]
    _down_until = time.monotonic() + _FAIL_COOLDOWN
    log.warning("БД недоступна (%s): %s", where, _last_db_error)


def _safe(default):
    """Сбой БД → функция отдаёт значение «как будто БД не настроена», а не исключение.

    `default` — либо готовое значение, либо фабрика (для list/dict).
    """

    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            global _down_until
            if supabase is None or time.monotonic() < _down_until:
                return default() if callable(default) else default
            try:
                out = fn(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 — БД не должна ронять поток
                _note_failure(fn.__name__, exc)
                return default() if callable(default) else default
            _down_until = 0.0
            return out

        return wrapper

    return deco


# --- запасной кэш профилей в памяти процесса (работает, пока БД лежит) ---
_MEM_LIMIT = 100
_MEM_PROFILES: "OrderedDict[str, dict[str, Any]]" = OrderedDict()


def _mem_put(profile_id: Optional[str], profile: dict[str, Any]) -> None:
    if not profile_id:
        return
    _MEM_PROFILES[profile_id] = profile
    _MEM_PROFILES.move_to_end(profile_id)
    while len(_MEM_PROFILES) > _MEM_LIMIT:
        _MEM_PROFILES.popitem(last=False)


def db_status() -> dict[str, Any]:
    """Реальная проверка доступности БД (не «заданы ли переменные») — для /status и /_diag."""
    if supabase is None:
        return {"configured": False, "alive": False, "error": "SUPABASE_URL/SUPABASE_KEY не заданы"}
    if time.monotonic() < _down_until:
        return {"configured": True, "alive": False, "error": _last_db_error}
    try:
        supabase.table("me_users").select("telegram_id").limit(1).execute()
    except Exception as exc:  # noqa: BLE001
        _note_failure("db_status", exc)
        return {"configured": True, "alive": False, "error": _last_db_error}
    return {"configured": True, "alive": True, "error": ""}


@_safe(None)
def get_user(telegram_id: int) -> Optional[dict[str, Any]]:
    """Сохранённые данные пользователя (имя, дата) — чтобы не вводить заново."""
    if supabase is None:
        return None
    res = supabase.table("me_users").select("*").eq("telegram_id", telegram_id).limit(1).execute()
    return res.data[0] if res.data else None


@_safe(None)
def upsert_user(telegram_id: int, fields: dict[str, Any]) -> None:
    """Создать/обновить данные пользователя."""
    if supabase is None:
        return
    from datetime import datetime, timezone

    row = {"telegram_id": telegram_id, "updated_at": datetime.now(timezone.utc).isoformat(), **fields}
    supabase.table("me_users").upsert(row, on_conflict="telegram_id").execute()


def save_profile(profile: dict[str, Any], telegram_id: Optional[int] = None) -> Optional[str]:
    """Сохранить профиль, вернуть profile_id. В память — всегда, в БД — если она жива."""
    pid = profile.get("profile_id")
    _mem_put(pid, profile)
    _save_profile_db(profile, telegram_id)
    return pid


@_safe(None)
def _save_profile_db(profile: dict[str, Any], telegram_id: Optional[int] = None) -> Optional[str]:
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
    """Профиль из БД; если БД недоступна — из кэша процесса."""
    data = _get_profile_db(profile_id)
    return data if data is not None else _MEM_PROFILES.get(profile_id)


@_safe(None)
def _get_profile_db(profile_id: str) -> Optional[dict[str, Any]]:
    if supabase is None:
        return None
    res = supabase.table("me_profiles").select("data").eq("profile_id", profile_id).limit(1).execute()
    return res.data[0]["data"] if res.data else None


@_safe(None)
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


@_safe(list)
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
    if supabase is None or time.monotonic() < _down_until:
        return partner_id
    row = {"partner_id": partner_id, "telegram_id": telegram_id, **fields}
    try:
        supabase.table("me_partners").insert(row).execute()
    except Exception as exc:  # noqa: BLE001 — партнёр просто не сохранится
        _note_failure("add_partner", exc)
    return partner_id


@_safe(list)
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


@_safe(None)
def get_partner(partner_id: str) -> Optional[dict[str, Any]]:
    if supabase is None:
        return None
    res = supabase.table("me_partners").select("*").eq("partner_id", partner_id).limit(1).execute()
    return res.data[0] if res.data else None


@_safe(None)
def delete_partner(partner_id: str) -> None:
    if supabase is None:
        return
    supabase.table("me_partners").delete().eq("partner_id", partner_id).execute()


@_safe(None)
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


@_safe(None)
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


@_safe(list)
def list_errors(limit: int = 100, only_unresolved: bool = False) -> list[dict[str, Any]]:
    if supabase is None:
        return []
    q = supabase.table("me_errors").select("*")
    if only_unresolved:
        q = q.eq("resolved", False)
    res = q.order("created_at", desc=True).limit(limit).execute()
    return res.data or []


@_safe(None)
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


@_safe(None)
def add_feedback(profile_id: str, text: str, rating: Optional[int] = None) -> None:
    if supabase is None:
        return
    supabase.table("me_feedback").insert(
        {"profile_id": profile_id, "text": text, "rating": rating}
    ).execute()
