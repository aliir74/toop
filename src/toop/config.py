from __future__ import annotations

import logging
import math
import os
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

VALID_WEEKDAYS = {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}
VALID_LANGS = {"fa", "en"}

# The six indicators that actually have a WEIGHT_ setting. Anything else matching
# WEIGHT_* in the environment or .env is a leftover from an older rating model.
_WEIGHT_INDICATORS = ("attack", "receive", "block", "setting", "serve", "positioning")
KNOWN_WEIGHT_KEYS = frozenset(f"WEIGHT_{ind.upper()}" for ind in _WEIGHT_INDICATORS)


def unknown_weight_keys(env_file: str = ".env") -> list[str]:
    """WEIGHT_* keys present in the environment or .env that no longer map to an
    indicator. `Settings` is configured with extra="ignore", so pydantic drops these
    without a word: WEIGHT_DEFENSE=0.4 sat in the production .env for months after the
    move to six indicators, silently leaving attack at 0.4 (2.4x every other skill)
    while receive/block/serve/positioning fell back to their defaults. Surfacing them
    at startup is what stops that recurring.
    """
    names = {k for k in os.environ if k.upper().startswith("WEIGHT_")}
    path = Path(env_file)
    if path.is_file():
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line.startswith("#") or "=" not in line:
                continue
            key = line.split("=", 1)[0].strip().upper()
            if key.startswith("WEIGHT_"):
                names.add(key)
    return sorted(n for n in names if n.upper() not in KNOWN_WEIGHT_KEYS)


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
    # Active language for all user-facing bot text. Persian by default (the group
    # is Iranian); set BOT_LANG=en for English. Command NAMES stay latin
    # regardless. Named BOT_LANG (not LANG) to avoid the POSIX LANG env var.
    BOT_LANG: str = "fa"
    SNAPSHOT_HOUR: int = Field(default=12, ge=0, le=23)
    SESSION_WEEKDAY: str = "monday"
    MAX_ATTENDEES: int = Field(default=14, gt=0)
    # Attendance-poll schedule: the bot posts the weekly بلی/خیر poll on this
    # weekday + hour in SESSION_POLL_TZ (the group's real Thursday-8pm-PST
    # cadence). QUORUM_THRESHOLD is the yes-count at or above which the bot
    # announces the session is on and posts payment; MAX_ATTENDEES is the cap
    # that closes the poll and opens the reservation/waitlist poll.
    SESSION_POLL_WEEKDAY: str = "thursday"
    SESSION_POLL_HOUR: int = Field(default=20, ge=0, le=23)
    SESSION_POLL_TZ: str = "America/Los_Angeles"
    QUORUM_THRESHOLD: int = Field(default=10, ge=0)
    # Payment block posted once quorum is reached (interpolated into the Persian
    # announcement). Left blank by default; fill in .env for the live group.
    PAYMENT_EMAIL: str = ""
    PAYMENT_AMOUNT: str = "7.5"
    ACCOUNTING_SHEET_URL: str = ""
    # Composite weights, one per indicator. Default = equal (1/6 each, summing to
    # 1.0). Env-tunable; they need not sum to 1.0 (a warning logs if they don't).
    WEIGHT_ATTACK: float = 0.1667
    WEIGHT_RECEIVE: float = 0.1667
    WEIGHT_BLOCK: float = 0.1667
    WEIGHT_SETTING: float = 0.1667
    WEIGHT_SERVE: float = 0.1666
    WEIGHT_POSITIONING: float = 0.1666
    CALIBRATION_THRESHOLD: int = Field(default=15, ge=0)
    # Rater-normalization tuning. A rater needs at least NORM_MIN_RATINGS scores
    # before we trust their own mean/stdev; below that we fall back to a global
    # shift. SHRINKAGE_K pseudo-observations pull sparsely-rated players toward
    # the global mean. NORMALIZATION_ENABLED toggles the whole pass off.
    NORMALIZATION_ENABLED: bool = True
    NORM_MIN_RATINGS: int = Field(default=8, ge=1)
    SHRINKAGE_K: float = Field(default=3.0, ge=0.0)
    DATABASE_PATH: str = "data/toop.db"
    # Where /set_photo backs up the original uploaded image bytes (one JPEG per
    # player, named <telegram_id>.jpg). file_ids are bound to the bot, so these
    # bytes are the recovery copy if the bot is ever recreated from scratch.
    PHOTOS_DIR: str = "data/photos"
    # Don't-know alert: flag a player to the admin when their skip count is at
    # least DK_ALERT_MIN_PROMPTS AND their skip rate is at least DK_ALERT_RATE.
    DK_ALERT_MIN_PROMPTS: int = Field(default=10, ge=0)
    DK_ALERT_RATE: float = Field(default=0.5, ge=0.0, le=1.0)
    DEFAULT_PAUSE_DAYS: int = Field(default=14, gt=0)
    # A 🤷 ندیدمش tap means "I have not seen this person play", so it hides that
    # whole player from that voter (every indicator) for this many days.
    SKIP_COOLDOWN_DAYS: int = Field(default=7, gt=0)

    @field_validator("SESSION_WEEKDAY", "SESSION_POLL_WEEKDAY")
    @classmethod
    def _weekday_valid(cls, v: str) -> str:
        lower = v.lower()
        if lower not in VALID_WEEKDAYS:
            raise ValueError(f"weekday must be one of {sorted(VALID_WEEKDAYS)}, got {v!r}")
        return lower

    @field_validator("BOT_LANG")
    @classmethod
    def _lang_valid(cls, v: str) -> str:
        lower = v.lower()
        if lower not in VALID_LANGS:
            raise ValueError(f"BOT_LANG must be one of {sorted(VALID_LANGS)}, got {v!r}")
        return lower

    @model_validator(mode="after")
    def _weights_sum_to_one(self) -> Settings:
        total = sum(self.composite_weights().values())
        if not math.isclose(total, 1.0, abs_tol=1e-6):
            logger.warning(
                "Composite weights sum to %.4f, not 1.0 — ratings will be scaled accordingly", total
            )
        for name in unknown_weight_keys():
            logger.warning(
                "%s is set but is not a known indicator weight — it is being IGNORED. "
                "Valid keys: %s",
                name,
                ", ".join(sorted(KNOWN_WEIGHT_KEYS)),
            )
        return self

    def composite_weights(self) -> dict[str, float]:
        """Indicator → weight, the single source for the composite weight vector."""
        return {
            "attack": self.WEIGHT_ATTACK,
            "receive": self.WEIGHT_RECEIVE,
            "block": self.WEIGHT_BLOCK,
            "setting": self.WEIGHT_SETTING,
            "serve": self.WEIGHT_SERVE,
            "positioning": self.WEIGHT_POSITIONING,
        }

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
