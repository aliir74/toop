from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from toop.admin import ADMIN_REJECT_MESSAGE, require_admin


def _mock_update(user_id: int | None) -> MagicMock:
    update = MagicMock()
    if user_id is None:
        update.effective_user = None
    else:
        update.effective_user = MagicMock(id=user_id)
    update.effective_message = MagicMock()
    update.effective_message.reply_text = AsyncMock()
    return update


@pytest.fixture
def admin_id(monkeypatch: pytest.MonkeyPatch) -> int:
    monkeypatch.setattr("toop.admin.settings", MagicMock(ADMIN_TELEGRAM_ID=42))
    return 42


async def test_admin_passes_through(admin_id: int) -> None:
    inner = AsyncMock()
    guarded = require_admin(inner)
    update = _mock_update(user_id=42)
    await guarded(update, MagicMock())
    inner.assert_awaited_once()
    update.effective_message.reply_text.assert_not_called()


async def test_non_admin_rejected(admin_id: int) -> None:
    inner = AsyncMock()
    guarded = require_admin(inner)
    update = _mock_update(user_id=99)
    await guarded(update, MagicMock())
    inner.assert_not_called()
    update.effective_message.reply_text.assert_awaited_once_with(ADMIN_REJECT_MESSAGE)


async def test_no_user_rejected(admin_id: int) -> None:
    inner = AsyncMock()
    guarded = require_admin(inner)
    update = _mock_update(user_id=None)
    await guarded(update, MagicMock())
    inner.assert_not_called()


async def test_admin_unset_rejects_all(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("toop.admin.settings", MagicMock(ADMIN_TELEGRAM_ID=0))
    inner = AsyncMock()
    guarded = require_admin(inner)
    update = _mock_update(user_id=1)
    await guarded(update, MagicMock())
    inner.assert_not_called()
