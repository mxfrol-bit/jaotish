"""HTTP API для веб-интерфейса. _ver/_diag — для проверки прода без логов Railway."""
from __future__ import annotations

import requests
from fastapi import APIRouter, HTTPException, Query

from .. import config, database
from ..engine import build_profile
from ..models import ProfileRequest

PROFILES_VER = "profiles-0.2.2"

router = APIRouter(prefix="/api/profiles", tags=["profiles"])


@router.get("/_ver")
def ver() -> dict:
    return {"ver": PROFILES_VER, "method_version": config.METHOD_VERSION}


@router.get("/_diag")
def diag(token: str = Query(...), ping: bool = Query(False)) -> dict:
    if not config.DIAG_TOKEN or token != config.DIAG_TOKEN:
        raise HTTPException(status_code=403, detail="bad token")
    out = {
        "ver": PROFILES_VER,
        "ai_ready": config.ai_ready(),
        "ai_model": config.OPENROUTER_MODEL,
        "db_ready": config.db_ready(),
        "db": database.db_status(),
        "bot_ready": config.bot_ready(),
    }
    if ping:
        out["ai_ping"] = _ping_openrouter()
    return out


def _ping_openrouter() -> dict:
    """Живой тест AI: реальный мини-запрос, чтобы увидеть точную ошибку (баланс/слаг модели)."""
    if not config.ai_ready():
        return {"ok": False, "error": "OPENROUTER_API_KEY не задан"}
    try:
        r = requests.post(
            f"{config.OPENROUTER_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {config.OPENROUTER_API_KEY}", "X-Title": "Matrix Engine"},
            json={"model": config.OPENROUTER_MODEL, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 5},
            timeout=30,
        )
    except requests.RequestException as e:
        return {"ok": False, "error": f"network: {e}"}
    return {"ok": r.status_code == 200, "status": r.status_code, "body": r.text[:300]}


@router.post("")
def create(req: ProfileRequest) -> dict:
    profile = build_profile(req)
    pid = database.save_profile(profile.model_dump(mode="json"))
    data = profile.model_dump(mode="json")
    if pid:
        data["profile_id"] = pid
    return data


@router.get("/{profile_id}")
def get(profile_id: str) -> dict:
    data = database.get_profile(profile_id)
    if data is None:
        raise HTTPException(status_code=404, detail="not found")
    return data
