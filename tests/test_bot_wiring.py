from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import toop.bot as bot


@pytest.fixture
def patched_main(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Patch every runtime dependency of bot.main() and return the mock app."""
    fake_settings = MagicMock(
        BOT_TOKEN="token",
        DATABASE_PATH="/tmp/toop-test.db",
        SESSION_WEEKDAY="Monday",
        SNAPSHOT_HOUR=12,
        ADMIN_TELEGRAM_ID=42,
        GROUP_CHAT_ID=-100123,
    )
    monkeypatch.setattr(bot, "settings", fake_settings)
    monkeypatch.setattr(bot, "get_connection", lambda _path: MagicMock())
    monkeypatch.setattr(bot, "init_db", lambda _conn: None)

    mock_app = MagicMock()
    mock_app.bot_data = {}
    fake_application = MagicMock()
    fake_application.builder.return_value.token.return_value.build.return_value = mock_app
    monkeypatch.setattr(bot, "Application", fake_application)
    return mock_app


def test_main_registers_all_handlers_and_schedules_snapshot(
    patched_main: MagicMock,
) -> None:
    mock_app = patched_main
    mock_app.job_queue = MagicMock()

    bot.main()

    # 25 command handlers + 3 callback-query handlers + 1 message handler.
    assert mock_app.add_handler.call_count == 29
    mock_app.job_queue.run_daily.assert_called_once()
    assert "conn" in mock_app.bot_data
    assert "started_at" in mock_app.bot_data
    mock_app.run_polling.assert_called_once()


def test_main_skips_scheduling_when_no_job_queue(patched_main: MagicMock) -> None:
    mock_app = patched_main
    mock_app.job_queue = None

    bot.main()

    # Handlers still register even without a job queue.
    assert mock_app.add_handler.call_count == 29
    mock_app.run_polling.assert_called_once()


def test_dunder_main_module_imports() -> None:
    # Importing the module executes its top-level import line (line 1);
    # the __main__ guard is excluded via pragma.
    import toop.__main__ as entry

    assert entry.main is bot.main
