from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

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
        SESSION_POLL_WEEKDAY="Thursday",
        SESSION_POLL_HOUR=20,
        SESSION_POLL_TZ="America/Los_Angeles",
        ADMIN_TELEGRAM_ID=42,
        GROUP_CHAT_ID=-100123,
    )
    monkeypatch.setattr(bot, "settings", fake_settings)
    monkeypatch.setattr(bot, "get_connection", lambda _path: MagicMock())
    monkeypatch.setattr(bot, "init_db", lambda _conn: None)

    mock_app = MagicMock()
    mock_app.bot_data = {}
    fake_application = MagicMock()
    # builder().token().post_init().build() returns the mock app.
    builder = fake_application.builder.return_value
    builder.token.return_value.post_init.return_value.build.return_value = mock_app
    monkeypatch.setattr(bot, "Application", fake_application)
    mock_app._builder = builder
    return mock_app


def test_main_registers_all_handlers_and_schedules_snapshot(
    patched_main: MagicMock,
) -> None:
    mock_app = patched_main
    mock_app.job_queue = MagicMock()

    bot.main()

    # 29 command + 15 callback-query + 1 poll-answer + 4 message handlers.
    assert mock_app.add_handler.call_count == 49
    assert mock_app.job_queue.run_daily.call_count == 3
    # Jobs must be scheduled on PTB's weekday numbering (0=Sunday..6=Saturday),
    # NOT datetime's Monday=0 — else every job fires a day early. With the fake
    # settings (session Monday, poll Thursday): snapshot→1 (Mon), poll→4 (Thu).
    days_by_name = {
        call.kwargs["name"]: call.kwargs["days"]
        for call in mock_app.job_queue.run_daily.call_args_list
        if "days" in call.kwargs
    }
    assert days_by_name["auto_snapshot"] == (1,)
    assert days_by_name["attendance_poll"] == (4,)
    assert "conn" in mock_app.bot_data
    assert "started_at" in mock_app.bot_data
    # The command-registration hook is wired via post_init.
    mock_app._builder.token.return_value.post_init.assert_called_once_with(bot._register_commands)
    mock_app.run_polling.assert_called_once()


def test_main_skips_scheduling_when_no_job_queue(patched_main: MagicMock) -> None:
    mock_app = patched_main
    mock_app.job_queue = None

    bot.main()

    # Handlers still register even without a job queue.
    assert mock_app.add_handler.call_count == 49
    mock_app.run_polling.assert_called_once()


async def test_register_commands_sets_default_and_admin_scopes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bot, "settings", MagicMock(ADMIN_TELEGRAM_ID=42))
    app = MagicMock()
    app.bot.set_my_commands = AsyncMock()

    await bot._register_commands(app)

    # One call for the default (public) scope, one for the admin chat scope.
    assert app.bot.set_my_commands.await_count == 2
    public_call, admin_call = app.bot.set_my_commands.await_args_list
    public_cmds = public_call.args[0]
    admin_cmds = admin_call.args[0]
    public_names = {c.command for c in public_cmds}
    admin_names = {c.command for c in admin_cmds}
    assert public_names == {"start", "vote", "help"}
    # Admin scope shows the full list, including the public commands.
    assert public_names <= admin_names
    assert "backup_db" in admin_names
    assert admin_call.kwargs["scope"].chat_id == 42


async def test_register_commands_skips_admin_scope_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bot, "settings", MagicMock(ADMIN_TELEGRAM_ID=0))
    app = MagicMock()
    app.bot.set_my_commands = AsyncMock()

    await bot._register_commands(app)

    # Only the default scope is registered when no admin is configured.
    assert app.bot.set_my_commands.await_count == 1


def test_registered_commands_match_the_command_list(patched_main: MagicMock) -> None:
    from telegram.ext import CommandHandler

    from toop.commands import COMMANDS

    mock_app = patched_main
    mock_app.job_queue = None

    bot.main()

    registered: set[str] = set()
    for call in mock_app.add_handler.call_args_list:
        handler = call.args[0]
        if isinstance(handler, CommandHandler):
            registered |= set(handler.commands)
    assert registered == {c.name for c in COMMANDS}


def test_dunder_main_module_imports() -> None:
    # Importing the module executes its top-level import line (line 1);
    # the __main__ guard is excluded via pragma.
    import toop.__main__ as entry

    assert entry.main is bot.main
