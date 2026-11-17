"""Suggested starting lineup (court positions 1-6) for one team.

The group plays a plain 6-6: no fixed setter, everyone rotates, and whoever
the rotation puts at front-middle (position 3) sets. Every player therefore
sets for one rotation, so the order decides who stands around each setter.

With 6 players there are only 120 circular orders, so every one is tried. An
order is scored by its *weakest* rotation (max-min), so no turn of the rotation
leaves the team with a thin front row and weak passers at the same time. The
chosen order is then turned so the steadiest servers start at positions 1 and 2:
the bot cannot know which team serves first, and a team that receives first
rotates before its first serve, so its first server is whoever started at 2.

Research and the scoring rationale: docs/algorithm/README.md (2026-11-16 entry).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from itertools import permutations

from toop.rating import INDICATORS, get_player_ratings

# Seats in the order players move through them: 2 -> 1 -> 6 -> 5 -> 4 -> 3 -> 2.
# Seat i's player moves to seat i+1 on each rotation.
_SEATS = (1, 6, 5, 4, 3, 2)
FRONT = (4, 3, 2)
BACK = (5, 6, 1)
SETTER_POSITION = 3

# Our own weights, not from a published model (none uses player ratings).
_BLOCK_WEIGHT = 0.5
_WEAKEST_PASSER_WEIGHT = 0.5

Scores = dict[str, dict[int, float]]


@dataclass(frozen=True)
class Lineup:
    court: dict[int, int]  # court position (1-6) -> telegram_id
    worst_rotation: float


def _court_at(order: tuple[int, ...], turn: int) -> dict[int, int]:
    return {_SEATS[(i + turn) % 6]: pid for i, pid in enumerate(order)}


def _rotation_score(court: dict[int, int], s: Scores) -> float:
    front = [court[p] for p in FRONT]
    back = [court[p] for p in BACK]
    setter = court[SETTER_POSITION]
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
    """Best starting lineup for a team of 6, or None for any other size."""
    if len(team) != 6:
        return None
    first, *rest = sorted(team)
    best: tuple[float, float] | None = None
    best_order: tuple[int, ...] = ()
    for perm in permutations(rest):
        order = (first, *perm)
        per_turn = [_rotation_score(_court_at(order, r), scores) for r in range(6)]
        key = (min(per_turn), sum(per_turn) / 6)
        if best is None or key > best:
            best, best_order = key, order
    assert best is not None

    def serve_key(turn: int) -> float:
        court = _court_at(best_order, turn)
        return scores["serve"][court[1]] + scores["serve"][court[2]]

    start = max(range(6), key=lambda r: (serve_key(r), -r))
    court = _court_at(best_order, start)
    return Lineup(court=court, worst_rotation=best[0])


def lineup_for_team(conn: sqlite3.Connection, team: list[int]) -> Lineup | None:
    """Read the current per-skill ratings and suggest a lineup for ``team``."""
    scores: Scores = {ind: {} for ind in INDICATORS}
    for pid in team:
        ratings = get_player_ratings(conn, pid)
        for ind in INDICATORS:
            scores[ind][pid] = ratings.get(ind, (0.0, 0, False))[0]
    return suggest_lineup(team, scores)
