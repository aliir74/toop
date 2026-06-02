from __future__ import annotations

from toop.commands import (
    ADMIN_COMMANDS,
    COMMANDS,
    PUBLIC_COMMANDS,
    menu_commands,
    render_help,
)


def test_public_and_admin_partition_the_command_list() -> None:
    # Every command is in exactly one of the two buckets.
    assert set(PUBLIC_COMMANDS) | set(ADMIN_COMMANDS) == set(COMMANDS)
    assert set(PUBLIC_COMMANDS) & set(ADMIN_COMMANDS) == set()
    assert all(not c.admin for c in PUBLIC_COMMANDS)
    assert all(c.admin for c in ADMIN_COMMANDS)


def test_public_commands_are_start_vote_help() -> None:
    assert {c.name for c in PUBLIC_COMMANDS} == {"start", "vote", "help"}


def test_command_names_are_unique() -> None:
    names = [c.name for c in COMMANDS]
    assert len(names) == len(set(names))


def test_menu_commands_admin_sees_everything() -> None:
    assert menu_commands(admin=True) == COMMANDS


def test_menu_commands_non_admin_sees_public_only() -> None:
    assert menu_commands(admin=False) == PUBLIC_COMMANDS


def test_render_help_admin_includes_admin_command() -> None:
    body = render_help(admin=True)
    assert "/backup_db" in body
    assert "/start" in body
    # One line per command.
    assert body.count("\n") == len(COMMANDS) - 1


def test_render_help_non_admin_hides_admin_commands() -> None:
    body = render_help(admin=False)
    assert "/backup_db" not in body
    assert "/start" in body
    assert "/vote" in body
    assert body.count("\n") == len(PUBLIC_COMMANDS) - 1
