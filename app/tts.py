"""Озвучка разборов через Edge-TTS (нейроголоса Microsoft, бесплатно, без ключа).

Возвращает MP3-байты. Перед синтезом чистим Markdown, чтобы голос не читал «решётки»
и «звёздочки». Длинные тексты режем по лимиту, чтобы аудио не было бесконечным.
"""
from __future__ import annotations

import re

import edge_tts

VOICE = "ru-RU-SvetlanaNeural"   # спокойный женский; альт: ru-RU-DmitryNeural (мужской)
_MAX_CHARS = 6000


def _strip_markdown(md: str) -> str:
    """Markdown → чистый текст для голоса."""
    text = md or ""
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", text)     # заголовки ## …
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)            # **жирный**
    text = re.sub(r"\*(.+?)\*", r"\1", text)                # *курсив*
    text = re.sub(r"(?m)^\s*[-•]\s*", "", text)             # маркеры списка
    text = text.replace("---", "")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


async def synth(md: str, voice: str = VOICE) -> bytes:
    """Сгенерировать MP3-озвучку из Markdown-текста. Пустой текст → b''."""
    text = _strip_markdown(md)[:_MAX_CHARS]
    if not text:
        return b""
    communicate = edge_tts.Communicate(text, voice)
    buf = bytearray()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            buf.extend(chunk["data"])
    return bytes(buf)
