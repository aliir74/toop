from __future__ import annotations

import sqlite3

from toop.contacts import upsert_contact
from toop.players import add_ghost_player, add_player, soft_remove_player
from toop.rating import INDICATORS
from toop.revote import nudge_targets, record_nudge
from toop.voting_queue import record_score

COOLDOWN = 7
MIN_PENDING = 3


def _targets(conn: sqlite3.Connection, **kw) -> list:
    return nudge_targets(conn, cooldown_days=COOLDOWN, min_pending=MIN_PENDING, **kw)


def _ids(conn: sqlite3.Connection, **kw) -> set[int]:
    return {t.telegram_id for t in _targets(conn, **kw)}


def _roster(conn: sqlite3.Connection, n: int) -> None:
    """n players, each a known contact so reachability is not the thing under test."""
    for i in range(1, n + 1):
        add_player(conn, i, f"P{i}", f"p{i}")
        upsert_contact(conn, i, username=f"p{i}", display_name=f"P{i}")


def test_target_owes_enough_to_be_worth_a_dm(conn: sqlite3.Connection) -> None:
    _roster(conn, 2)
    # Voter 1 owes all six of player 2's indicators.
    assert 1 in _ids(conn)


def test_below_min_pending_is_skipped(conn: sqlite3.Connection) -> None:
    _roster(conn, 2)
    for ind in INDICATORS[: len(INDICATORS) - 2]:
        record_score(conn, 1, 2, ind, 3)
    # Two left, under MIN_PENDING of 3.
    assert 1 not in _ids(conn)


def test_recent_nudge_is_inside_the_cooldown(conn: sqlite3.Connection) -> None:
    _roster(conn, 2)
    record_nudge(conn, 1)
    conn.execute("UPDATE revote_nudges SET last_nudged_at = datetime('now','-2 days')")
    conn.commit()
    assert 1 not in _ids(conn)


def test_old_nudge_is_due_again(conn: sqlite3.Connection) -> None:
    _roster(conn, 2)
    record_nudge(conn, 1)
    conn.execute("UPDATE revote_nudges SET last_nudged_at = datetime('now','-8 days')")
    conn.commit()
    assert 1 in _ids(conn)


def test_ignore_cooldown_overrides_a_fresh_nudge(conn: sqlite3.Connection) -> None:
    """What /revote_ping force waives — the DM cooldown, nothing else."""
    _roster(conn, 2)
    record_nudge(conn, 1)
    assert 1 not in _ids(conn)
    assert 1 in _ids(conn, ignore_cooldown=True)


def test_never_nudged_player_is_included(conn: sqlite3.Connection) -> None:
    _roster(conn, 2)
    assert conn.execute("SELECT COUNT(*) AS n FROM revote_nudges").fetchone()["n"] == 0
    assert 1 in _ids(conn)


def test_inactive_player_is_excluded(conn: sqlite3.Connection) -> None:
    _roster(conn, 3)
    soft_remove_player(conn, 1)
    assert 1 not in _ids(conn)


def test_ghost_player_is_excluded(conn: sqlite3.Connection) -> None:
    """Ghosts are active and in the rating pool by design so others can rate
    them, but they have a synthetic negative id and have never DM'd the bot."""
    _roster(conn, 2)
    ghost = add_ghost_player(conn, "Ghosty")
    assert ghost.telegram_id not in _ids(conn)


def test_unreachable_player_is_excluded(conn: sqlite3.Connection) -> None:
    """No contacts row and no score cast: the bot has no open DM with them, so
    a send would just raise Forbidden every week forever."""
    add_player(conn, 1, "Silent", "silent")
    add_player(conn, 2, "Other", "other")
    upsert_contact(conn, 2, username="other", display_name="Other")
    assert 1 not in _ids(conn)


def test_voter_with_scores_but_no_contact_row_is_reachable(conn: sqlite3.Connection) -> None:
    """contacts postdates the bot's first weeks and is only written by /start,
    so long-standing voters can have no row. Having voted proves an open DM."""
    add_player(conn, 1, "Oldtimer", "old")
    add_player(conn, 2, "Other", "other")
    add_player(conn, 3, "Third", "third")
    record_score(conn, 1, 2, "attack", 4)
    assert conn.execute("SELECT COUNT(*) AS n FROM contacts").fetchone()["n"] == 0
    assert 1 in _ids(conn)


def test_record_nudge_upserts_and_moves_the_clock(conn: sqlite3.Connection) -> None:
    _roster(conn, 2)
    record_nudge(conn, 1)
    conn.execute("UPDATE revote_nudges SET last_nudged_at = datetime('now','-30 days')")
    conn.commit()
    record_nudge(conn, 1)
    rows = conn.execute("SELECT last_nudged_at FROM revote_nudges").fetchall()
    assert len(rows) == 1
    fresh = conn.execute(
        "SELECT last_nudged_at > datetime('now','-1 day') AS ok FROM revote_nudges"
    ).fetchone()["ok"]
    assert fresh == 1


def test_target_carries_the_new_vs_stale_split(conn: sqlite3.Connection) -> None:
    _roster(conn, 3)
    for ind in INDICATORS:
        record_score(conn, 1, 2, ind, 3)
    conn.execute("UPDATE scores SET updated_at = datetime('now','-90 days')")
    conn.commit()
    target = next(t for t in _targets(conn) if t.telegram_id == 1)
    assert target.stale == len(INDICATORS)
    assert target.unscored == len(INDICATORS)
    assert target.total == 2 * len(INDICATORS)


def test_first_name_is_the_dm_opener(conn: sqlite3.Connection) -> None:
    add_player(conn, 1, "Ali Reza Irani", "ali")
    add_player(conn, 2, "Other", "other")
    upsert_contact(conn, 1, username="ali", display_name="Ali Reza Irani")
    assert next(t for t in _targets(conn) if t.telegram_id == 1).first_name == "Ali"


def test_first_name_falls_back_on_a_blank_name(conn: sqlite3.Connection) -> None:
    add_player(conn, 1, "   ", "blank")
    add_player(conn, 2, "Other", "other")
    upsert_contact(conn, 1, username="blank", display_name="   ")
    assert next(t for t in _targets(conn) if t.telegram_id == 1).first_name == "   "
