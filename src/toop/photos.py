"""Local byte backup for player profile photos.

The bot stores Telegram's reusable ``file_id`` in ``players.photo_file_id`` as the
source of truth, but ``file_id``s are bound to the bot: recreating the bot from
scratch (not merely revoking the token) invalidates every one. These on-disk
copies under ``PHOTOS_DIR`` make a bulk re-upload trivial if that ever happens.
"""

from __future__ import annotations

from pathlib import Path

from toop.config import settings


def _photo_path(telegram_id: int) -> Path:
    return Path(settings.PHOTOS_DIR) / f"{int(telegram_id)}.jpg"


def save_photo_bytes(telegram_id: int, raw: bytes) -> Path:
    """Write the original uploaded image to ``<PHOTOS_DIR>/<id>.jpg``, returning
    the path. Creates the directory on first use and overwrites on re-send.
    Negative ghost ids are valid filenames."""
    path = _photo_path(telegram_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return path


def delete_photo_bytes(telegram_id: int) -> None:
    """Remove a player's backup image. No-op when it doesn't exist (called by
    /unset_photo)."""
    _photo_path(telegram_id).unlink(missing_ok=True)
