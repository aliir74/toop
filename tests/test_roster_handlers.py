from __future__ import annotations

import sqlite3
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.error import BadRequest

from toop.contacts import upsert_contact
from toop.handlers.roster import (
    handle_add_ghost,
    handle_add_player,
    handle_contacts,
    handle_disable_voting,
    handle_dk_report,
    handle_enable_voting,
    handle_link_player,
    handle_list_players,
    handle_pause_voting,
    handle_remove_player,
)
from toop.players import add_ghost_player, add_player, list_active_players


@pytest.fixture
def admin_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("toop.admin.settings", MagicMock(ADMIN_TELEGRAM_ID=42))


def _admin_update(text: str) -> MagicMock:
    u = MagicMock()
    u.effective_user = MagicMock(id=42)
    msg = MagicMock()
    msg.text = text
    msg.reply_text = AsyncMock()
    u.effective_message = msg
    return u


def _context(conn: sqlite3.Connection, args: list[str], chat_id: int | None = None) -> MagicMock:
    ctx = MagicMock()
    ctx.bot_data = {"conn": conn}
    ctx.args = args
    bot = MagicMock()
    if chat_id is None:
        bot.get_chat = AsyncMock(side_effect=BadRequest("chat not found"))
    else:
        bot.get_chat = AsyncMock(return_value=MagicMock(id=chat_id))
    ctx.bot = bot
    return ctx


async def test_add_player_round_trip(admin_settings: None, conn: sqlite3.Connection) -> None:
    update = _admin_update('/add_player @alice "Alice Smith"')
    ctx = _context(conn, args=["@alice", '"Alice', 'Smith"'], chat_id=111)
    await handle_add_player(update, ctx)
    players = list_active_players(conn)
    assert len(players) == 1
    assert players[0].telegram_id == 111
    assert players[0].display_name == "Alice Smith"
    update.effective_message.reply_text.assert_awaited_once()


async def test_add_player_unknown_username(admin_settings: None, conn: sqlite3.Connection) -> None:
    update = _admin_update('/add_player @ghost "Ghost"')
    ctx = _context(conn, args=["@ghost", '"Ghost"'], chat_id=None)
    await handle_add_player(update, ctx)
    assert list_active_players(conn) == []
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "DM me /start" in reply


async def test_add_player_bad_usage(admin_settings: None, conn: sqlite3.Connection) -> None:
    update = _admin_update("/add_player @alice")
    ctx = _context(conn, args=["@alice"])
    await handle_add_player(update, ctx)
    update.effective_message.reply_text.assert_awaited_once()
    reply = update.effective_message.reply_text.await_args.args[0]
    assert reply.startswith("Usage:")


async def test_add_player_by_id_success(admin_settings: None, conn: sqlite3.Connection) -> None:
    # Contact has DM'd the bot and carries a username — add purely by numeric id.
    upsert_contact(conn, 7290468940, username="meysam", display_name="Meysam Bz")
    update = _admin_update('/add_player 7290468940 "Meysam Bz"')
    # chat_id=None so the @handle resolution path would fail if it were taken.
    ctx = _context(conn, args=["7290468940", '"Meysam', 'Bz"'], chat_id=None)
    await handle_add_player(update, ctx)
    players = list_active_players(conn)
    assert len(players) == 1
    assert players[0].telegram_id == 7290468940
    assert players[0].display_name == "Meysam Bz"
    assert players[0].username == "meysam"


async def test_add_player_by_id_null_username(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    # No-username contact — the whole point of add-by-id.
    upsert_contact(conn, 5299711301, username=None, display_name="SHH")
    update = _admin_update('/add_player 5299711301 "SHH"')
    ctx = _context(conn, args=["5299711301", '"SHH"'], chat_id=None)
    await handle_add_player(update, ctx)
    players = list_active_players(conn)
    assert len(players) == 1
    assert players[0].telegram_id == 5299711301
    assert players[0].username is None
    assert "(no username)" in update.effective_message.reply_text.await_args.args[0]


async def test_add_player_by_id_not_a_contact(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    # Unknown id with no contacts row — can't be DM'd later, so reject.
    update = _admin_update('/add_player 999 "Ghost"')
    ctx = _context(conn, args=["999", '"Ghost"'], chat_id=None)
    await handle_add_player(update, ctx)
    assert list_active_players(conn) == []
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "hasn't DM'd the bot yet" in reply
    assert "999" in reply


async def test_add_player_unknown_username_points_to_id_path(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    update = _admin_update('/add_player @ghost "Ghost"')
    ctx = _context(conn, args=["@ghost", '"Ghost"'], chat_id=None)
    await handle_add_player(update, ctx)
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "/contacts" in reply
    assert "/add_player <id>" in reply


async def test_remove_player_round_trip(admin_settings: None, conn: sqlite3.Connection) -> None:
    add_update = _admin_update('/add_player @alice "Alice"')
    add_ctx = _context(conn, args=["@alice", '"Alice"'], chat_id=111)
    await handle_add_player(add_update, add_ctx)

    remove_update = _admin_update("/remove_player @alice")
    remove_ctx = _context(conn, args=["@alice"], chat_id=111)
    await handle_remove_player(remove_update, remove_ctx)
    assert list_active_players(conn) == []


async def test_list_players_empty(admin_settings: None, conn: sqlite3.Connection) -> None:
    update = _admin_update("/list_players")
    ctx = _context(conn, args=[])
    await handle_list_players(update, ctx)
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "empty" in reply.lower()


async def test_list_players_with_calibration_marker(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    add_update = _admin_update('/add_player @alice "Alice"')
    add_ctx = _context(conn, args=["@alice", '"Alice"'], chat_id=111)
    await handle_add_player(add_update, add_ctx)

    list_update = _admin_update("/list_players")
    list_ctx = _context(conn, args=[])
    await handle_list_players(list_update, list_ctx)
    reply = list_update.effective_message.reply_text.await_args.args[0]
    assert "Alice" in reply
    assert "calibrating" in reply


async def test_non_admin_blocked(monkeypatch: pytest.MonkeyPatch, conn: sqlite3.Connection) -> None:
    monkeypatch.setattr("toop.admin.settings", MagicMock(ADMIN_TELEGRAM_ID=42))
    update = _admin_update('/add_player @alice "Alice"')
    update.effective_user = MagicMock(id=99)
    ctx = _context(conn, args=["@alice", '"Alice"'], chat_id=111)
    await handle_add_player(update, ctx)
    assert list_active_players(conn) == []


# ----- branch coverage additions -----

from toop.handlers.roster import _conn, _parse_add_args  # noqa: E402


def test_conn_raises_when_missing() -> None:
    ctx = MagicMock()
    ctx.bot_data = {}
    with pytest.raises(RuntimeError, match="DB connection missing"):
        _conn(ctx)


def test_parse_add_args_unbalanced_quote() -> None:
    assert _parse_add_args('/add_player @alice "Unclosed') is None


def test_parse_add_args_too_few_tokens() -> None:
    assert _parse_add_args("/add_player @alice") is None


def test_parse_add_args_empty_username() -> None:
    assert _parse_add_args('/add_player @ "Name"') is None


def test_parse_add_args_numeric_id() -> None:
    assert _parse_add_args('/add_player 7290468940 "Meysam Bz"') == (7290468940, "Meysam Bz")


def test_parse_add_args_empty_display_name() -> None:
    assert _parse_add_args('/add_player @alice ""') is None


async def test_add_player_no_text(admin_settings: None, conn: sqlite3.Connection) -> None:
    update = _admin_update('/add_player @a "A"')
    update.effective_message.text = None
    await handle_add_player(update, _context(conn, args=["@a"], chat_id=111))
    update.effective_message.reply_text.assert_not_called()


async def test_add_player_revives_soft_deleted(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    await handle_add_player(
        _admin_update('/add_player @alice "Alice"'),
        _context(conn, args=["@alice", '"Alice"'], chat_id=111),
    )
    await handle_remove_player(
        _admin_update("/remove_player @alice"),
        _context(conn, args=["@alice"], chat_id=111),
    )
    update = _admin_update('/add_player @alice "Alice"')
    await handle_add_player(update, _context(conn, args=["@alice", '"Alice"'], chat_id=111))
    assert "revived" in update.effective_message.reply_text.await_args.args[0]


async def test_remove_player_returns_without_message(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    update = _admin_update("/remove_player @x")
    update.effective_message = None
    await handle_remove_player(update, _context(conn, args=["@x"], chat_id=111))


async def test_remove_player_empty_username(admin_settings: None, conn: sqlite3.Connection) -> None:
    update = _admin_update("/remove_player @")
    await handle_remove_player(update, _context(conn, args=["@"], chat_id=111))
    assert update.effective_message.reply_text.await_args.args[0].startswith("Usage")


async def test_remove_player_unknown_username(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    update = _admin_update("/remove_player @ghost")
    await handle_remove_player(update, _context(conn, args=["@ghost"], chat_id=None))
    assert "Couldn't find" in update.effective_message.reply_text.await_args.args[0]


async def test_remove_player_not_on_roster(admin_settings: None, conn: sqlite3.Connection) -> None:
    update = _admin_update("/remove_player @ghost")
    await handle_remove_player(update, _context(conn, args=["@ghost"], chat_id=222))
    assert "wasn't in the active roster" in update.effective_message.reply_text.await_args.args[0]


async def test_list_players_returns_without_message(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    update = _admin_update("/list_players")
    update.effective_message = None
    await handle_list_players(update, _context(conn, args=[]))


# ----- /contacts -----


async def test_contacts_empty(admin_settings: None, conn: sqlite3.Connection) -> None:
    update = _admin_update("/contacts")
    await handle_contacts(update, _context(conn, args=[]))
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "Nobody has DM'd me yet" in reply


async def test_contacts_flags_non_roster(admin_settings: None, conn: sqlite3.Connection) -> None:
    # Bob is on the roster; Newbie has only DM'd the bot.
    add_player(conn, 111, "Bob", "bob")
    upsert_contact(conn, 111, username="bob", display_name="Bob")
    upsert_contact(conn, 222, username="newbie", display_name="New Bie")

    update = _admin_update("/contacts")
    await handle_contacts(update, _context(conn, args=[]))
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "@bob" in reply
    assert "@newbie" in reply
    # Only the non-roster contact is flagged.
    assert reply.count("🆕 not on roster") == 1
    assert "available to /add_player (1" in reply
    # Non-roster contact gets a ready-to-copy add-by-id command line.
    assert '/add_player 222 "New Bie"' in reply
    # Roster member (Bob) gets no copy line.
    assert "/add_player 111" not in reply


async def test_contacts_all_on_roster_no_flag(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    add_player(conn, 111, "Bob", "bob")
    upsert_contact(conn, 111, username="bob", display_name="Bob")
    update = _admin_update("/contacts")
    await handle_contacts(update, _context(conn, args=[]))
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "not on roster" not in reply
    assert "available to /add_player" not in reply


async def test_contacts_returns_without_message(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    update = _admin_update("/contacts")
    update.effective_message = None
    await handle_contacts(update, _context(conn, args=[]))


# ----- pause / disable / enable voting -----


def _pool(conn: sqlite3.Connection, telegram_id: int) -> sqlite3.Row:
    return conn.execute(
        "SELECT in_pool, pool_paused_until FROM players WHERE telegram_id=?",
        (telegram_id,),
    ).fetchone()


async def test_pause_voting_by_id_sets_timer(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    add_player(conn, 111, "Bob", "bob")
    update = _admin_update("/pause_voting 111 2w")
    await handle_pause_voting(update, _context(conn, args=["111", "2w"]))
    assert _pool(conn, 111)["pool_paused_until"] is not None
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "rate" in reply.lower()


async def test_pause_voting_by_username(admin_settings: None, conn: sqlite3.Connection) -> None:
    add_player(conn, 111, "Bob", "bob")
    update = _admin_update("/pause_voting @bob 10d")
    await handle_pause_voting(update, _context(conn, args=["@bob", "10d"]))
    assert _pool(conn, 111)["pool_paused_until"] is not None


async def test_pause_voting_bad_duration(admin_settings: None, conn: sqlite3.Connection) -> None:
    add_player(conn, 111, "Bob", "bob")
    update = _admin_update("/pause_voting 111 soon")
    await handle_pause_voting(update, _context(conn, args=["111", "soon"]))
    assert _pool(conn, 111)["pool_paused_until"] is None
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "duration" in reply.lower() or "usage" in reply.lower()


async def test_pause_voting_bad_usage(admin_settings: None, conn: sqlite3.Connection) -> None:
    update = _admin_update("/pause_voting")
    await handle_pause_voting(update, _context(conn, args=[]))
    update.effective_message.reply_text.assert_awaited_once()


async def test_pause_voting_unknown_player(admin_settings: None, conn: sqlite3.Connection) -> None:
    update = _admin_update("/pause_voting @ghost 2w")
    await handle_pause_voting(update, _context(conn, args=["@ghost", "2w"]))
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "couldn't find" in reply.lower() or "not" in reply.lower()


async def test_disable_voting(admin_settings: None, conn: sqlite3.Connection) -> None:
    add_player(conn, 111, "Bob", "bob")
    update = _admin_update("/disable_voting 111")
    await handle_disable_voting(update, _context(conn, args=["111"]))
    assert _pool(conn, 111)["in_pool"] == 0


async def test_disable_voting_bad_usage(admin_settings: None, conn: sqlite3.Connection) -> None:
    update = _admin_update("/disable_voting")
    await handle_disable_voting(update, _context(conn, args=[]))
    update.effective_message.reply_text.assert_awaited_once()


async def test_disable_voting_unknown(admin_settings: None, conn: sqlite3.Connection) -> None:
    update = _admin_update("/disable_voting @ghost")
    await handle_disable_voting(update, _context(conn, args=["@ghost"]))
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "couldn't find" in reply.lower() or "not" in reply.lower()


async def test_enable_voting_clears_both(admin_settings: None, conn: sqlite3.Connection) -> None:
    add_player(conn, 111, "Bob", "bob")
    update = _admin_update("/disable_voting 111")
    await handle_disable_voting(update, _context(conn, args=["111"]))
    update2 = _admin_update("/enable_voting 111")
    await handle_enable_voting(update2, _context(conn, args=["111"]))
    row = _pool(conn, 111)
    assert row["in_pool"] == 1
    assert row["pool_paused_until"] is None


async def test_enable_voting_bad_usage(admin_settings: None, conn: sqlite3.Connection) -> None:
    update = _admin_update("/enable_voting")
    await handle_enable_voting(update, _context(conn, args=[]))
    update.effective_message.reply_text.assert_awaited_once()


async def test_enable_voting_unknown(admin_settings: None, conn: sqlite3.Connection) -> None:
    update = _admin_update("/enable_voting 999")
    await handle_enable_voting(update, _context(conn, args=["999"]))
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "not" in reply.lower() or "couldn't" in reply.lower()


async def test_pool_handlers_return_without_message(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    for handler in (
        handle_pause_voting,
        handle_disable_voting,
        handle_enable_voting,
        handle_dk_report,
    ):
        update = _admin_update("/x")
        update.effective_message = None
        await handler(update, _context(conn, args=[]))


async def test_dk_report_lists_by_rate(admin_settings: None, conn: sqlite3.Connection) -> None:
    add_player(conn, 1, "Alice", "alice")
    add_player(conn, 2, "Bob", "bob")
    # Voters skip rating Alice on several indicators; Bob gets one skip too.
    conn.executescript(
        """
        INSERT INTO score_skips (voter_id, player_id, indicator) VALUES
            (2, 1, 'attack'), (2, 1, 'receive'), (2, 1, 'block'), (2, 1, 'setting');
        INSERT INTO scores (voter_id, player_id, indicator, score) VALUES (2, 1, 'serve', 3);
        INSERT INTO score_skips (voter_id, player_id, indicator) VALUES (1, 2, 'attack');
        """
    )
    conn.commit()
    update = _admin_update("/dk_report")
    await handle_dk_report(update, _context(conn, args=[]))
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "Alice" in reply
    assert "Bob" in reply
    assert "%" in reply


async def test_dk_report_empty(admin_settings: None, conn: sqlite3.Connection) -> None:
    update = _admin_update("/dk_report")
    await handle_dk_report(update, _context(conn, args=[]))
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "No players" in reply


async def test_add_ghost_creates_and_hints_link(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    update = _admin_update('/add_ghost "Late Joiner"')
    await handle_add_ghost(update, _context(conn, args=[]))
    players = list_active_players(conn)
    assert len(players) == 1
    assert players[0].is_ghost is True
    assert players[0].display_name == "Late Joiner"
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "/link_player" in reply
    assert str(players[0].telegram_id) in reply


async def test_add_ghost_bad_usage(admin_settings: None, conn: sqlite3.Connection) -> None:
    update = _admin_update("/add_ghost")
    await handle_add_ghost(update, _context(conn, args=[]))
    reply = update.effective_message.reply_text.await_args.args[0]
    assert reply.startswith("Usage:")


async def test_add_ghost_unbalanced_quote(admin_settings: None, conn: sqlite3.Connection) -> None:
    update = _admin_update('/add_ghost "Unclosed')
    await handle_add_ghost(update, _context(conn, args=[]))
    reply = update.effective_message.reply_text.await_args.args[0]
    assert reply.startswith("Usage:")


async def test_add_ghost_no_text_returns(admin_settings: None, conn: sqlite3.Connection) -> None:
    update = _admin_update("/add_ghost")
    update.effective_message.text = None
    await handle_add_ghost(update, _context(conn, args=[]))
    update.effective_message.reply_text.assert_not_awaited()


# ----- /link_player -----


async def test_link_player_by_id_success(admin_settings: None, conn: sqlite3.Connection) -> None:
    ghost = add_ghost_player(conn, "Late Joiner")
    g = ghost.telegram_id
    upsert_contact(conn, 555, username="latejoiner", display_name="Late Joiner")
    update = _admin_update(f"/link_player {g} 555")
    await handle_link_player(update, _context(conn, args=[str(g), "555"], chat_id=None))
    assert conn.execute("SELECT 1 FROM players WHERE telegram_id=555").fetchone() is not None
    assert conn.execute("SELECT 1 FROM players WHERE telegram_id=?", (g,)).fetchone() is None
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "Linked" in reply


async def test_link_player_by_username_success(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    ghost = add_ghost_player(conn, "Late Joiner")
    g = ghost.telegram_id
    upsert_contact(conn, 555, username="latejoiner", display_name="Late Joiner")
    update = _admin_update(f"/link_player {g} @latejoiner")
    # chat_id=555 so get_chat resolves @latejoiner → 555.
    await handle_link_player(update, _context(conn, args=[str(g), "@latejoiner"], chat_id=555))
    assert conn.execute("SELECT 1 FROM players WHERE telegram_id=555").fetchone() is not None


async def test_link_player_one_arg_shows_usage(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    # No args is the ghost-button flow now; a single arg is incomplete typed use.
    update = _admin_update("/link_player 5")
    await handle_link_player(update, _context(conn, args=["5"]))
    reply = update.effective_message.reply_text.await_args.args[0]
    assert reply.startswith("Usage:")


async def test_link_player_non_digit_ghost(admin_settings: None, conn: sqlite3.Connection) -> None:
    update = _admin_update("/link_player abc 555")
    await handle_link_player(update, _context(conn, args=["abc", "555"]))
    reply = update.effective_message.reply_text.await_args.args[0]
    assert reply.startswith("Usage:")


async def test_link_player_not_a_ghost(admin_settings: None, conn: sqlite3.Connection) -> None:
    add_player(conn, 111, "Real", "real")  # a normal player, not a ghost
    update = _admin_update("/link_player 111 555")
    await handle_link_player(update, _context(conn, args=["111", "555"]))
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "isn't a ghost" in reply


async def test_link_player_username_unresolved(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    ghost = add_ghost_player(conn, "Late Joiner")
    g = ghost.telegram_id
    update = _admin_update(f"/link_player {g} @nope")
    await handle_link_player(update, _context(conn, args=[str(g), "@nope"], chat_id=None))
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "Couldn't find" in reply


async def test_link_player_real_not_contact(admin_settings: None, conn: sqlite3.Connection) -> None:
    ghost = add_ghost_player(conn, "Late Joiner")
    g = ghost.telegram_id
    update = _admin_update(f"/link_player {g} 555")  # 555 never DM'd the bot
    await handle_link_player(update, _context(conn, args=[str(g), "555"], chat_id=None))
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "hasn't DM'd" in reply


async def test_link_player_no_message_returns(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    update = _admin_update("/link_player")
    update.effective_message = None
    await handle_link_player(update, _context(conn, args=[]))


# ----- list/contacts markers for ghost & paused players -----


async def test_list_players_marks_ghost_paused_disabled(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    from datetime import UTC, datetime, timedelta

    add_player(conn, 1, "Normal", "normal")
    add_player(conn, 2, "Disabled", "disabled")
    add_player(conn, 3, "Paused", "paused")
    add_player(conn, 4, "Anon")  # real player, no username → "(no username)"
    add_ghost_player(conn, "Ghosty")
    conn.execute("UPDATE players SET in_pool=0 WHERE telegram_id=2")
    future = (datetime.now(UTC) + timedelta(days=5)).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("UPDATE players SET pool_paused_until=? WHERE telegram_id=3", (future,))
    conn.commit()
    update = _admin_update("/list_players")
    await handle_list_players(update, _context(conn, args=[]))
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "👻 ghost" in reply
    assert "🚫 voting disabled" in reply
    assert "⏸ voting paused" in reply
    assert "(no username)" in reply


async def test_list_players_expired_pause_not_marked(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    from datetime import UTC, datetime, timedelta

    add_player(conn, 1, "Expired", "expired")
    past = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("UPDATE players SET pool_paused_until=? WHERE telegram_id=1", (past,))
    conn.commit()
    update = _admin_update("/list_players")
    await handle_list_players(update, _context(conn, args=[]))
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "paused" not in reply


async def test_contacts_excludes_ghosts(admin_settings: None, conn: sqlite3.Connection) -> None:
    add_ghost_player(conn, "Ghosty")
    upsert_contact(conn, 111, username="bob", display_name="Bob")
    update = _admin_update("/contacts")
    await handle_contacts(update, _context(conn, args=[]))
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "Ghosty" not in reply
    assert "@bob" in reply


# ----- shared button helpers -----

from datetime import timedelta  # noqa: E402

from toop.handlers.roster import (  # noqa: E402
    PAUSE_DURATIONS,
    _parse_duration,
    _pick_id,
    _player_keyboard,
)


def test_parse_duration_months() -> None:
    assert _parse_duration("1m") == timedelta(days=30)
    assert _parse_duration("3m") == timedelta(days=90)


def test_parse_duration_weeks_days_still_work() -> None:
    assert _parse_duration("2w") == timedelta(days=14)
    assert _parse_duration("10d") == timedelta(days=10)
    assert _parse_duration("soon") is None


def test_pause_durations_all_parse() -> None:
    # Every button token must round-trip through the typed parser.
    for _label, token in PAUSE_DURATIONS:
        assert _parse_duration(token) is not None


def test_player_keyboard_one_button_per_player(conn: sqlite3.Connection) -> None:
    add_player(conn, 1, "Alice", "alice")
    add_player(conn, 2, "SHH", None)
    kb = _player_keyboard(list_active_players(conn), "rmpick:")
    labels = [b.text for row in kb.inline_keyboard for b in row]
    callbacks = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "Alice (@alice)" in labels
    assert "SHH" in labels
    assert "rmpick:1" in callbacks
    assert "rmpick:2" in callbacks


def test_pick_id_parses_positive_negative_and_rejects() -> None:
    assert _pick_id("rmpick:5", "rmpick:") == 5
    assert _pick_id("lnkghost:-3", "lnkghost:") == -3  # negative ghost id
    assert _pick_id("rmpick:abc", "rmpick:") is None


# ----- callback test helper -----


def _callback_update(data: str) -> MagicMock:
    u = MagicMock()
    u.effective_user = MagicMock(id=42)
    q = MagicMock()
    q.data = data
    q.from_user = MagicMock(id=42)
    q.answer = AsyncMock()
    q.edit_message_text = AsyncMock()
    u.callback_query = q
    return u


def _callbacks(update: MagicMock) -> tuple:
    return update.callback_query.answer, update.callback_query.edit_message_text


# ----- /remove_player buttons -----

from toop.handlers.roster import handle_remove_callback  # noqa: E402


async def test_remove_player_no_args_lists_buttons(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    add_player(conn, 1, "Alice", "alice")
    add_player(conn, 2, "SHH", None)
    update = _admin_update("/remove_player")
    await handle_remove_player(update, _context(conn, args=[]))
    kb = update.effective_message.reply_text.await_args.kwargs["reply_markup"].inline_keyboard
    callbacks = [b.callback_data for row in kb for b in row]
    assert "rmpick:1" in callbacks
    assert "rmpick:2" in callbacks


async def test_remove_player_no_args_empty_roster(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    update = _admin_update("/remove_player")
    await handle_remove_player(update, _context(conn, args=[]))
    assert "empty" in update.effective_message.reply_text.await_args.args[0].lower()


async def test_remove_callback_removes_and_edits(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    add_player(conn, 111, "Bob", "bob")
    update = _callback_update("rmpick:111")
    answer, edit = _callbacks(update)
    await handle_remove_callback(update, _context(conn, args=[]))
    assert list_active_players(conn) == []
    answer.assert_awaited()
    assert "Removed Bob" in edit.await_args.args[0]


async def test_remove_callback_gone_player_alerts(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    update = _callback_update("rmpick:999")
    answer, edit = _callbacks(update)
    await handle_remove_callback(update, _context(conn, args=[]))
    assert "no longer" in answer.await_args.args[0].lower()
    edit.assert_not_called()


async def test_remove_callback_bad_int_returns(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    update = _callback_update("rmpick:notanint")
    answer, edit = _callbacks(update)
    await handle_remove_callback(update, _context(conn, args=[]))
    answer.assert_awaited()
    edit.assert_not_called()


async def test_remove_callback_no_query_returns(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    update = MagicMock()
    update.effective_user = MagicMock(id=42)
    update.callback_query = None
    await handle_remove_callback(update, _context(conn, args=[]))  # silent


# ----- /disable_voting buttons -----

from toop.handlers.roster import handle_disable_callback  # noqa: E402


async def test_disable_voting_no_args_lists_buttons(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    add_player(conn, 111, "Bob", "bob")
    update = _admin_update("/disable_voting")
    await handle_disable_voting(update, _context(conn, args=[]))
    kb = update.effective_message.reply_text.await_args.kwargs["reply_markup"].inline_keyboard
    assert "dispick:111" in [b.callback_data for row in kb for b in row]


async def test_disable_callback_disables_and_edits(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    add_player(conn, 111, "Bob", "bob")
    update = _callback_update("dispick:111")
    answer, edit = _callbacks(update)
    await handle_disable_callback(update, _context(conn, args=[]))
    assert _pool(conn, 111)["in_pool"] == 0
    answer.assert_awaited()
    assert "Disabled Bob" in edit.await_args.args[0]


async def test_disable_callback_gone_player_alerts(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    update = _callback_update("dispick:999")
    answer, edit = _callbacks(update)
    await handle_disable_callback(update, _context(conn, args=[]))
    assert "no longer" in answer.await_args.args[0].lower()
    edit.assert_not_called()


# ----- /enable_voting buttons (only paused/disabled) -----

from datetime import UTC, datetime  # noqa: E402

from toop.handlers.roster import handle_enable_callback  # noqa: E402


async def test_enable_voting_no_args_lists_only_paused_disabled(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    add_player(conn, 1, "Normal", "normal")  # in pool — must NOT appear
    add_player(conn, 2, "Disabled", "disabled")
    add_player(conn, 3, "Paused", "paused")
    conn.execute("UPDATE players SET in_pool=0 WHERE telegram_id=2")
    future = (datetime.now(UTC) + timedelta(days=5)).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("UPDATE players SET pool_paused_until=? WHERE telegram_id=3", (future,))
    conn.commit()
    update = _admin_update("/enable_voting")
    await handle_enable_voting(update, _context(conn, args=[]))
    kb = update.effective_message.reply_text.await_args.kwargs["reply_markup"].inline_keyboard
    callbacks = [b.callback_data for row in kb for b in row]
    assert set(callbacks) == {"enpick:2", "enpick:3"}


async def test_enable_voting_no_args_nobody_paused(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    add_player(conn, 1, "Normal", "normal")
    update = _admin_update("/enable_voting")
    await handle_enable_voting(update, _context(conn, args=[]))
    assert "Nobody is paused" in update.effective_message.reply_text.await_args.args[0]


async def test_enable_callback_restores_and_edits(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    add_player(conn, 111, "Bob", "bob")
    conn.execute("UPDATE players SET in_pool=0 WHERE telegram_id=111")
    conn.commit()
    update = _callback_update("enpick:111")
    answer, edit = _callbacks(update)
    await handle_enable_callback(update, _context(conn, args=[]))
    assert _pool(conn, 111)["in_pool"] == 1
    answer.assert_awaited()
    assert "Restored Bob" in edit.await_args.args[0]


# ----- /pause_voting player → duration chain -----

from toop.handlers.roster import (  # noqa: E402
    handle_pause_dur_callback,
    handle_pause_pick_callback,
)


async def test_pause_no_args_lists_buttons(admin_settings: None, conn: sqlite3.Connection) -> None:
    add_player(conn, 111, "Bob", "bob")
    update = _admin_update("/pause_voting")
    await handle_pause_voting(update, _context(conn, args=[]))
    kb = update.effective_message.reply_text.await_args.kwargs["reply_markup"].inline_keyboard
    assert "pausepick:111" in [b.callback_data for row in kb for b in row]


async def test_pause_one_arg_missing_duration(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    # A player but no duration: the typed path still needs both, so show usage.
    add_player(conn, 111, "Bob", "bob")
    update = _admin_update("/pause_voting @bob")
    await handle_pause_voting(update, _context(conn, args=["@bob"]))
    assert update.effective_message.reply_text.await_args.args[0].startswith("Usage:")


async def test_pause_pick_callback_shows_durations(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    add_player(conn, 111, "Bob", "bob")
    update = _callback_update("pausepick:111")
    answer, edit = _callbacks(update)
    await handle_pause_pick_callback(update, _context(conn, args=[]))
    answer.assert_awaited()
    kb = edit.await_args.kwargs["reply_markup"].inline_keyboard
    callbacks = [b.callback_data for row in kb for b in row]
    assert "pausedur:111:1w" in callbacks
    assert "pausedur:111:1m" in callbacks


async def test_pause_pick_callback_gone_player_alerts(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    update = _callback_update("pausepick:999")
    answer, edit = _callbacks(update)
    await handle_pause_pick_callback(update, _context(conn, args=[]))
    assert "no longer" in answer.await_args.args[0].lower()
    edit.assert_not_called()


async def test_pause_pick_callback_bad_int_returns(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    update = _callback_update("pausepick:abc")
    answer, edit = _callbacks(update)
    await handle_pause_pick_callback(update, _context(conn, args=[]))
    answer.assert_awaited()
    edit.assert_not_called()


async def test_pause_pick_callback_no_query_returns(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    update = MagicMock()
    update.effective_user = MagicMock(id=42)
    update.callback_query = None
    await handle_pause_pick_callback(update, _context(conn, args=[]))  # silent


async def test_pause_dur_callback_applies(admin_settings: None, conn: sqlite3.Connection) -> None:
    add_player(conn, 111, "Bob", "bob")
    update = _callback_update("pausedur:111:2w")
    answer, edit = _callbacks(update)
    await handle_pause_dur_callback(update, _context(conn, args=[]))
    assert _pool(conn, 111)["pool_paused_until"] is not None
    answer.assert_awaited()
    assert "Paused Bob" in edit.await_args.args[0]


async def test_pause_dur_callback_negative_ghost_id(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    ghost = add_ghost_player(conn, "Ghosty")
    g = ghost.telegram_id  # negative
    update = _callback_update(f"pausedur:{g}:1m")
    answer, edit = _callbacks(update)
    await handle_pause_dur_callback(update, _context(conn, args=[]))
    assert _pool(conn, g)["pool_paused_until"] is not None
    assert "Paused Ghosty" in edit.await_args.args[0]


async def test_pause_dur_callback_bad_id_returns(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    update = _callback_update("pausedur:abc:2w")
    answer, edit = _callbacks(update)
    await handle_pause_dur_callback(update, _context(conn, args=[]))
    answer.assert_awaited()
    edit.assert_not_called()


async def test_pause_dur_callback_bad_token_returns(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    add_player(conn, 111, "Bob", "bob")
    update = _callback_update("pausedur:111:bogus")
    answer, edit = _callbacks(update)
    await handle_pause_dur_callback(update, _context(conn, args=[]))
    answer.assert_awaited()
    edit.assert_not_called()
    assert _pool(conn, 111)["pool_paused_until"] is None


async def test_pause_dur_callback_gone_player_alerts(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    update = _callback_update("pausedur:999:2w")
    answer, edit = _callbacks(update)
    await handle_pause_dur_callback(update, _context(conn, args=[]))
    assert "no longer" in answer.await_args.args[0].lower()
    edit.assert_not_called()


async def test_pause_dur_callback_no_query_returns(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    update = MagicMock()
    update.effective_user = MagicMock(id=42)
    update.callback_query = None
    await handle_pause_dur_callback(update, _context(conn, args=[]))  # silent


# ----- /link_player ghost → contact chain -----

from toop.handlers.roster import (  # noqa: E402
    handle_link_ghost_callback,
    handle_link_real_callback,
)


async def test_link_no_args_lists_ghosts(admin_settings: None, conn: sqlite3.Connection) -> None:
    ghost = add_ghost_player(conn, "Late Joiner")
    add_player(conn, 1, "Normal", "normal")  # real player — must NOT appear
    update = _admin_update("/link_player")
    await handle_link_player(update, _context(conn, args=[]))
    kb = update.effective_message.reply_text.await_args.kwargs["reply_markup"].inline_keyboard
    callbacks = [b.callback_data for row in kb for b in row]
    assert callbacks == [f"lnkghost:{ghost.telegram_id}"]


async def test_link_no_args_no_ghosts(admin_settings: None, conn: sqlite3.Connection) -> None:
    update = _admin_update("/link_player")
    await handle_link_player(update, _context(conn, args=[]))
    assert "No ghost players" in update.effective_message.reply_text.await_args.args[0]


async def test_link_ghost_callback_lists_contacts(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    ghost = add_ghost_player(conn, "Late Joiner")
    g = ghost.telegram_id
    upsert_contact(conn, 555, username="latejoiner", display_name="Late Joiner")
    update = _callback_update(f"lnkghost:{g}")
    answer, edit = _callbacks(update)
    await handle_link_ghost_callback(update, _context(conn, args=[]))
    answer.assert_awaited()
    kb = edit.await_args.kwargs["reply_markup"].inline_keyboard
    assert f"lnkreal:{g}:555" in [b.callback_data for row in kb for b in row]


async def test_link_ghost_callback_no_contacts(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    ghost = add_ghost_player(conn, "Late Joiner")
    update = _callback_update(f"lnkghost:{ghost.telegram_id}")
    answer, edit = _callbacks(update)
    await handle_link_ghost_callback(update, _context(conn, args=[]))
    assert "Nobody new" in edit.await_args.args[0]


async def test_link_ghost_callback_not_a_ghost_alerts(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    add_player(conn, 111, "Real", "real")  # not a ghost
    update = _callback_update("lnkghost:111")
    answer, edit = _callbacks(update)
    await handle_link_ghost_callback(update, _context(conn, args=[]))
    assert "isn't a ghost" in answer.await_args.args[0].lower()
    edit.assert_not_called()


async def test_link_ghost_callback_bad_int_returns(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    update = _callback_update("lnkghost:notanint")
    answer, edit = _callbacks(update)
    await handle_link_ghost_callback(update, _context(conn, args=[]))
    answer.assert_awaited()
    edit.assert_not_called()


async def test_link_ghost_callback_no_query_returns(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    update = MagicMock()
    update.effective_user = MagicMock(id=42)
    update.callback_query = None
    await handle_link_ghost_callback(update, _context(conn, args=[]))  # silent


async def test_link_real_callback_links_and_edits(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    ghost = add_ghost_player(conn, "Late Joiner")
    g = ghost.telegram_id
    upsert_contact(conn, 555, username="latejoiner", display_name="Late Joiner")
    update = _callback_update(f"lnkreal:{g}:555")
    answer, edit = _callbacks(update)
    await handle_link_real_callback(update, _context(conn, args=[]))
    assert conn.execute("SELECT 1 FROM players WHERE telegram_id=555").fetchone() is not None
    assert conn.execute("SELECT 1 FROM players WHERE telegram_id=?", (g,)).fetchone() is None
    answer.assert_awaited()
    assert "Linked" in edit.await_args.args[0]


async def test_link_real_callback_bad_int_returns(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    update = _callback_update("lnkreal:abc:555")
    answer, edit = _callbacks(update)
    await handle_link_real_callback(update, _context(conn, args=[]))
    answer.assert_awaited()
    edit.assert_not_called()


async def test_link_real_callback_ghost_gone_alerts(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    upsert_contact(conn, 555, username="latejoiner", display_name="Late Joiner")
    update = _callback_update("lnkreal:-99:555")  # no such ghost
    answer, edit = _callbacks(update)
    await handle_link_real_callback(update, _context(conn, args=[]))
    assert "ghost is no longer" in answer.await_args.args[0].lower()
    edit.assert_not_called()


async def test_link_real_callback_contact_gone_alerts(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    ghost = add_ghost_player(conn, "Late Joiner")
    update = _callback_update(f"lnkreal:{ghost.telegram_id}:555")  # 555 never DM'd
    answer, edit = _callbacks(update)
    await handle_link_real_callback(update, _context(conn, args=[]))
    assert "contact is no longer" in answer.await_args.args[0].lower()
    edit.assert_not_called()


async def test_link_real_callback_no_query_returns(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    update = MagicMock()
    update.effective_user = MagicMock(id=42)
    update.callback_query = None
    await handle_link_real_callback(update, _context(conn, args=[]))  # silent


# ----- /add_player from contacts + typed-name consumer -----

from telegram.constants import ChatType  # noqa: E402

from toop.handlers.roster import (  # noqa: E402
    PENDING_ADD_KEY,
    handle_add_pick_callback,
    handle_add_player_text,
)


def _private_update(text: str) -> MagicMock:
    u = _admin_update(text)
    u.effective_chat = MagicMock(type=ChatType.PRIVATE)
    return u


async def test_add_player_no_args_lists_contacts(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    upsert_contact(conn, 222, username="newbie", display_name="New Bie")
    update = _private_update("/add_player")
    await handle_add_player(update, _context(conn, args=[]))
    kb = update.effective_message.reply_text.await_args.kwargs["reply_markup"].inline_keyboard
    assert "addpick:222" in [b.callback_data for row in kb for b in row]


async def test_add_player_no_args_in_group_redirects(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    update = _admin_update("/add_player")
    update.effective_chat = MagicMock(type=ChatType.GROUP)
    await handle_add_player(update, _context(conn, args=[]))
    assert "DM me" in update.effective_message.reply_text.await_args.args[0]


async def test_add_player_no_args_no_contacts(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    update = _private_update("/add_player")
    await handle_add_player(update, _context(conn, args=[]))
    assert "No new contacts" in update.effective_message.reply_text.await_args.args[0]


async def test_add_pick_callback_stashes_and_prompts(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    upsert_contact(conn, 222, username="newbie", display_name="New Bie")
    update = _callback_update("addpick:222")
    ctx = _context(conn, args=[])
    ctx.user_data = {}
    await handle_add_pick_callback(update, ctx)
    assert ctx.user_data[PENDING_ADD_KEY] == 222
    update.callback_query.answer.assert_awaited()
    assert "Send the display name" in update.callback_query.edit_message_text.await_args.args[0]


async def test_add_pick_callback_gone_contact_alerts(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    update = _callback_update("addpick:999")
    ctx = _context(conn, args=[])
    ctx.user_data = {}
    await handle_add_pick_callback(update, ctx)
    assert "no longer available" in update.callback_query.answer.await_args.args[0].lower()
    assert PENDING_ADD_KEY not in ctx.user_data


async def test_add_pick_callback_bad_int_returns(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    update = _callback_update("addpick:abc")
    answer, edit = _callbacks(update)
    await handle_add_pick_callback(update, _context(conn, args=[]))
    answer.assert_awaited()
    edit.assert_not_called()


async def test_add_pick_callback_no_query_returns(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    update = MagicMock()
    update.effective_user = MagicMock(id=42)
    update.callback_query = None
    await handle_add_pick_callback(update, _context(conn, args=[]))  # silent


async def test_add_player_text_adds_and_clears(conn: sqlite3.Connection) -> None:
    upsert_contact(conn, 222, username="newbie", display_name="New Bie")
    update = _admin_update("Newbie Display")
    ctx = _context(conn, args=[])
    ctx.user_data = {PENDING_ADD_KEY: 222}
    await handle_add_player_text(update, ctx)
    players = list_active_players(conn)
    assert any(p.telegram_id == 222 and p.display_name == "Newbie Display" for p in players)
    assert PENDING_ADD_KEY not in ctx.user_data
    assert "Added" in update.effective_message.reply_text.await_args.args[0]


async def test_add_player_text_no_pending_ignored(conn: sqlite3.Connection) -> None:
    update = _admin_update("random chatter")
    ctx = _context(conn, args=[])
    ctx.user_data = {}
    await handle_add_player_text(update, ctx)
    update.effective_message.reply_text.assert_not_called()


async def test_add_player_text_command_cancels(conn: sqlite3.Connection) -> None:
    update = _admin_update("/list_players")
    ctx = _context(conn, args=[])
    ctx.user_data = {PENDING_ADD_KEY: 222}
    await handle_add_player_text(update, ctx)
    assert PENDING_ADD_KEY not in ctx.user_data
    assert "cancelled" in update.effective_message.reply_text.await_args.args[0].lower()


async def test_add_player_text_empty_keeps_pending(conn: sqlite3.Connection) -> None:
    update = _admin_update("   ")
    ctx = _context(conn, args=[])
    ctx.user_data = {PENDING_ADD_KEY: 222}
    await handle_add_player_text(update, ctx)
    assert ctx.user_data[PENDING_ADD_KEY] == 222
    assert "empty" in update.effective_message.reply_text.await_args.args[0].lower()


async def test_add_player_text_contact_gone(conn: sqlite3.Connection) -> None:
    update = _admin_update("Some Name")
    ctx = _context(conn, args=[])
    ctx.user_data = {PENDING_ADD_KEY: 999}  # never a contact
    await handle_add_player_text(update, ctx)
    assert PENDING_ADD_KEY not in ctx.user_data
    assert "no longer available" in update.effective_message.reply_text.await_args.args[0]


async def test_add_player_text_no_message_returns(conn: sqlite3.Connection) -> None:
    update = _admin_update("x")
    update.effective_message = None
    ctx = _context(conn, args=[])
    ctx.user_data = {}
    await handle_add_player_text(update, ctx)  # silent
