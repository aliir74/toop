from __future__ import annotations

import re
from datetime import timedelta

# Token suffix → days. ``m`` is a coarse month (30 days) — exact enough for a
# "pull them for a month" style pause.
DURATION_DAYS = {"d": 1, "w": 7, "m": 30}


def parse_duration(token: str) -> timedelta | None:
    """Parse a pause duration like ``2w``, ``10d`` or ``1m`` into a timedelta.

    Returns None when the token doesn't match the ``<number><d|w|m>`` shape.
    Shared by /pause_voting (a per-player pause) and /pause_events (a global
    schedule pause) so both accept the same syntax.
    """
    match = re.fullmatch(r"(\d+)([dwm])", token.lower())
    if not match:
        return None
    amount = int(match.group(1))
    return timedelta(days=amount * DURATION_DAYS[match.group(2)])
