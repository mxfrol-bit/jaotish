"""HTTP API для веб-интерфейса. _ver/_diag — для проверки прода без логов Railway."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from .. import config, database
from ..engine import build_profile
from ..models import ProfileRequest

PROFILES_VER = "profiles-0.1.0"

router = APIRouter(prefix="/api/profiles", tags=["profiles"])


@router.get("/_ver")
def ver() -> dict:
    return {"ver": PROFILES_VER, "method_version": config.METHOD_VERSION}


@router.get("/_diag")
def diag(token: str = Query(...)) -> dict:
    if not config.DIAG_TOKEN or token != config.DIAG_TOKEN:
        raise HTTPException(status_code=403, detail="bad token")
    return {
        "ver": PROFILES_VER,
        "ai_ready": config.ai_ready(),
        "ai_model": config.OPENROUTER_MODEL,
        "db_ready": config.db_ready(),
        "bot_ready": config.bot_ready(),
    }


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
