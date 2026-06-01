from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from toop.handlers.ops import _format_uptime, handle_backup_db, handle_version


@pytest.fixture(autouse=True)
def patch_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("toop.admin.settings", MagicMock(ADMIN_TELEGRAM_ID=42))


def _admin_update() -> MagicMock:
    u = MagicMock()
    u.effective_user = MagicMock(id=42)
    msg = MagicMock()
    msg.reply_text = AsyncMock()
    u.effective_message = msg
    return u


def _ctx(conn: sqlite3.Connection, db_path: Path | None = None) -> MagicMock:
    ctx = MagicMock()
    bot_data: dict = {"conn": conn}
    bot_data["started_at"] = datetime.now(UTC) - timedelta(hours=2, minutes=3, seconds=4)
    ctx.bot_data = bot_data
    return ctx


def test_format_uptime() -> None:
    assert _format_uptime(0) == "0s"
    assert _format_uptime(59) == "59s"
    assert _format_uptime(60) == "1m 0s"
    assert _format_uptime(3661) == "1h 1m 1s"
    assert _format_uptime(86400) == "1d 0s"


async def test_version_reports_sha_and_uptime(conn: sqlite3.Connection) -> None:
    update = _admin_update()
    await handle_version(update, _ctx(conn))
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "commit" in reply
    assert "uptime" in reply
    assert "2h" in reply  # from the 2h fixture offset


async def test_backup_db_writes_timestamped_copy(
    conn: sqlite3.Connection,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    db_path: Path,
) -> None:
    monkeypatch.setattr("toop.handlers.ops.settings", MagicMock(DATABASE_PATH=str(db_path)))
    update = _admin_update()
    await handle_backup_db(update, _ctx(conn))
    backup_dir = db_path.parent / "backups"
    assert backup_dir.exists()
    backups = list(backup_dir.glob("toop-*.db"))
    assert len(backups) == 1
    # The copy should be a valid SQLite file containing the same tables.
    cp = sqlite3.connect(str(backups[0]))
    try:
        tables = {r[0] for r in cp.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "players" in tables
    finally:
        cp.close()


async def test_backup_db_missing_path_friendly_error(
    conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "toop.handlers.ops.settings",
        MagicMock(DATABASE_PATH=str(tmp_path / "does_not_exist.db")),
    )
    update = _admin_update()
    await handle_backup_db(update, _ctx(conn))
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "not found" in reply


# ----- branch coverage additions -----

from toop.handlers.ops import _commit_sha, _conn  # noqa: E402


def test_conn_raises_when_missing() -> None:
    ctx = MagicMock()
    ctx.bot_data = {}
    with pytest.raises(RuntimeError, match="DB connection missing"):
        _conn(ctx)


def test_commit_sha_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_SHA", "abc1234")
    assert _commit_sha() == "abc1234"


def test_commit_sha_subprocess_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GIT_SHA", raising=False)

    def boom(*a: object, **k: object) -> None:
        raise FileNotFoundError

    monkeypatch.setattr("toop.handlers.ops.subprocess.run", boom)
    assert _commit_sha() == "unknown"


async def test_version_returns_without_message(conn: sqlite3.Connection) -> None:
    u = MagicMock()
    u.effective_user = MagicMock(id=42)
    u.effective_message = None
    await handle_version(u, _ctx(conn))


async def test_backup_db_returns_without_message(conn: sqlite3.Connection) -> None:
    u = MagicMock()
    u.effective_user = MagicMock(id=42)
    u.effective_message = None
    await handle_backup_db(u, _ctx(conn))
