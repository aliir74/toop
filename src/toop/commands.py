from __future__ import annotations

from dataclasses import dataclass

# Single source of truth for the bot's command set. Adding a command means:
# write the handler, register it in bot.py, and add ONE entry below. Both the
# Telegram `/` menu (via set_my_commands in bot.py's post_init) and the /help
# command render from this list, so they can't drift from each other.


@dataclass(frozen=True)
class BotCmd:
    """One bot command.

    `short` is the one-line blurb Telegram shows in the `/` autocomplete menu;
    `usage` is the fuller line /help renders (it carries the quoted-example
    syntax that won't fit in the menu). `admin` marks commands gated by the
    @require_admin decorator on their handler.
    """

    name: str
    short: str
    usage: str
    admin: bool


COMMANDS: tuple[BotCmd, ...] = (
    # Public — anyone who DMs the bot.
    BotCmd(
        "start",
        "Register and start rating",
        "/start — register with the bot and start rating teammates",
        admin=False,
    ),
    BotCmd(
        "vote",
        "Rate the next pair",
        "/vote — show the next pair of teammates to compare",
        admin=False,
    ),
    BotCmd(
        "help",
        "Show available commands",
        "/help — list the commands you can use",
        admin=False,
    ),
    # Admin-only — gated by @require_admin.
    BotCmd(
        "add_player",
        "Add a player to the roster",
        '/add_player @username "Display Name"  (or /add_player <telegram_id> "Display Name")',
        admin=True,
    ),
    BotCmd(
        "remove_player",
        "Remove a player from the roster",
        "/remove_player (no args) for buttons, or /remove_player @username",
        admin=True,
    ),
    BotCmd(
        "pause_voting",
        "Pause rating a player",
        "/pause_voting <@username|telegram_id> <duration like 2w or 10d>",
        admin=True,
    ),
    BotCmd(
        "disable_voting",
        "Stop rating a player indefinitely",
        "/disable_voting <@username|telegram_id>",
        admin=True,
    ),
    BotCmd(
        "enable_voting",
        "Restore a player to the rating pool",
        "/enable_voting <@username|telegram_id>",
        admin=True,
    ),
    BotCmd(
        "dk_report",
        "Don't-know rate report",
        "/dk_report — list each player's don't-know rate, highest first",
        admin=True,
    ),
    BotCmd(
        "add_ghost",
        "Add an accountless player",
        '/add_ghost "Display Name"',
        admin=True,
    ),
    BotCmd(
        "link_player",
        "Link a ghost to a real account",
        "/link_player <ghost_id> <@username|real_telegram_id>",
        admin=True,
    ),
    BotCmd(
        "list_players",
        "List the active roster",
        "/list_players — show the active roster",
        admin=True,
    ),
    BotCmd(
        "rename",
        "Rename a player's display name",
        '/rename (no args) for buttons, or /rename <@username|telegram_id> "New Name"',
        admin=True,
    ),
    BotCmd(
        "contacts",
        "List everyone who DM'd the bot",
        "/contacts — list everyone who has DM'd the bot, flagging who's not on the roster",
        admin=True,
    ),
    BotCmd(
        "open_session",
        "Open a session",
        "/open_session [YYYY-MM-DD]  (defaults to the next session weekday)",
        admin=True,
    ),
    BotCmd(
        "close_session",
        "Close the open session",
        "/close_session — close the currently open session",
        admin=True,
    ),
    BotCmd(
        "sessions",
        "List recent sessions",
        "/sessions — list recent sessions and their status",
        admin=True,
    ),
    BotCmd(
        "lock_in",
        "Force a player's RSVP to yes",
        "/lock_in (no args) for buttons, or /lock_in @username  (or /lock_in <telegram_id>)",
        admin=True,
    ),
    BotCmd(
        "nudge",
        "Draft nudges for low-completion voters",
        "/nudge — copy/paste templates to nudge the lowest-completion voters",
        admin=True,
    ),
    BotCmd(
        "refresh_ratings",
        "Recompute composite ratings",
        "/refresh_ratings — refit composite ratings from the current votes",
        admin=True,
    ),
    BotCmd(
        "snapshot",
        "Generate balanced teams",
        "/snapshot — build balanced teams from the current yes-RSVPs",
        admin=True,
    ),
    BotCmd(
        "teams",
        "Show the latest teams",
        "/teams — show the most recent team snapshot",
        admin=True,
    ),
    BotCmd(
        "swap",
        "Swap two players between teams",
        "/swap @player_a @player_b",
        admin=True,
    ),
    BotCmd(
        "publish",
        "Publish teams to the group",
        "/publish — post the current teams to the group chat",
        admin=True,
    ),
    BotCmd(
        "health",
        "Data and rating health check",
        "/health — show data and rating health metrics",
        admin=True,
    ),
    BotCmd(
        "coverage",
        "Rating coverage report",
        "/coverage — show the most under-sampled pairs per axis",
        admin=True,
    ),
    BotCmd(
        "version",
        "Show commit and uptime",
        "/version — show the running commit SHA and uptime",
        admin=True,
    ),
    BotCmd(
        "backup_db",
        "Back up the database",
        "/backup_db — write a timestamped SQLite backup",
        admin=True,
    ),
)

PUBLIC_COMMANDS: tuple[BotCmd, ...] = tuple(c for c in COMMANDS if not c.admin)
ADMIN_COMMANDS: tuple[BotCmd, ...] = tuple(c for c in COMMANDS if c.admin)


def menu_commands(*, admin: bool) -> tuple[BotCmd, ...]:
    """Commands to show in a chat's `/` menu.

    The admin sees the full list (admin chat scope replaces the default scope
    for that chat, so the public commands must be included too); everyone else
    sees only the public subset.
    """
    return COMMANDS if admin else PUBLIC_COMMANDS


def render_help(*, admin: bool) -> str:
    """Render the /help body from the command list.

    Admins get every command; non-admins get only the public ones.
    """
    cmds = COMMANDS if admin else PUBLIC_COMMANDS
    lines = [c.usage for c in cmds]
    return "\n".join(lines)
