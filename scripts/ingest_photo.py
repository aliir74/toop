"""Store a photo someone sent you as a player's bot profile picture.

``/set_photo`` is the intended path, but it is an interactive inline-button flow
in the admin's own DM with the bot: tap a player, then send the image. That is
fine for one or two and tedious for twenty, and nothing outside Telegram can
drive it (a user-account CLI can send messages but cannot press a bot's buttons).

This is the same operation as a one-liner over a local file. It uploads the image
to the admin chat to mint a ``file_id`` (Bot API photos have no other way to get
one), then stores it exactly the way ``handle_set_photo_photo`` does: the
``file_id`` into ``players.photo_file_id`` as the source of truth, plus the byte
backup under ``PHOTOS_DIR`` for a from-scratch bot rebuild.

    # who still needs one
    uv run python scripts/ingest_photo.py --list

    # store it
    uv run python scripts/ingest_photo.py 137207815 ~/Downloads/ghasem.jpg

Ghost players work too, negative id and all, which ``--dump-avatars`` on
``pull_profile_photos.py`` cannot help with since a ghost has no account.

The upload lands in the admin's own chat with the bot. That is a real message and
the only way the Bot API will hand back a reusable ``file_id``; it is a
self-notification to the admin, never to the player.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import sqlite3
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from toop.config import settings
from toop.photos import save_photo_bytes
from toop.players import set_player_photo

API = "https://api.telegram.org"


def _post_photo(chat_id: int, raw: bytes, filename: str) -> dict:
    """Upload one photo as multipart/form-data and return the API envelope.

    Hand-rolled because the project has no requests dependency and this is the
    only multipart call in the codebase.
    """
    boundary = f"----------{uuid.uuid4().hex}"
    ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    pre = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="chat_id"\r\n\r\n{chat_id}\r\n'
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="photo"; filename="{filename}"\r\n'
        f"Content-Type: {ctype}\r\n\r\n"
    ).encode()
    body = pre + raw + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        f"{API}/bot{settings.BOT_TOKEN}/sendPhoto",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read().decode())
        except ValueError:
            return {"ok": False, "description": f"HTTP {exc.code}"}
    except OSError as exc:
        return {"ok": False, "description": str(exc)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("telegram_id", type=int, nargs="?", help="player id (negative for a ghost)")
    parser.add_argument("image", nargs="?", help="path to the image file")
    parser.add_argument(
        "--list",
        action="store_true",
        help="list active players with no photo and exit",
    )
    args = parser.parse_args()

    conn = sqlite3.connect(settings.DATABASE_PATH)
    conn.row_factory = sqlite3.Row

    if args.list:
        rows = conn.execute(
            "SELECT telegram_id, username, display_name, is_ghost FROM players "
            "WHERE active=1 AND photo_file_id IS NULL ORDER BY display_name COLLATE NOCASE"
        ).fetchall()
        for row in rows:
            handle = f"@{row['username']}" if row["username"] else "(no handle)"
            ghost = "  ghost" if row["is_ghost"] else ""
            print(f"  {row['telegram_id']:>12}  {row['display_name']:<26} {handle}{ghost}")
        print(f"\n{len(rows)} player(s) still without a photo")
        return 0

    if args.telegram_id is None or args.image is None:
        parser.error("telegram_id and image are required unless --list is given")

    path = Path(args.image).expanduser()
    if not path.is_file():
        print(f"no such file: {path}")
        return 1

    row = conn.execute(
        "SELECT display_name FROM players WHERE telegram_id=? AND active=1",
        (args.telegram_id,),
    ).fetchone()
    if row is None:
        print(f"no active player with id {args.telegram_id} (try --list)")
        return 1

    raw = path.read_bytes()
    sent = _post_photo(settings.ADMIN_TELEGRAM_ID, raw, path.name)
    if not sent.get("ok"):
        print(f"upload failed: {sent.get('description')}")
        return 1
    # Largest PhotoSize is the full-resolution one; earlier entries are thumbs.
    file_id = sent["result"]["photo"][-1]["file_id"]

    try:
        save_photo_bytes(args.telegram_id, raw)
    except OSError as exc:
        print(f"(backup failed, file_id still stored: {exc})")

    name = set_player_photo(conn, args.telegram_id, file_id)
    print(f"stored photo for {name} ({args.telegram_id})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
