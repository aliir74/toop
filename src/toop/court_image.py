"""Court picture of both teams' suggested starting lineups (see lineup.py).

The court is drawn as it stands: Team A on the far side facing down, Team B on
the near side facing up, so each team's left (positions 4 and 5) is on its own
left. A player with a /set_photo picture shows it in their circle; everyone else
gets their initials. Text inside the picture stays Latin (A, B, NET, SET):
Pillow without libraqm cannot shape Persian, so the explanation goes in the
Telegram caption instead.
"""

from __future__ import annotations

import io
import logging
import sqlite3
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError
from telegram import Bot
from telegram.error import TelegramError

from toop.lineup import Lineup
from toop.photos import photo_path

logger = logging.getLogger(__name__)

_W, _H = 1440, 2360
_MARGIN = 80
_COURT = (_MARGIN, 220, _W - _MARGIN, _H - 220)
_R = 92  # player circle radius
_BG = "#0f172a"
_FLOOR = "#e8a25c"
_SETTER = "#facc15"
_COLORS = {"A": "#2563eb", "B": "#dc2626"}
_FRONT = (4, 3, 2)
_BACK = (5, 6, 1)


@dataclass(frozen=True)
class CourtTeam:
    key: str  # "A" or "B"
    lineup: Lineup
    names: dict[int, str]
    photos: dict[int, bytes]


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    return ImageFont.load_default(size)


def _initials(name: str) -> str:
    words = name.replace(".", " ").split()
    return "".join(w[0] for w in words[:2]).upper() or "?"


def _fit_name(draw: ImageDraw.ImageDraw, name: str, max_w: float) -> tuple[str, int]:
    """Largest font size (down to 24) that fits; past that, truncate with an ellipsis."""
    for size in range(40, 23, -2):
        if draw.textlength(name, font=_font(size)) <= max_w:
            return name, size
    while len(name) > 1 and draw.textlength(name + "…", font=_font(24)) > max_w:
        name = name[:-1]
    return name + "…", 24


def _photo_disc(raw: bytes, diameter: int) -> Image.Image | None:
    try:
        pic = Image.open(io.BytesIO(raw)).convert("RGB")
    except (UnidentifiedImageError, OSError):
        return None
    pic = ImageOps.fit(pic, (diameter, diameter))
    mask = Image.new("L", (diameter, diameter), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, diameter - 1, diameter - 1), fill=255)
    disc = Image.new("RGBA", (diameter, diameter))
    disc.paste(pic, (0, 0), mask)
    return disc


def _ball(draw: ImageDraw.ImageDraw, x: float, y: float, r: int) -> None:
    draw.ellipse((x - r, y - r, x + r, y + r), fill="white", outline="#111827", width=5)
    draw.arc((x - r, y - 2 * r, x + r, y), 30, 150, fill="#111827", width=4)
    draw.arc((x - r, y, x + r, y + 2 * r), 210, 330, fill="#111827", width=4)


def _player(img: Image.Image, team: CourtTeam, pid: int, pos: int, x: float, y: float) -> None:
    draw = ImageDraw.Draw(img)
    color = _COLORS[team.key]
    setter = pid in team.lineup.setters
    ring = 18 if setter else 8
    draw.ellipse(
        (x - _R - ring, y - _R - ring, x + _R + ring, y + _R + ring),
        fill=_SETTER if setter else "white",
    )
    draw.ellipse((x - _R, y - _R, x + _R, y + _R), fill=color)
    raw = team.photos.get(pid)
    disc = _photo_disc(raw, 2 * _R) if raw else None
    name = team.names.get(pid, f"#{pid}")
    if disc is not None:
        img.paste(disc, (int(x - _R), int(y - _R)), disc)
    else:
        draw.text((x, y), _initials(name), font=_font(64), fill="white", anchor="mm")
    # position number, top-left
    bx, by = x - _R + 4, y - _R + 4
    draw.ellipse((bx - 30, by - 30, bx + 30, by + 30), fill="white", outline="#111827", width=3)
    draw.text((bx, by), str(pos), font=_font(38), fill="#111827", anchor="mm")
    if setter:
        sx, sy = x + _R - 10, y - _R + 4
        draw.rounded_rectangle((sx - 52, sy - 26, sx + 52, sy + 26), radius=14, fill=_SETTER)
        draw.text((sx, sy), "SET", font=_font(30), fill="#111827", anchor="mm")
    if pos == 1:
        _ball(draw, x + _R - 6, y + _R - 6, 26)
    text, size = _fit_name(draw, name, (_COURT[2] - _COURT[0]) / 3 - 24)
    draw.text(
        (x, y + _R + ring + 34),
        text,
        font=_font(size),
        fill="white",
        anchor="mm",
        stroke_width=7,
        stroke_fill=_BG,
    )


def render_court(team_a: CourtTeam, team_b: CourtTeam) -> bytes:
    """PNG bytes of both lineups on one court, Team A on top."""
    img = Image.new("RGB", (_W, _H), _BG)
    draw = ImageDraw.Draw(img)
    x0, y0, x1, y1 = _COURT
    draw.rounded_rectangle(_COURT, radius=20, fill=_FLOOR, outline="white", width=10)
    net = (y0 + y1) // 2
    half = (y1 - y0) // 2
    for y in (net - half // 3, net + half // 3):  # 3 m attack lines
        draw.line((x0, y, x1, y), fill="white", width=6)
    draw.rectangle((x0 - 28, net - 10, x1 + 28, net + 10), fill="#111827")
    draw.text(
        (_W // 2, net), "NET", font=_font(34), fill="white", anchor="mm",
        stroke_width=8, stroke_fill="#111827",
    )  # fmt: skip
    col_w = (x1 - x0) / 3
    for team, top in ((team_a, True), (team_b, False)):
        sign = -1 if top else 1
        rows = ((_FRONT, net + sign * half * 0.2), (_BACK, net + sign * half * 0.66))
        for positions, y in rows:
            # A faces down the page, so its left (4, 5) is on the page's right.
            ordered = positions[::-1] if top else positions
            for i, pos in enumerate(ordered):
                x = x0 + col_w * (i + 0.5)
                _player(img, team, team.lineup.court[pos], pos, x, y)
        label_y = 110 if top else _H - 110
        draw.text(
            (_W // 2, label_y), team.key, font=_font(110), fill=_COLORS[team.key], anchor="mm"
        )
    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue()


async def load_photos(bot: Bot, conn: sqlite3.Connection, pids: list[int]) -> dict[int, bytes]:
    """Each player's /set_photo picture: the on-disk backup first, else downloaded
    from Telegram by file_id. Players with no photo, or whose download fails,
    are left out (they get initials)."""
    photos: dict[int, bytes] = {}
    for pid in pids:
        path = photo_path(pid)
        if path.is_file():
            photos[pid] = path.read_bytes()
            continue
        row = conn.execute(
            "SELECT photo_file_id FROM players WHERE telegram_id=?", (pid,)
        ).fetchone()
        if row is None or not row["photo_file_id"]:
            continue
        try:
            tg_file = await bot.get_file(row["photo_file_id"])
            photos[pid] = bytes(await tg_file.download_as_bytearray())
        except TelegramError as exc:
            logger.warning("court image: photo for %s unavailable: %s", pid, exc)
    return photos
