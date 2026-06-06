from __future__ import annotations

import sqlite3
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.constants import ParseMode
from telegram.error import BadRequest

from toop.handlers.voting import _get_player, _send_next_prompt
from toop.players import add_player, set_player_photo
from toop.voting_queue import ScoreTarget


def _ctx(conn: sqlite3.Connection) -> MagicMock:
    ctx = MagicMock()
    ctx.bot_data = {"conn": conn}
    ctx.bot = MagicMock()
    ctx.bot.send_message = AsyncMock()
    ctx.bot.send_photo = AsyncMock()
    ctx.bot.edit_message_text = AsyncMock()
    ctx.bot.delete_message = AsyncMock()
    return ctx


def _patch_selector(monkeypatch: pytest.MonkeyPatch, target: ScoreTarget | None) -> None:
    monkeypatch.setattr("toop.handlers.voting.select_next_score_target", lambda *a, **k: target)


def _seed(conn: sqlite3.Connection) -> None:
    add_player(conn, 1, "Voter", "voter")
    add_player(conn, 2, "Target", "target")


# ----- 4.1: photo_file_id hydrated into the card's player -----


def test_get_player_carries_photo_file_id(conn: sqlite3.Connection) -> None:
    add_player(conn, 2, "Target", "target")
    assert _get_player(conn, 2).photo_file_id is None
    set_player_photo(conn, 2, "FILEID")
    assert _get_player(conn, 2).photo_file_id == "FILEID"


# ----- 4.2: render as photo when set, text otherwise -----


async def test_fresh_send_uses_photo_when_set(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(conn)
    set_player_photo(conn, 2, "FILEID")
    _patch_selector(monkeypatch, ScoreTarget(player_id=2, indicator="attack"))
    ctx = _ctx(conn)
    await _send_next_prompt(conn, ctx, chat_id=1, voter_id=1)
    ctx.bot.send_photo.assert_awaited_once()
    kwargs = ctx.bot.send_photo.await_args.kwargs
    assert kwargs["photo"] == "FILEID"
    assert kwargs["parse_mode"] == ParseMode.MARKDOWN
    assert "Target" in kwargs["caption"]
    assert kwargs["reply_markup"] is not None
    ctx.bot.send_message.assert_not_awaited()


async def test_fresh_send_uses_text_when_no_photo(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(conn)
    _patch_selector(monkeypatch, ScoreTarget(player_id=2, indicator="attack"))
    ctx = _ctx(conn)
    await _send_next_prompt(conn, ctx, chat_id=1, voter_id=1)
    ctx.bot.send_message.assert_awaited_once()
    ctx.bot.send_photo.assert_not_awaited()


async def test_stale_photo_file_id_falls_back_to_text(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(conn)
    set_player_photo(conn, 2, "DEADFILE")
    _patch_selector(monkeypatch, ScoreTarget(player_id=2, indicator="attack"))
    ctx = _ctx(conn)
    ctx.bot.send_photo = AsyncMock(side_effect=BadRequest("wrong file identifier"))
    await _send_next_prompt(conn, ctx, chat_id=1, voter_id=1)
    # Voting never blocks on a bad photo — the text card still goes out.
    ctx.bot.send_message.assert_awaited_once()


# ----- 4.3: transitions across photo/text -----


async def test_text_to_text_advance_edits_in_place(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(conn)
    _patch_selector(monkeypatch, ScoreTarget(player_id=2, indicator="attack"))
    ctx = _ctx(conn)
    await _send_next_prompt(
        conn, ctx, chat_id=1, voter_id=1, edit_message_id=9, current_is_photo=False
    )
    ctx.bot.edit_message_text.assert_awaited_once()
    ctx.bot.delete_message.assert_not_awaited()
    ctx.bot.send_photo.assert_not_awaited()


async def test_text_to_photo_advance_deletes_and_sends_photo(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(conn)
    set_player_photo(conn, 2, "FILEID")
    _patch_selector(monkeypatch, ScoreTarget(player_id=2, indicator="attack"))
    ctx = _ctx(conn)
    await _send_next_prompt(
        conn, ctx, chat_id=1, voter_id=1, edit_message_id=9, current_is_photo=False
    )
    ctx.bot.delete_message.assert_awaited_once()
    ctx.bot.send_photo.assert_awaited_once()
    ctx.bot.edit_message_text.assert_not_awaited()


async def test_photo_to_text_advance_deletes_and_sends_text(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(conn)
    _patch_selector(monkeypatch, ScoreTarget(player_id=2, indicator="attack"))
    ctx = _ctx(conn)
    await _send_next_prompt(
        conn, ctx, chat_id=1, voter_id=1, edit_message_id=9, current_is_photo=True
    )
    ctx.bot.delete_message.assert_awaited_once()
    ctx.bot.send_message.assert_awaited_once()
    ctx.bot.edit_message_text.assert_not_awaited()


async def test_photo_to_photo_advance_deletes_and_sends_photo(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(conn)
    set_player_photo(conn, 2, "FILEID")
    _patch_selector(monkeypatch, ScoreTarget(player_id=2, indicator="attack"))
    ctx = _ctx(conn)
    await _send_next_prompt(
        conn, ctx, chat_id=1, voter_id=1, edit_message_id=9, current_is_photo=True
    )
    ctx.bot.delete_message.assert_awaited_once()
    ctx.bot.send_photo.assert_awaited_once()


async def test_no_prompts_from_photo_card_deletes_then_sends_text(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_selector(monkeypatch, None)
    ctx = _ctx(conn)
    await _send_next_prompt(
        conn, ctx, chat_id=1, voter_id=1, edit_message_id=9, current_is_photo=True
    )
    ctx.bot.delete_message.assert_awaited_once()
    ctx.bot.send_message.assert_awaited_once()
    ctx.bot.edit_message_text.assert_not_awaited()
