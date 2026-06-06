from __future__ import annotations

import sqlite3
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.constants import ChatType
from telegram.error import TimedOut

from toop import photos
from toop.handlers.roster import (
    PENDING_SET_PHOTO_KEY,
    handle_set_photo,
    handle_set_photo_callback,
    handle_set_photo_photo,
    handle_set_photo_text,
    handle_unset_photo,
    handle_unset_photo_callback,
)
from toop.i18n import t
from toop.players import add_ghost_player, add_player, list_active_players, set_player_photo


@pytest.fixture
def admin_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("toop.admin.settings", MagicMock(ADMIN_TELEGRAM_ID=42))


@pytest.fixture(autouse=True)
def _photos_dir(tmp_path: object, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(photos.settings, "PHOTOS_DIR", str(tmp_path / "photos"))  # type: ignore[operator]


def _admin_update(*, text: str | None = None, chat_type: str = ChatType.PRIVATE) -> MagicMock:
    u = MagicMock()
    u.effective_user = MagicMock(id=42)
    chat = MagicMock()
    chat.type = chat_type
    u.effective_chat = chat
    msg = MagicMock()
    msg.text = text
    msg.photo = []
    msg.reply_text = AsyncMock()
    u.effective_message = msg
    return u


def _photo_update(file_ids: list[str]) -> MagicMock:
    u = _admin_update()
    u.effective_message.text = None
    u.effective_message.photo = [MagicMock(file_id=fid) for fid in file_ids]
    return u


def _callback_update(data: str) -> MagicMock:
    u = MagicMock()
    u.effective_user = MagicMock(id=42)
    q = MagicMock()
    q.data = data
    q.answer = AsyncMock()
    q.edit_message_text = AsyncMock()
    u.callback_query = q
    return u


def _ctx(conn: sqlite3.Connection, *, file_bytes: bytes = b"IMG") -> MagicMock:
    ctx = MagicMock()
    ctx.bot_data = {"conn": conn}
    ctx.user_data = {}
    tg_file = MagicMock()
    tg_file.download_as_bytearray = AsyncMock(return_value=bytearray(file_bytes))
    ctx.bot = MagicMock()
    ctx.bot.get_file = AsyncMock(return_value=tg_file)
    return ctx


# ----- /set_photo (interactive list) -----


async def test_set_photo_lists_active_players_including_ghost(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    add_player(conn, 1, "Alice", "alice")
    ghost = add_ghost_player(conn, "Ghosty")
    update = _admin_update(text="/set_photo")
    await handle_set_photo(update, _ctx(conn))
    kwargs = update.effective_message.reply_text.await_args.kwargs
    callbacks = [b.callback_data for row in kwargs["reply_markup"].inline_keyboard for b in row]
    assert "setphoto:1" in callbacks
    assert f"setphoto:{ghost.telegram_id}" in callbacks


async def test_set_photo_empty_roster(admin_settings: None, conn: sqlite3.Connection) -> None:
    update = _admin_update(text="/set_photo")
    await handle_set_photo(update, _ctx(conn))
    assert update.effective_message.reply_text.await_args.args[0] == t(
        "setphoto.empty_roster", "en"
    )


async def test_set_photo_in_group_redirects_to_dm(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    add_player(conn, 1, "Alice", "alice")
    update = _admin_update(text="/set_photo", chat_type=ChatType.GROUP)
    await handle_set_photo(update, _ctx(conn))
    assert update.effective_message.reply_text.await_args.args[0] == t("setphoto.dm_only", "en")


async def test_set_photo_non_admin_rejected(
    monkeypatch: pytest.MonkeyPatch, conn: sqlite3.Connection
) -> None:
    monkeypatch.setattr("toop.admin.settings", MagicMock(ADMIN_TELEGRAM_ID=42))
    update = _admin_update(text="/set_photo")
    update.effective_user = MagicMock(id=99)
    await handle_set_photo(update, _ctx(conn))
    assert "admin-only" in update.effective_message.reply_text.await_args.args[0]


async def test_set_photo_no_message_returns(admin_settings: None, conn: sqlite3.Connection) -> None:
    update = _admin_update(text="/set_photo")
    update.effective_message = None
    await handle_set_photo(update, _ctx(conn))  # no crash


# ----- callback: arm the pending capture -----


async def test_set_photo_callback_sets_pending(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    add_player(conn, 111, "H P", "hp")
    update = _callback_update("setphoto:111")
    ctx = _ctx(conn)
    await handle_set_photo_callback(update, ctx)
    assert ctx.user_data[PENDING_SET_PHOTO_KEY] == 111
    update.callback_query.answer.assert_awaited()
    assert "H P" in update.callback_query.edit_message_text.await_args.args[0]


async def test_set_photo_callback_invalid_id(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    update = _callback_update("setphoto:notanint")
    ctx = _ctx(conn)
    await handle_set_photo_callback(update, ctx)
    assert PENDING_SET_PHOTO_KEY not in ctx.user_data
    update.callback_query.answer.assert_awaited()


async def test_set_photo_callback_player_gone(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    update = _callback_update("setphoto:999")
    ctx = _ctx(conn)
    await handle_set_photo_callback(update, ctx)
    assert PENDING_SET_PHOTO_KEY not in ctx.user_data


async def test_set_photo_callback_without_query(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    update = MagicMock()
    update.effective_user = MagicMock(id=42)
    update.callback_query = None
    await handle_set_photo_callback(update, _ctx(conn))  # silent


# ----- photo: store the file_id + back up bytes -----


async def test_set_photo_photo_stores_file_id_and_backs_up(
    admin_settings: None, conn: sqlite3.Connection, tmp_path: object
) -> None:
    add_player(conn, 111, "H P", "hp")
    update = _photo_update(["thumb", "FULLRES"])
    ctx = _ctx(conn, file_bytes=b"JPEGBYTES")
    ctx.user_data[PENDING_SET_PHOTO_KEY] = 111
    await handle_set_photo_photo(update, ctx)
    # Largest PhotoSize (last) is stored.
    assert list_active_players(conn)[0].photo_file_id == "FULLRES"
    assert PENDING_SET_PHOTO_KEY not in ctx.user_data
    backup = photos._photo_path(111)
    assert backup.read_bytes() == b"JPEGBYTES"
    assert "H P" in update.effective_message.reply_text.await_args.args[0]


async def test_set_photo_photo_no_pending_is_ignored(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    add_player(conn, 111, "H P", "hp")
    update = _photo_update(["X"])
    await handle_set_photo_photo(update, _ctx(conn))
    assert list_active_players(conn)[0].photo_file_id is None
    update.effective_message.reply_text.assert_not_called()


async def test_set_photo_photo_backup_failure_still_stores_file_id(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    add_player(conn, 111, "H P", "hp")
    update = _photo_update(["FULLRES"])
    ctx = _ctx(conn)
    ctx.bot.get_file = AsyncMock(side_effect=TimedOut())
    ctx.user_data[PENDING_SET_PHOTO_KEY] = 111
    await handle_set_photo_photo(update, ctx)
    # file_id (the source of truth) is stored even when the byte backup failed.
    assert list_active_players(conn)[0].photo_file_id == "FULLRES"


async def test_set_photo_photo_player_gone_reports(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    update = _photo_update(["FULLRES"])
    ctx = _ctx(conn)
    ctx.user_data[PENDING_SET_PHOTO_KEY] = 999  # not on roster
    await handle_set_photo_photo(update, ctx)
    assert update.effective_message.reply_text.await_args.args[0] == t("setphoto.gone", "en")


async def test_set_photo_photo_no_photo_returns(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    update = _admin_update()  # photo == []
    ctx = _ctx(conn)
    ctx.user_data[PENDING_SET_PHOTO_KEY] = 111
    await handle_set_photo_photo(update, ctx)
    update.effective_message.reply_text.assert_not_called()


# ----- text guard while a set is pending -----


async def test_set_photo_text_command_cancels(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    update = _admin_update(text="/list_players")
    ctx = _ctx(conn)
    ctx.user_data[PENDING_SET_PHOTO_KEY] = 111
    await handle_set_photo_text(update, ctx)
    assert PENDING_SET_PHOTO_KEY not in ctx.user_data
    assert update.effective_message.reply_text.await_args.args[0] == t("setphoto.cancelled", "en")


async def test_set_photo_text_plain_nudges_and_keeps_pending(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    update = _admin_update(text="hello")
    ctx = _ctx(conn)
    ctx.user_data[PENDING_SET_PHOTO_KEY] = 111
    await handle_set_photo_text(update, ctx)
    assert ctx.user_data[PENDING_SET_PHOTO_KEY] == 111
    assert update.effective_message.reply_text.await_args.args[0] == t("setphoto.not_photo", "en")


async def test_set_photo_text_no_pending_is_ignored(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    update = _admin_update(text="just chatting")
    await handle_set_photo_text(update, _ctx(conn))
    update.effective_message.reply_text.assert_not_called()


async def test_set_photo_text_non_text_message_returns(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    update = _admin_update(text=None)  # e.g. a caption-less media message
    ctx = _ctx(conn)
    ctx.user_data[PENDING_SET_PHOTO_KEY] = 111
    await handle_set_photo_text(update, ctx)
    # Guard returns before touching the pending state.
    assert ctx.user_data[PENDING_SET_PHOTO_KEY] == 111
    update.effective_message.reply_text.assert_not_called()


# ----- /unset_photo -----


async def test_unset_photo_lists_players(admin_settings: None, conn: sqlite3.Connection) -> None:
    add_player(conn, 1, "Alice", "alice")
    update = _admin_update(text="/unset_photo")
    await handle_unset_photo(update, _ctx(conn))
    kwargs = update.effective_message.reply_text.await_args.kwargs
    callbacks = [b.callback_data for row in kwargs["reply_markup"].inline_keyboard for b in row]
    assert "unsetphoto:1" in callbacks


async def test_unset_photo_empty_roster(admin_settings: None, conn: sqlite3.Connection) -> None:
    update = _admin_update(text="/unset_photo")
    await handle_unset_photo(update, _ctx(conn))
    assert update.effective_message.reply_text.await_args.args[0] == t(
        "setphoto.empty_roster", "en"
    )


async def test_unset_photo_in_group_redirects(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    add_player(conn, 1, "Alice", "alice")
    update = _admin_update(text="/unset_photo", chat_type=ChatType.GROUP)
    await handle_unset_photo(update, _ctx(conn))
    assert update.effective_message.reply_text.await_args.args[0] == t("setphoto.dm_only", "en")


async def test_unset_photo_no_message_returns(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    update = _admin_update(text="/unset_photo")
    update.effective_message = None
    await handle_unset_photo(update, _ctx(conn))  # no crash


async def test_unset_photo_callback_clears_and_deletes_backup(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    add_player(conn, 111, "H P", "hp")
    set_player_photo(conn, 111, "FILE")
    photos.save_photo_bytes(111, b"x")
    update = _callback_update("unsetphoto:111")
    await handle_unset_photo_callback(update, _ctx(conn))
    assert list_active_players(conn)[0].photo_file_id is None
    assert not photos._photo_path(111).exists()
    assert "H P" in update.callback_query.edit_message_text.await_args.args[0]
