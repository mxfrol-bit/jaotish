"""Pydantic-модели профиля Matrix Engine (схема — Слайд 7 методички)."""
from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class CalculationStatus(str, Enum):
    calculated = "calculated"
    manual_input_required = "manual_input_required"
    not_connected = "calculation_module_not_connected"
    insufficient_input = "insufficient_input"


class AnalysisType(str, Enum):
    personality = "personality"
    compatibility = "compatibility"
    work = "work"
    current_period = "current_period"
    event = "event"  # конкретная дата/сделка: что она активирует + вердикт «стоит ли»


class UserInput(BaseModel):
    name: str = ""
    gender: str = ""
    birth_date: date
    birth_time: Optional[str] = None
    birth_place: Optional[str] = None
    current_city: Optional[str] = None
    event_date: Optional[date] = None  # дата события/сделки (для analysis_type=event)
    main_request: str = ""
    analysis_type: AnalysisType = AnalysisType.personality


class ProfileRequest(BaseModel):
    """Вход от пользователя/бота для одного разбора."""
    name: str = Field(default="", description="Имя или ФИО")
    gender: str = ""
    birth_date: date
    birth_time: Optional[str] = None
    birth_place: Optional[str] = None
    # Предрассчитанные координаты/таймзона (бот хранит их, чтобы не геокодить каждый раз).
    lat: Optional[float] = None
    lon: Optional[float] = None
    timezone: Optional[str] = None
    main_request: str = ""
    analysis_type: AnalysisType = AnalysisType.personality


class Profile(BaseModel):
    profile_id: str = ""
    schema_version: str = "1.0"
    user_input: UserInput
    geo: dict[str, Any] = Field(default_factory=lambda: {"lat": None, "lon": None, "timezone": ""})
    calculation_modules: dict[str, Any] = Field(default_factory=dict)
    synthesis: dict[str, Any] = Field(default_factory=dict)
    report: dict[str, str] = Field(default_factory=lambda: {"short_summary": "", "full_report": "", "action_plan": ""})
    meta: dict[str, Any] = Field(default_factory=dict)
