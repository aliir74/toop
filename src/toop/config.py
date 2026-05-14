from __future__ import annotations

import logging
import math

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

VALID_WEEKDAYS = {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    BOT_TOKEN: str = ""
    ADMIN_TELEGRAM_ID: int = 0
    GROUP_CHAT_ID: int = 0
    SNAPSHOT_HOUR: int = Field(default=12, ge=0, le=23)
    SESSION_WEEKDAY: str = "monday"
    MAX_ATTENDEES: int = Field(default=14, gt=0)
    WEIGHT_ATTACK: float = 0.4
    WEIGHT_DEFENSE: float = 0.4
    WEIGHT_SETTING: float = 0.2
    CALIBRATION_THRESHOLD: int = Field(default=15, ge=0)
    QUEUE_DEPTH: int = Field(default=5, gt=0)
    DATABASE_PATH: str = "data/toop.db"

    @field_validator("SESSION_WEEKDAY")
    @classmethod
    def _weekday_valid(cls, v: str) -> str:
        lower = v.lower()
        if lower not in VALID_WEEKDAYS:
            raise ValueError(f"SESSION_WEEKDAY must be one of {sorted(VALID_WEEKDAYS)}, got {v!r}")
        return lower

    @model_validator(mode="after")
    def _weights_sum_to_one(self) -> Settings:
        total = self.WEIGHT_ATTACK + self.WEIGHT_DEFENSE + self.WEIGHT_SETTING
        if not math.isclose(total, 1.0, abs_tol=1e-6):
            logger.warning(
                "Composite weights sum to %.4f, not 1.0 — ratings will be scaled accordingly", total
            )
        return self

    def require_runtime(self) -> None:
        """Raise if any field that's optional at import-time is missing at startup."""
        missing = []
        if not self.BOT_TOKEN:
            missing.append("BOT_TOKEN")
        if self.ADMIN_TELEGRAM_ID == 0:
            missing.append("ADMIN_TELEGRAM_ID")
        if self.GROUP_CHAT_ID == 0:
            missing.append("GROUP_CHAT_ID")
        if missing:
            raise RuntimeError(f"Missing required env vars: {', '.join(missing)}")


settings = Settings()
