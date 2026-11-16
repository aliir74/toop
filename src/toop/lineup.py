"""Suggested starting lineup (court positions 1-6) for one team.

The group plays a light 4-2: the team's two best setters stand opposite each
other in the rotation, so one of them is always in the front row, and after the
serve that one walks to front-middle (position 3) and sets. Everyone else plays
wherever the rotation puts them.

With 6 players there are only 120 circular orders, so every one is tried. An
order is scored by its *weakest* rotation (max-min), so no turn of the rotation
leaves the team with a thin front row and weak passers at the same time. The
chosen order is then turned so the steadiest servers start at positions 1 and 2:
the bot cannot know which team serves first, and a team that receives first
rotates before its first serve, so its first server is whoever started at 2.

A 7th player (the per-team sub) joins the cycle: whoever rotates out of
position 1 sits out, and the sitting player comes back in at position 6.

Research and the scoring rationale: docs/algorithm/README.md (2026-11-16 entry).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from itertools import permutations

from toop.rating import INDICATORS, get_player_ratings

BENCH = 0
# Seats in the order players move through them: 2 -> 1 -> 6 -> 5 -> 4 -> 3 -> 2.
# Seat i's player moves to seat i+1 on each rotation.
_SEATS = {6: (1, 6, 5, 4, 3, 2), 7: (1, BENCH, 6, 5, 4, 3, 2)}
FRONT = (4, 3, 2)
BACK = (5, 6, 1)

# Our own weights, not from a published model (none uses player ratings).
_BLOCK_WEIGHT = 0.5
_WEAKEST_PASSER_WEIGHT = 0.5

Scores = dict[str, dict[int, float]]


@dataclass(frozen=True)
class Lineup:
    court: dict[int, int]  # court position (1-6) -> telegram_id
    bench: int | None  # the sub sitting out at the start (7-player teams)
    setters: tuple[int, int]  # the two designated setters, best first
    worst_rotation: float


def _court_at(order: tuple[int, ...], seats: tuple[int, ...], turn: int) -> dict[int, int]:
    n = len(seats)
    return {seats[(i + turn) % n]: pid for i, pid in enumerate(order)}


def _rotation_score(court: dict[int, int], setters: tuple[int, int], s: Scores) -> float:
    front = [court[p] for p in FRONT]
    back = [court[p] for p in BACK]
    pool = [p for p in front if p in setters] or front
    setter = max(pool, key=lambda p: (s["setting"][p], -p))
    hitters = [p for p in front if p != setter]
    receive = [s["receive"][p] for p in back]
    return (
        s["setting"][setter]
        + sum(s["attack"][p] for p in hitters)
        + _BLOCK_WEIGHT * sum(s["block"][p] for p in front)
        + sum(receive)
        + _WEAKEST_PASSER_WEIGHT * min(receive)
    )


def suggest_lineup(team: list[int], scores: Scores) -> Lineup | None:
    """Best starting lineup for a team of 6 or 7, or None for any other size."""
    seats = _SEATS.get(len(team))
    if seats is None:
        return None
    n = len(seats)
    by_setting = sorted(team, key=lambda p: (-scores["setting"][p], p))
    setters = (by_setting[0], by_setting[1])

    first, *rest = sorted(team)
    best: tuple[float, float] | None = None
    best_order: tuple[int, ...] = ()
    for perm in permutations(rest):
        order = (first, *perm)
        if n == 6 and abs(order.index(setters[0]) - order.index(setters[1])) != 3:
            continue
        per_turn = [_rotation_score(_court_at(order, seats, r), setters, scores) for r in range(n)]
        key = (min(per_turn), sum(per_turn) / n)
        if best is None or key > best:
            best, best_order = key, order
    assert best is not None

    def serve_key(turn: int) -> float:
        court = _court_at(best_order, seats, turn)
        return scores["serve"][court[1]] + scores["serve"][court[2]]

    start = max(range(n), key=lambda r: (serve_key(r), -r))
    court = _court_at(best_order, seats, start)
    bench = court.pop(BENCH, None)
    return Lineup(court=court, bench=bench, setters=setters, worst_rotation=best[0])


def lineup_for_team(conn: sqlite3.Connection, team: list[int]) -> Lineup | None:
    """Read the current per-skill ratings and suggest a lineup for ``team``."""
    scores: Scores = {ind: {} for ind in INDICATORS}
    for pid in team:
        ratings = get_player_ratings(conn, pid)
        for ind in INDICATORS:
            scores[ind][pid] = ratings.get(ind, (0.0, 0, False))[0]
    return suggest_lineup(team, scores)
