from __future__ import annotations

from dataclasses import dataclass

from toop.i18n import t

# Single source of truth for the bot's command set. Adding a command means:
# write the handler, register it in bot.py, and add ONE entry below. Both the
# Telegram `/` menu (via set_my_commands in bot.py's post_init) and the /help
# command render from this list, so they can't drift from each other. The
# descriptions are translated — `short`/`usage` are catalog keys resolved
# through i18n.t() at the active language; only the latin `name` is fixed
# (Telegram requires ascii `/commands`).


@dataclass(frozen=True)
class BotCmd:
    """One bot command.

    `name` is the latin command (never translated). `short_key`/`usage_key` are
    i18n catalog keys: `short` is the one-line blurb Telegram shows in the `/`
    autocomplete menu; `usage` is the fuller line /help renders. `admin` marks
    commands gated by the @require_admin decorator on their handler.
    """

    name: str
    short_key: str
    usage_key: str
    admin: bool

    def short(self, lang: str | None = None) -> str:
        return t(self.short_key, lang)

    def usage(self, lang: str | None = None) -> str:
        return t(self.usage_key, lang)


def _cmd(name: str, *, admin: bool) -> BotCmd:
    """Build a BotCmd whose description keys follow the cmd.<name>.* convention."""
    return BotCmd(name, f"cmd.{name}.short", f"cmd.{name}.usage", admin)


COMMANDS: tuple[BotCmd, ...] = (
    # Public — anyone who DMs the bot.
    _cmd("start", admin=False),
    _cmd("vote", admin=False),
    _cmd("help", admin=False),
    # Admin-only — gated by @require_admin.
    _cmd("add_player", admin=True),
    _cmd("remove_player", admin=True),
    _cmd("pause_voting", admin=True),
    _cmd("disable_voting", admin=True),
    _cmd("enable_voting", admin=True),
    _cmd("dk_report", admin=True),
    _cmd("add_ghost", admin=True),
    _cmd("link_player", admin=True),
    _cmd("list_players", admin=True),
    _cmd("rename", admin=True),
    _cmd("contacts", admin=True),
    _cmd("open_session", admin=True),
    _cmd("sessions", admin=True),
    _cmd("nudge", admin=True),
    _cmd("snapshot", admin=True),
    _cmd("swap", admin=True),
    _cmd("change_player", admin=True),
    _cmd("publish", admin=True),
    _cmd("health", admin=True),
    _cmd("coverage", admin=True),
    _cmd("version", admin=True),
    _cmd("backup_db", admin=True),
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


def render_help(*, admin: bool, lang: str | None = None) -> str:
    """Render the /help body from the command list, in the active language.

    Admins get every command; non-admins get only the public ones.
    """
    cmds = COMMANDS if admin else PUBLIC_COMMANDS
    lines = [c.usage(lang) for c in cmds]
    return "\n".join(lines)
