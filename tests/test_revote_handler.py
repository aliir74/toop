from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.error import BadRequest, Forbidden

from toop.contacts import upsert_contact
from toop.handlers.revote import _conn, handle_revote_ping, revote_nudge_job
from toop.i18n import t
from toop.pause import pause_events_until
from toop.players import add_player
from toop.revote import record_nudge


@pytest.fixture(autouse=True)
def patch_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    """require_admin reads toop.admin.settings, not the handler's own."""
    monkeypatch.setattr("toop.admin.settings", MagicMock(ADMIN_TELEGRAM_ID=42))


@pytest.fixture(autouse=True)
def patch_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """The handler reads settings off its OWN module name, mirroring
    handlers/alerts.py — patching toop.config.settings would not take."""
    monkeypatch.setattr(
        "toop.handlers.revote.settings",
        MagicMock(REVOTE_NUDGE_COOLDOWN_DAYS=7, REVOTE_NUDGE_MIN_PENDING=3),
    )


def _ctx(conn: sqlite3.Connection, args: list[str] | None = None) -> MagicMock:
    ctx = MagicMock()
    ctx.bot_data = {"conn": conn}
    ctx.args = args or []
    ctx.bot = MagicMock()
    ctx.bot.send_message = AsyncMock()
    return ctx


def _admin_update() -> MagicMock:
    u = MagicMock()
    u.effective_user = MagicMock(id=42)
    msg = MagicMock()
    msg.reply_text = AsyncMock()
    u.effective_message = msg
    return u


def _roster(conn: sqlite3.Connection, n: int) -> None:
    for i in range(1, n + 1):
        add_player(conn, i, f"P{i}", f"p{i}")
        upsert_contact(conn, i, username=f"p{i}", display_name=f"P{i}")


def _dmed(ctx: MagicMock) -> set[int]:
    return {c.kwargs["chat_id"] for c in ctx.bot.send_message.await_args_list}


async def test_job_dms_due_voters_and_records_them(conn: sqlite3.Connection) -> None:
    _roster(conn, 3)
    ctx = _ctx(conn)
    await revote_nudge_job(ctx)
    assert _dmed(ctx) == {1, 2, 3}
    recorded = {r["telegram_id"] for r in conn.execute("SELECT telegram_id FROM revote_nudges")}
    assert recorded == {1, 2, 3}


async def test_job_dm_carries_the_pending_total(conn: sqlite3.Connection) -> None:
    _roster(conn, 2)
    ctx = _ctx(conn)
    await revote_nudge_job(ctx)
    text = ctx.bot.send_message.await_args_list[0].kwargs["text"]
    assert text == t("revote.dm_nudge", first="P1", total=6)


async def test_forbidden_voter_is_not_recorded_and_does_not_abort(
    conn: sqlite3.Connection,
) -> None:
    """A blocked bot must be retried next week, not silently retired, and it
    must not stop the voters queued behind it."""
    _roster(conn, 3)
    ctx = _ctx(conn)
    ctx.bot.send_message = AsyncMock(side_effect=[Forbidden("blocked"), None, None])
    await revote_nudge_job(ctx)
    recorded = {r["telegram_id"] for r in conn.execute("SELECT telegram_id FROM revote_nudges")}
    assert recorded == {2, 3}
    assert ctx.bot.send_message.await_count == 3


async def test_bad_request_is_also_survived(conn: sqlite3.Connection) -> None:
    _roster(conn, 2)
    ctx = _ctx(conn)
    ctx.bot.send_message = AsyncMock(side_effect=[BadRequest("chat not found"), None])
    await revote_nudge_job(ctx)
    recorded = {r["telegram_id"] for r in conn.execute("SELECT telegram_id FROM revote_nudges")}
    assert recorded == {2}


async def test_job_skips_entirely_while_events_are_paused(conn: sqlite3.Connection) -> None:
    _roster(conn, 3)
    pause_events_until(conn, datetime.now(UTC) + timedelta(days=5))
    ctx = _ctx(conn)
    await revote_nudge_job(ctx)
    ctx.bot.send_message.assert_not_awaited()


async def test_job_respects_the_cooldown(conn: sqlite3.Connection) -> None:
    _roster(conn, 2)
    record_nudge(conn, 1)
    ctx = _ctx(conn)
    await revote_nudge_job(ctx)
    assert 1 not in _dmed(ctx)
    assert 2 in _dmed(ctx)


async def test_ping_lists_who_was_dmed(conn: sqlite3.Connection) -> None:
    _roster(conn, 2)
    update = _admin_update()
    await handle_revote_ping(update, _ctx(conn))
    reply = update.effective_message.reply_text.await_args.args[0]
    assert t("revote.ping_header") in reply
    assert "P1" in reply and "P2" in reply
    assert t("revote.ping_sent", sent=2, failed=0) in reply


async def test_ping_honours_the_cooldown_without_force(conn: sqlite3.Connection) -> None:
    _roster(conn, 2)
    record_nudge(conn, 1)
    record_nudge(conn, 2)
    update = _admin_update()
    await handle_revote_ping(update, _ctx(conn))
    assert update.effective_message.reply_text.await_args.args[0] == t("revote.ping_none")


async def test_ping_force_bypasses_the_cooldown(conn: sqlite3.Connection) -> None:
    _roster(conn, 2)
    record_nudge(conn, 1)
    record_nudge(conn, 2)
    ctx = _ctx(conn, args=["force"])
    await handle_revote_ping(_admin_update(), ctx)
    assert _dmed(ctx) == {1, 2}


async def test_ping_reports_failures_in_the_summary(conn: sqlite3.Connection) -> None:
    _roster(conn, 2)
    ctx = _ctx(conn)
    ctx.bot.send_message = AsyncMock(side_effect=[Forbidden("blocked"), None])
    update = _admin_update()
    await handle_revote_ping(update, ctx)
    assert (
        t("revote.ping_sent", sent=1, failed=1)
        in (update.effective_message.reply_text.await_args.args[0])
    )


async def test_ping_rejects_non_admin(conn: sqlite3.Connection) -> None:
    _roster(conn, 2)
    update = _admin_update()
    update.effective_user = MagicMock(id=999)
    ctx = _ctx(conn)
    await handle_revote_ping(update, ctx)
    ctx.bot.send_message.assert_not_awaited()


async def test_ping_returns_without_message(conn: sqlite3.Connection) -> None:
    update = _admin_update()
    update.effective_message = None
    ctx = _ctx(conn)
    await handle_revote_ping(update, ctx)
    ctx.bot.send_message.assert_not_awaited()


def test_conn_raises_when_missing() -> None:
    ctx = MagicMock()
    ctx.bot_data = {}
    with pytest.raises(RuntimeError, match="DB connection missing"):
        _conn(ctx)
