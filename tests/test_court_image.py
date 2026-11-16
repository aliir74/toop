from __future__ import annotations

import io
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from PIL import Image
from telegram.error import TelegramError

from toop import photos as photos_mod
from toop.court_image import CourtTeam, _fit_name, _font, load_photos, render_court
from toop.lineup import Lineup
from toop.players import add_player, set_player_photo

MAGENTA = (255, 0, 255)
CYAN = (0, 255, 255)


def _jpeg(color: tuple[int, int, int]) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (300, 300), color).save(out, format="PNG")
    return out.getvalue()


def _lineup(base: int) -> Lineup:
    # court position -> player id; base+4 stands at position 4.
    return Lineup(
        court={pos: base + pos for pos in range(1, 7)},
        setters=(base + 1, base + 4),
        worst_rotation=0.0,
    )


def _names(base: int) -> dict[int, str]:
    return {base + pos: f"Player {base + pos}" for pos in range(1, 7)}


def _pixels_of(png: bytes, color: tuple[int, int, int]) -> list[tuple[int, int]]:
    img = Image.open(io.BytesIO(png)).convert("RGB")
    w, h = img.size
    data = img.load()
    assert data is not None
    return [(x, y) for y in range(0, h, 4) for x in range(0, w, 4) if data[x, y] == color]


def test_each_team_stands_on_its_own_side_facing_the_net() -> None:
    # Team A's position-4 player (front-left from A's view, facing down the
    # page) must appear top-right; Team B's position-4 player bottom-left.
    team_a = CourtTeam("A", _lineup(10), _names(10), {14: _jpeg(MAGENTA)})
    team_b = CourtTeam("B", _lineup(20), _names(20), {24: _jpeg(CYAN)})
    png = render_court(team_a, team_b)
    w, h = Image.open(io.BytesIO(png)).size

    a_px = _pixels_of(png, MAGENTA)
    b_px = _pixels_of(png, CYAN)
    assert a_px and b_px, "photos must be drawn in the player circles"
    assert sum(x for x, _ in a_px) / len(a_px) > w / 2
    assert sum(y for _, y in a_px) / len(a_px) < h / 2
    assert sum(x for x, _ in b_px) / len(b_px) < w / 2
    assert sum(y for _, y in b_px) / len(b_px) > h / 2


def test_players_without_a_photo_get_no_photo_pixels() -> None:
    png = render_court(
        CourtTeam("A", _lineup(10), _names(10), {}),
        CourtTeam("B", _lineup(20), _names(20), {}),
    )
    assert not _pixels_of(png, MAGENTA)
    assert Image.open(io.BytesIO(png)).format == "PNG"


def test_broken_photo_bytes_fall_back_to_initials() -> None:
    png = render_court(
        CourtTeam("A", _lineup(10), _names(10), {14: b"not an image"}),
        CourtTeam("B", _lineup(20), _names(20), {}),
    )
    assert Image.open(io.BytesIO(png)).size[0] > 0


def test_long_names_shrink_then_truncate() -> None:
    from PIL import ImageDraw

    draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    short, big = _fit_name(draw, "Ali", 400)
    assert (short, big) == ("Ali", 40)
    text, size = _fit_name(draw, "Seyed Mohammad Esmaeil Hosseini Tabatabaei", 300)
    assert size == 24 and text.endswith("…")
    assert draw.textlength(text, font=_font(24)) <= 300


async def test_load_photos_prefers_disk_then_telegram(
    conn: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(photos_mod.settings, "PHOTOS_DIR", str(tmp_path))
    for pid in (1, 2, 3, 4):
        add_player(conn, pid, f"P{pid}", f"p{pid}")
    (tmp_path / "1.jpg").write_bytes(b"disk")
    set_player_photo(conn, 2, "file-2")
    set_player_photo(conn, 3, "file-3")

    async def get_file(file_id: str) -> MagicMock:
        if file_id == "file-3":
            raise TelegramError("gone")
        tg_file = MagicMock()
        tg_file.download_as_bytearray = AsyncMock(return_value=bytearray(b"tg"))
        return tg_file

    bot = MagicMock()
    bot.get_file = AsyncMock(side_effect=get_file)
    got = await load_photos(bot, conn, [1, 2, 3, 4, 99])
    assert got == {1: b"disk", 2: b"tg"}
