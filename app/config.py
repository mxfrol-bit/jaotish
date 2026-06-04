"""Конфигурация из окружения. Без секретов в коде."""
import os

# --- AI (OpenRouter, как в Planify) ---
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "anthropic/claude-sonnet-4.5")
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

# --- Supabase ---
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")  # service_role (серверный доступ)

# --- Telegram ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

# --- Генерация AI-обложек (Replicate, опционально) ---
REPLICATE_API_TOKEN = os.getenv("REPLICATE_API_TOKEN", "")
IMAGE_MODEL = os.getenv("IMAGE_MODEL", "black-forest-labs/flux-schnell")

# --- Прочее ---
REPORT_STYLE = os.getenv("REPORT_STYLE", "мягкий, на «ты», практичный")
DIAG_TOKEN = os.getenv("DIAG_TOKEN", "")  # для /_diag без логов Railway
METHOD_VERSION = "v0.1"


def ai_ready() -> bool:
    return bool(OPENROUTER_API_KEY)


def image_ready() -> bool:
    return bool(REPLICATE_API_TOKEN)


def db_ready() -> bool:
    return bool(SUPABASE_URL and SUPABASE_KEY)


def bot_ready() -> bool:
    return bool(TELEGRAM_BOT_TOKEN)
