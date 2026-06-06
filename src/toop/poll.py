from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from toop.rsvp import is_player_on_roster, upsert_rsvp

# The attendance poll's options, in order. Index 0 (بلی / "yes") means attending.
ATTENDANCE_OPTIONS: tuple[str, str] = ("بلی", "خیر")
ATTENDANCE_YES_INDEX = 0

POLL_KINDS = ("attendance", "reservation")


@dataclass(frozen=True)
class PollRow:
    poll_id: str
    session_id: int
    kind: str
    message_id: int | None
    closed: bool
    quorum_announced: bool
    cap_closed: bool


def record_poll(
    conn: sqlite3.Connection,
    *,
    session_id: int,
    poll_id: str,
    kind: str,
    message_id: int | None,
) -> None:
    """Persist a bot-owned poll so a later poll_answer maps poll_id → session."""
    if kind not in POLL_KINDS:
        raise ValueError(f"kind must be one of {POLL_KINDS}, got {kind!r}")
    conn.execute(
        """
        INSERT INTO session_polls (poll_id, session_id, kind, message_id)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(poll_id) DO UPDATE SET
            session_id=excluded.session_id,
            kind=excluded.kind,
            message_id=excluded.message_id
        """,
        (poll_id, session_id, kind, message_id),
    )
    conn.commit()


def get_poll(conn: sqlite3.Connection, poll_id: str) -> PollRow | None:
    row = conn.execute(
        "SELECT poll_id, session_id, kind, message_id, closed, quorum_announced, cap_closed "
        "FROM session_polls WHERE poll_id=?",
        (poll_id,),
    ).fetchone()
    if row is None:
        return None
    return PollRow(
        poll_id=row["poll_id"],
        session_id=row["session_id"],
        kind=row["kind"],
        message_id=row["message_id"],
        closed=bool(row["closed"]),
        quorum_announced=bool(row["quorum_announced"]),
        cap_closed=bool(row["cap_closed"]),
    )


CAPACITY_MESSAGE = "ظرفیت تکمیل شد."


def quorum_message(amount: str, email: str, sheet_url: str) -> str:
    """The 'it's happening + pay' announcement posted once quorum is reached."""
    lines = ["🎉 والیبال برگزار می‌شود 🏐"]
    if email:
        lines.append(
            f"\nلطفا در صورتی که در رای‌گیری حضور اعلام کرده‌اید مبلغ {amount} دلار "
            f"به ایمیل زیر ای-ترنسفر کنید:\n{email}"
        )
        lines.append("\nدوستان لطفا بعد از ارسال هزینه این پیام را لایک کنید.")
    if sheet_url:
        lines.append(f"\nجدول حسابداری:\n{sheet_url}")
    return "\n".join(lines)


def set_quorum_announced(conn: sqlite3.Connection, poll_id: str) -> None:
    conn.execute("UPDATE session_polls SET quorum_announced=1 WHERE poll_id=?", (poll_id,))
    conn.commit()


def set_cap_closed(conn: sqlite3.Connection, poll_id: str) -> None:
    """Latch the attendance poll closed (cap reached). Sets both flags."""
    conn.execute(
        "UPDATE session_polls SET cap_closed=1, closed=1 WHERE poll_id=?",
        (poll_id,),
    )
    conn.commit()


def record_attendance_answer(
    conn: sqlite3.Connection,
    session_id: int,
    voter_id: int,
    option_ids: list[int],
) -> bool:
    """Apply one attendance poll_answer to the rsvps table.

    `option_ids` is what Telegram delivers: the chosen option indices, or an
    empty list when the voter retracts. A 'yes' (بلی) writes status='yes'; any
    other choice writes 'no'; a retraction removes the row entirely so the voter
    counts as neither. Returns False (a no-op) when the voter isn't on the
    roster, so off-roster taps never touch attendance.
    """
    if not is_player_on_roster(conn, voter_id):
        return False
    if not option_ids:
        conn.execute(
            "DELETE FROM rsvps WHERE session_id=? AND telegram_id=?",
            (session_id, voter_id),
        )
        conn.commit()
        return True
    status = "yes" if ATTENDANCE_YES_INDEX in option_ids else "no"
    upsert_rsvp(conn, session_id, voter_id, status)
    return True
