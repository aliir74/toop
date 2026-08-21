"""Backfill player photos from their existing Telegram profile picture.

``/set_photo`` is admin-driven: the admin taps a player, then sends a photo they
collected by hand. That is the only path today, so a roster of 20-odd players
means 20-odd DMs chasing images. Most people already have a profile picture that
the bot can read, and for anyone whose privacy setting allows it we can skip the
chase entirely.

This walks every active player with ``photo_file_id IS NULL``, asks
``getUserProfilePhotos`` for their current avatar, and stores it exactly the way
``handle_set_photo_photo`` does: the largest ``PhotoSize``'s ``file_id`` into
``players.photo_file_id`` (source of truth), plus a best-effort byte backup under
``PHOTOS_DIR`` so a from-scratch bot rebuild can re-upload.

Only reads Telegram; the sole writes are the local DB row and the backup file. It
never messages anyone. Dry-run by default -- pass ``--apply`` to write.

    uv run python scripts/pull_profile_photos.py            # report only
    uv run python scripts/pull_profile_photos.py --apply     # write
    uv run python scripts/pull_profile_photos.py --apply --only 154050001

A player whose privacy hides their avatar (or who has none) is reported as
``no avatar`` and left alone -- those are the ones that still need a DM.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request

from toop.config import settings
from toop.photos import save_photo_bytes
from toop.players import set_player_photo

API = "https://api.telegram.org"


def _call(method: str, **params: object) -> dict:
    """POST a Bot API method, returning the decoded envelope (ok/result/description).

    Telegram answers a rejected call with a 4xx *and* a JSON body, so the
    HTTPError branch is a normal outcome here, not an exception to propagate.
    """
    url = f"{API}/bot{settings.BOT_TOKEN}/{method}"
    data = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=30) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read().decode())
        except ValueError:
            return {"ok": False, "description": f"HTTP {exc.code}"}
    except OSError as exc:
        return {"ok": False, "description": str(exc)}


def _download(file_path: str) -> bytes:
    url = f"{API}/file/bot{settings.BOT_TOKEN}/{file_path}"
    with urllib.request.urlopen(url, timeout=60) as resp:
        return resp.read()


def _avatar_file_id(telegram_id: int) -> tuple[str | None, str]:
    """Return (file_id, note) for a player's current avatar.

    file_id is None when there is nothing to pull; note explains why, which is
    the whole point of the dry run.
    """
    got = _call("getUserProfilePhotos", user_id=telegram_id, limit=1)
    if not got.get("ok"):
        # "chat not found" here means the bot cannot see this user at all --
        # blocked, deleted, or never started. Worth surfacing: they also cannot
        # receive vote prompts.
        return None, f"unreachable ({got.get('description', 'unknown error')})"
    photos = got["result"].get("photos") or []
    if not photos:
        return None, "no avatar (none set, or profile photo hidden from bots)"
    largest = photos[0][-1]  # last PhotoSize = full resolution
    return largest["file_id"], f"{largest['width']}x{largest['height']}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write the photos (default is a dry run that only reports)",
    )
    parser.add_argument(
        "--only",
        type=int,
        action="append",
        metavar="TELEGRAM_ID",
        help="restrict to these telegram ids (repeatable)",
    )
    args = parser.parse_args()

    conn = sqlite3.connect(settings.DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT telegram_id, username, display_name FROM players "
        "WHERE active=1 AND photo_file_id IS NULL ORDER BY display_name COLLATE NOCASE"
    ).fetchall()
    if args.only:
        wanted = set(args.only)
        rows = [r for r in rows if r["telegram_id"] in wanted]

    pulled = skipped = 0
    for row in rows:
        tid, name = row["telegram_id"], row["display_name"]
        handle = f"@{row['username']}" if row["username"] else "(no handle)"
        if tid < 0:
            # Ghost player: a synthetic id with no Telegram account behind it,
            # so there is no avatar to fetch. Only /set_photo can cover them.
            print(f"  skip   {name} {handle} (ghost, no Telegram account)")
            skipped += 1
            continue

        file_id, note = _avatar_file_id(tid)
        if file_id is None:
            print(f"  skip   {name} {handle} ({note})")
            skipped += 1
            continue

        if not args.apply:
            print(f"  would pull {name} {handle} (avatar {note})")
            pulled += 1
            continue

        # Byte backup is best effort, same as the /set_photo handler: file_id is
        # the source of truth and a download hiccup must not block storing it.
        got = _call("getFile", file_id=file_id)
        if got.get("ok"):
            try:
                save_photo_bytes(tid, _download(got["result"]["file_path"]))
            except OSError as exc:
                print(f"         (backup failed for {name}: {exc})", file=sys.stderr)
        else:
            print(
                f"         (backup skipped for {name}: {got.get('description')})",
                file=sys.stderr,
            )

        if set_player_photo(conn, tid, file_id) is None:
            print(f"  skip   {name} {handle} (vanished from roster mid-run)")
            skipped += 1
            continue
        print(f"  pulled {name} {handle} (avatar {note})")
        pulled += 1

    verb = "pulled" if args.apply else "would pull"
    print(f"\n{verb} {pulled}, skipped {skipped}, of {len(rows)} photo-less players")
    if not args.apply and pulled:
        print("re-run with --apply to write")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
