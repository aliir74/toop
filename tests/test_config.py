from __future__ import annotations

import logging

import pytest
from pydantic import ValidationError

from toop.config import Settings


def test_defaults_load() -> None:
    s = Settings(_env_file=None)
    assert s.SNAPSHOT_HOUR == 12
    assert s.SESSION_WEEKDAY == "monday"
    assert pytest.approx(1.0) == s.WEIGHT_ATTACK + s.WEIGHT_DEFENSE + s.WEIGHT_SETTING


def test_dk_alert_defaults() -> None:
    s = Settings(_env_file=None)
    assert s.DK_ALERT_MIN_PROMPTS == 10
    assert s.DK_ALERT_RATE == 0.5
    assert s.DEFAULT_PAUSE_DAYS == 14


def test_dk_alert_env_overrides() -> None:
    s = Settings(_env_file=None, DK_ALERT_MIN_PROMPTS=20, DK_ALERT_RATE=0.7, DEFAULT_PAUSE_DAYS=30)
    assert s.DK_ALERT_MIN_PROMPTS == 20
    assert s.DK_ALERT_RATE == 0.7
    assert s.DEFAULT_PAUSE_DAYS == 30


def test_dk_alert_rate_out_of_range() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, DK_ALERT_RATE=1.5)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, DK_ALERT_RATE=-0.1)


def test_dk_alert_min_prompts_negative() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, DK_ALERT_MIN_PROMPTS=-1)


def test_snapshot_hour_out_of_range() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, SNAPSHOT_HOUR=24)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, SNAPSHOT_HOUR=-1)


def test_invalid_weekday() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, SESSION_WEEKDAY="funday")


def test_weights_not_summing_warns(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="toop.config"):
        Settings(_env_file=None, WEIGHT_ATTACK=0.5, WEIGHT_DEFENSE=0.5, WEIGHT_SETTING=0.5)
    assert any("Composite weights sum to" in r.message for r in caplog.records)


def test_require_runtime_raises_when_missing() -> None:
    s = Settings(_env_file=None)
    with pytest.raises(RuntimeError, match="BOT_TOKEN"):
        s.require_runtime()


def test_require_runtime_passes_when_set() -> None:
    s = Settings(
        _env_file=None,
        BOT_TOKEN="abc",
        ADMIN_TELEGRAM_ID=1,
        GROUP_CHAT_ID=-100,
    )
    s.require_runtime()  # should not raise
