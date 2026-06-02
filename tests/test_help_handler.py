from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from toop.handlers.help import handle_help


@pytest.fixture(autouse=True)
def patch_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("toop.handlers.help.settings", MagicMock(ADMIN_TELEGRAM_ID=42))


def _update(user_id: int | None) -> MagicMock:
    u = MagicMock()
    u.effective_user = None if user_id is None else MagicMock(id=user_id)
    msg = MagicMock()
    msg.reply_text = AsyncMock()
    u.effective_message = msg
    return u


async def test_help_admin_gets_full_list() -> None:
    update = _update(42)
    await handle_help(update, MagicMock())
    body = update.effective_message.reply_text.await_args.args[0]
    assert "/backup_db" in body
    assert "/start" in body


async def test_help_non_admin_gets_public_subset() -> None:
    update = _update(99)
    await handle_help(update, MagicMock())
    body = update.effective_message.reply_text.await_args.args[0]
    assert "/backup_db" not in body
    assert "/vote" in body


async def test_help_no_user_is_public() -> None:
    update = _update(None)
    await handle_help(update, MagicMock())
    body = update.effective_message.reply_text.await_args.args[0]
    assert "/backup_db" not in body
    assert "/help" in body


async def test_help_admin_unset_is_public(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("toop.handlers.help.settings", MagicMock(ADMIN_TELEGRAM_ID=0))
    update = _update(42)
    await handle_help(update, MagicMock())
    body = update.effective_message.reply_text.await_args.args[0]
    assert "/backup_db" not in body


async def test_help_returns_without_message() -> None:
    u = MagicMock()
    u.effective_user = MagicMock(id=42)
    u.effective_message = None
    await handle_help(u, MagicMock())
