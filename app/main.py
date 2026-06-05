"""FastAPI + Telegram-бот в одном процессе (как Planify).

Бот работает в режиме polling, запускается в lifespan приложения.
Если TELEGRAM_BOT_TOKEN не задан — API поднимается без бота.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import config
from .routers import profiles, web

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("matrix-engine")

_tg_app = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _tg_app
    if config.bot_ready():
        from .bot import build_application

        _tg_app = build_application()
        await _tg_app.initialize()
        await _tg_app.start()
        await _tg_app.updater.start_polling(drop_pending_updates=True)
        log.info("Telegram bot polling started")
    else:
        log.warning("TELEGRAM_BOT_TOKEN not set — bot disabled, API only")

    yield

    if _tg_app is not None:
        await _tg_app.updater.stop()
        await _tg_app.stop()
        await _tg_app.shutdown()
        log.info("Telegram bot stopped")


app = FastAPI(title="Matrix Engine", version=config.METHOD_VERSION, lifespan=lifespan)
app.include_router(profiles.router)
app.include_router(web.router)  # "/" (лендинг+форма), "/report", "/admin"


@app.get("/status")
def status() -> dict:
    return {
        "service": "matrix-engine",
        "method_version": config.METHOD_VERSION,
        "ai_ready": config.ai_ready(),
        "db_ready": config.db_ready(),
        "bot_ready": config.bot_ready(),
    }


@app.get("/health")
def health() -> dict:
    return {"ok": True}
