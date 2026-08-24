from __future__ import annotations

import logging
import os

import pytest
from pydantic import ValidationError

from toop.config import KNOWN_WEIGHT_KEYS, Settings, unknown_weight_keys
from toop.rating import INDICATORS


def test_defaults_load() -> None:
    s = Settings(_env_file=None)
    assert s.SNAPSHOT_HOUR == 12
    assert s.SESSION_WEEKDAY == "monday"
    # Poll posts Thursday 8pm PST, ahead of the following Monday session.
    assert s.SESSION_POLL_WEEKDAY == "thursday"
    assert pytest.approx(1.0) == sum(s.composite_weights().values())


def test_composite_weights_keyed_by_indicator() -> None:
    s = Settings(_env_file=None)
    assert set(s.composite_weights().keys()) == set(INDICATORS)


def test_normalization_defaults() -> None:
    s = Settings(_env_file=None)
    assert s.NORMALIZATION_ENABLED is True
    assert s.NORM_MIN_RATINGS == 8
    assert s.SHRINKAGE_K == 3.0


def test_norm_min_ratings_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, NORM_MIN_RATINGS=0)


def test_shrinkage_k_must_be_non_negative() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, SHRINKAGE_K=-1.0)


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


def test_lang_defaults_to_persian() -> None:
    assert Settings(_env_file=None).BOT_LANG == "fa"


def test_lang_normalizes_case() -> None:
    assert Settings(_env_file=None, BOT_LANG="EN").BOT_LANG == "en"


def test_invalid_lang_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, BOT_LANG="de")


def test_weights_not_summing_warns(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="toop.config"):
        Settings(_env_file=None, WEIGHT_ATTACK=0.5)
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


def test_skip_cooldown_default() -> None:
    assert Settings(_env_file=None).SKIP_COOLDOWN_DAYS == 7


def test_skip_cooldown_env_override() -> None:
    assert Settings(_env_file=None, SKIP_COOLDOWN_DAYS=14).SKIP_COOLDOWN_DAYS == 14


def test_skip_cooldown_rejects_zero() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, SKIP_COOLDOWN_DAYS=0)


def test_unknown_weight_keys_flags_retired_indicator(tmp_path, monkeypatch) -> None:
    """WEIGHT_DEFENSE sat in the production .env for months after the six-indicator
    migration; pydantic's extra="ignore" dropped it silently and left attack at 0.4.
    """
    monkeypatch.delenv("WEIGHT_DEFENSE", raising=False)
    env = tmp_path / ".env"
    env.write_text(
        "# a comment\nWEIGHT_ATTACK=0.1667\nWEIGHT_DEFENSE=0.4\nBOT_TOKEN=x\n",
        encoding="utf-8",
    )
    assert unknown_weight_keys(str(env)) == ["WEIGHT_DEFENSE"]


def test_unknown_weight_keys_clean_env_returns_nothing(tmp_path, monkeypatch) -> None:
    for key in list(os.environ):
        if key.upper().startswith("WEIGHT_"):
            monkeypatch.delenv(key, raising=False)
    env = tmp_path / ".env"
    env.write_text(
        "\n".join(f"{k}=0.1667" for k in sorted(KNOWN_WEIGHT_KEYS)) + "\n", encoding="utf-8"
    )
    assert unknown_weight_keys(str(env)) == []


def test_unknown_weight_keys_reads_the_environment_too(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("WEIGHT_SPIKE", "0.9")
    assert "WEIGHT_SPIKE" in unknown_weight_keys(str(tmp_path / "missing.env"))


def test_retired_weight_key_warns_at_startup(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The silent drop is the whole bug: surface it loudly instead."""
    monkeypatch.setenv("WEIGHT_DEFENSE", "0.4")
    with caplog.at_level(logging.WARNING, logger="toop.config"):
        Settings(_env_file=None)
    assert any(
        "WEIGHT_DEFENSE is set but is not a known indicator weight" in r.message
        for r in caplog.records
    )
