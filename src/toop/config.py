from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


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
    SNAPSHOT_HOUR: int = 12
    SESSION_WEEKDAY: str = "monday"
    MAX_ATTENDEES: int = 14
    WEIGHT_ATTACK: float = 0.4
    WEIGHT_DEFENSE: float = 0.4
    WEIGHT_SETTING: float = 0.2
    CALIBRATION_THRESHOLD: int = 15
    QUEUE_DEPTH: int = 5
    DATABASE_PATH: str = "data/toop.db"


settings = Settings()
