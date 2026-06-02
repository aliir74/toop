from __future__ import annotations

import sqlite3

from toop.contacts import (
    get_contact,
    list_addable_contacts,
    list_contacts,
    upsert_contact,
)
from toop.players import add_ghost_player, add_player


def test_upsert_inserts_new_contact(conn: sqlite3.Connection) -> None:
    upsert_contact(conn, 1, username="@Alice", display_name="Alice Smith")
    contacts = list_contacts(conn)
    assert len(contacts) == 1
    assert contacts[0].telegram_id == 1
    # Username normalized: stripped of @ and lowercased.
    assert contacts[0].username == "alice"
    assert contacts[0].display_name == "Alice Smith"


def test_get_contact_found_and_missing(conn: sqlite3.Connection) -> None:
    upsert_contact(conn, 7290468940, username=None, display_name="Meysam Bz")
    found = get_contact(conn, 7290468940)
    assert found is not None
    assert found.telegram_id == 7290468940
    assert found.username is None
    assert found.display_name == "Meysam Bz"
    assert get_contact(conn, 999) is None


def test_upsert_handles_missing_username_and_name(conn: sqlite3.Connection) -> None:
    upsert_contact(conn, 2)
    contacts = list_contacts(conn)
    assert contacts[0].username is None
    assert contacts[0].display_name is None


def test_upsert_conflict_refreshes_fields_and_bumps_last_seen(conn: sqlite3.Connection) -> None:
    upsert_contact(conn, 1, username="old", display_name="Old Name")
    first = conn.execute(
        "SELECT first_seen_at, last_seen_at FROM contacts WHERE telegram_id=1"
    ).fetchone()
    # Force a later timestamp so the bump is observable.
    conn.execute("UPDATE contacts SET last_seen_at=datetime('now', '-1 hour') WHERE telegram_id=1")
    conn.commit()
    upsert_contact(conn, 1, username="@New", display_name="New Name")
    row = conn.execute(
        "SELECT username, display_name, first_seen_at, last_seen_at "
        "FROM contacts WHERE telegram_id=1"
    ).fetchone()
    assert row["username"] == "new"
    assert row["display_name"] == "New Name"
    # first_seen_at is preserved; last_seen_at advances past the backdated value.
    assert row["first_seen_at"] == first["first_seen_at"]
    assert row["last_seen_at"] > "1970"
    assert len(list_contacts(conn)) == 1


def test_list_contacts_oldest_first(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO contacts (telegram_id, username, first_seen_at) "
        "VALUES (10, 'second', '2026-01-02 00:00:00')"
    )
    conn.execute(
        "INSERT INTO contacts (telegram_id, username, first_seen_at) "
        "VALUES (11, 'first', '2026-01-01 00:00:00')"
    )
    conn.commit()
    ids = [c.telegram_id for c in list_contacts(conn)]
    assert ids == [11, 10]


def test_list_addable_contacts_excludes_roster(conn: sqlite3.Connection) -> None:
    add_player(conn, 111, "Bob", "bob")
    upsert_contact(conn, 111, username="bob", display_name="Bob")  # on roster
    upsert_contact(conn, 222, username="newbie", display_name="New Bie")  # addable
    addable = list_addable_contacts(conn)
    assert [c.telegram_id for c in addable] == [222]


def test_list_addable_contacts_empty_when_all_on_roster(conn: sqlite3.Connection) -> None:
    add_player(conn, 111, "Bob", "bob")
    upsert_contact(conn, 111, username="bob", display_name="Bob")
    assert list_addable_contacts(conn) == []


def test_list_addable_contacts_oldest_first(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO contacts (telegram_id, username, first_seen_at) "
        "VALUES (10, 'second', '2026-01-02 00:00:00')"
    )
    conn.execute(
        "INSERT INTO contacts (telegram_id, username, first_seen_at) "
        "VALUES (11, 'first', '2026-01-01 00:00:00')"
    )
    conn.commit()
    assert [c.telegram_id for c in list_addable_contacts(conn)] == [11, 10]


def test_list_addable_contacts_ignores_ghosts(conn: sqlite3.Connection) -> None:
    # Ghosts have negative ids and no contact row — they never appear here.
    add_ghost_player(conn, "Ghosty")
    upsert_contact(conn, 222, username="newbie", display_name="New Bie")
    addable = list_addable_contacts(conn)
    assert [c.telegram_id for c in addable] == [222]


def test_contacts_table_holds_no_vote_columns(conn: sqlite3.Connection) -> None:
    """Privacy: contacts is a presence log only — never carries vote data."""
    cols = {c[1] for c in conn.execute("PRAGMA table_info(contacts)").fetchall()}
    assert cols == {
        "telegram_id",
        "username",
        "display_name",
        "first_seen_at",
        "last_seen_at",
    }
