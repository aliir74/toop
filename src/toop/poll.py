from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from toop.i18n import t
from toop.rsvp import upsert_rsvp

# Attendance poll options, in order. Index 0 (yes) means attending — the index
# semantics are language-independent; only the labels are translated.
ATTENDANCE_YES_INDEX = 0
# 'reservation' is the retired waitlist poll; kept so pre-existing rows still load.
POLL_KINDS = ("attendance", "reservation")


def attendance_question(lang: str | None = None) -> str:
    return t("poll.attendance_question", lang)


def attendance_options(lang: str | None = None) -> list[str]:
    return [t("poll.attendance_yes", lang), t("poll.attendance_no", lang)]


def capacity_message(lang: str | None = None) -> str:
    return t("poll.capacity", lang)


@dataclass(frozen=True)
class PollRow:
    poll_id: str
    session_id: int
    kind: str
    message_id: int | None
    closed: bool
    quorum_announced: bool
    cap_announced: bool
    roster_message_id: int | None


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
        "SELECT poll_id, session_id, kind, message_id, closed, quorum_announced, cap_closed, "
        "roster_message_id FROM session_polls WHERE poll_id=?",
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
        cap_announced=bool(row["cap_closed"]),
        roster_message_id=row["roster_message_id"],
    )


def quorum_message(amount: str, email: str, sheet_url: str, lang: str | None = None) -> str:
    """The 'it's happening + pay' announcement posted once quorum is reached."""
    lines = [t("poll.quorum_header", lang)]
    if email:
        lines.append(t("poll.quorum_payment", lang, amount=amount, email=email))
        lines.append(t("poll.quorum_like", lang))
    if sheet_url:
        lines.append(t("poll.quorum_sheet", lang, sheet=sheet_url))
    return "\n".join(lines)


def set_quorum_announced(conn: sqlite3.Connection, poll_id: str) -> None:
    conn.execute("UPDATE session_polls SET quorum_announced=1 WHERE poll_id=?", (poll_id,))
    conn.commit()


def set_cap_announced(conn: sqlite3.Connection, poll_id: str) -> None:
    """Latch the one-shot capacity announcement. The poll itself stays open so
    voters can still retract; yes votes past the cap become the waitlist."""
    conn.execute("UPDATE session_polls SET cap_closed=1 WHERE poll_id=?", (poll_id,))
    conn.commit()


def set_roster_message_id(conn: sqlite3.Connection, poll_id: str, message_id: int) -> None:
    conn.execute(
        "UPDATE session_polls SET roster_message_id=? WHERE poll_id=?", (message_id, poll_id)
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
    counts as neither. The voter must already be a registered player — the poll
    handler auto-registers unknown voters first — so every vote in the group
    poll counts toward quorum/capacity, not just rostered players'. Returns True.
    """
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


def voters_by_status(conn: sqlite3.Connection, session_id: int, status: str) -> list[int]:
    """Telegram ids with this RSVP status, in vote order."""
    rows = conn.execute(
        "SELECT telegram_id FROM rsvps WHERE session_id=? AND status=? "
        "ORDER BY created_at, telegram_id",
        (session_id, status),
    ).fetchall()
    return [r["telegram_id"] for r in rows]


def roster_message(
    attending: list[str],
    waitlist: list[str],
    declined: list[str],
    cap: int,
    lang: str | None = None,
) -> str:
    """The live voter list posted under the attendance poll.

    Telegram hides who voted until you vote yourself; this message shows
    everyone the attendees, waitlist and 'no' votes as they stand.
    """
    lines = [t("poll.roster_header", lang, n=len(attending), cap=cap)]
    if attending:
        lines += [f"{i}. {name}" for i, name in enumerate(attending, 1)]
    else:
        lines.append(t("poll.roster_empty", lang))
    if waitlist:
        lines.append("")
        lines.append(t("poll.roster_waitlist", lang, n=len(waitlist)))
        lines += [f"{i}. {name}" for i, name in enumerate(waitlist, 1)]
    if declined:
        lines.append("")
        lines.append(t("poll.roster_declined", lang, n=len(declined), names=", ".join(declined)))
    return "\n".join(lines)
