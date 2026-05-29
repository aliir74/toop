from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from toop.rating import AXES, composite_score, get_player_ratings


@dataclass(frozen=True)
class TeamMetrics:
    team_a_total: float
    team_b_total: float
    abs_delta: float
    per_axis_a: dict[str, float]
    per_axis_b: dict[str, float]
    calibration_confidence: str
    setter_swap_applied: bool


def _composite_for_team(scores: dict[int, float], team: list[int]) -> float:
    return sum(scores[pid] for pid in team)


def _per_axis_totals(axis_scores: dict[str, dict[int, float]], team: list[int]) -> dict[str, float]:
    return {axis: sum(axis_scores[axis].get(pid, 0.0) for pid in team) for axis in AXES}


def _snake_assign(sorted_attendees: list[int]) -> tuple[list[int], list[int]]:
    team_a: list[int] = []
    team_b: list[int] = []
    for i, pid in enumerate(sorted_attendees):
        round_idx = i // 2
        pick_in_round = i % 2
        a_starts = round_idx % 2 == 0
        first_pick = pick_in_round == 0
        if a_starts == first_pick:
            team_a.append(pid)
        else:
            team_b.append(pid)
    return team_a, team_b


def _confidence_from_ratio(ratio: float) -> str:
    if ratio >= 0.8:
        return "high"
    if ratio >= 0.5:
        return "medium"
    return "low"


def _try_setter_swap(
    team_a: list[int],
    team_b: list[int],
    top_setters: set[int],
    composite: dict[int, float],
) -> tuple[list[int], list[int], bool]:
    """Return (team_a, team_b, swap_applied). Greedy lowest-impact fix."""
    a_setters = sum(1 for p in team_a if p in top_setters)
    b_setters = sum(1 for p in team_b if p in top_setters)
    if a_setters >= 1 and b_setters >= 1:
        return team_a, team_b, False
    if a_setters == 0:
        donor_team, recipient_team = team_b, team_a
    else:
        donor_team, recipient_team = team_a, team_b

    donors = [p for p in donor_team if p in top_setters]
    recipients = [p for p in recipient_team if p not in top_setters]
    if not donors or not recipients:
        return team_a, team_b, False

    best: tuple[float, int, int] | None = None
    for d in donors:
        for r in recipients:
            impact = abs(composite[d] - composite[r])
            if best is None or impact < best[0]:
                best = (impact, d, r)
    if best is None:  # pragma: no cover - defensive; loop above always sets best
        return team_a, team_b, False

    _, d, r = best
    new_donor = [r if p == d else p for p in donor_team]
    new_recipient = [d if p == r else p for p in recipient_team]
    if donor_team is team_a:
        return new_donor, new_recipient, True
    return new_recipient, new_donor, True


def generate_teams(
    conn: sqlite3.Connection,
    attendees: list[int],
    weights: tuple[float, float, float],
) -> tuple[list[int], list[int], TeamMetrics]:
    """Snake-draft attendees by composite score, then enforce setter constraint.

    Returns (team_a, team_b, metrics).
    """
    if not attendees:
        return (
            [],
            [],
            TeamMetrics(
                team_a_total=0.0,
                team_b_total=0.0,
                abs_delta=0.0,
                per_axis_a={a: 0.0 for a in AXES},
                per_axis_b={a: 0.0 for a in AXES},
                calibration_confidence="low",
                setter_swap_applied=False,
            ),
        )

    composite: dict[int, float] = {}
    statuses: dict[int, str] = {}
    axis_scores: dict[str, dict[int, float]] = {axis: {} for axis in AXES}
    for pid in attendees:
        score, status = composite_score(conn, pid, weights)
        composite[pid] = score
        statuses[pid] = status
        ratings = get_player_ratings(conn, pid)
        for axis in AXES:
            axis_scores[axis][pid] = ratings.get(axis, (0.0, 0, False))[0]

    sorted_attendees = sorted(attendees, key=lambda pid: (-composite[pid], pid))
    team_a, team_b = _snake_assign(sorted_attendees)

    top_quartile_count = max(1, len(attendees) // 4)
    setting_ranked = sorted(attendees, key=lambda pid: (-axis_scores["setting"][pid], pid))
    top_setters = set(setting_ranked[:top_quartile_count])

    team_a, team_b, swap_applied = _try_setter_swap(team_a, team_b, top_setters, composite)

    calibrated_count = sum(1 for pid in attendees if statuses[pid] == "calibrated")
    confidence = _confidence_from_ratio(calibrated_count / len(attendees))

    metrics = TeamMetrics(
        team_a_total=_composite_for_team(composite, team_a),
        team_b_total=_composite_for_team(composite, team_b),
        abs_delta=abs(
            _composite_for_team(composite, team_a) - _composite_for_team(composite, team_b)
        ),
        per_axis_a=_per_axis_totals(axis_scores, team_a),
        per_axis_b=_per_axis_totals(axis_scores, team_b),
        calibration_confidence=confidence,
        setter_swap_applied=swap_applied,
    )
    return team_a, team_b, metrics


def swap_players(
    team_a: list[int], team_b: list[int], player_a: int, player_b: int
) -> tuple[list[int], list[int]]:
    """Manually swap two players between teams. Raises if either isn't on its team."""
    if player_a in team_a and player_b in team_b:
        new_a = [player_b if p == player_a else p for p in team_a]
        new_b = [player_a if p == player_b else p for p in team_b]
        return new_a, new_b
    if player_b in team_a and player_a in team_b:
        new_a = [player_a if p == player_b else p for p in team_a]
        new_b = [player_b if p == player_a else p for p in team_b]
        return new_a, new_b
    raise ValueError("Both players must be on opposite teams to swap.")


def compute_metrics(
    conn: sqlite3.Connection,
    team_a: list[int],
    team_b: list[int],
    weights: tuple[float, float, float],
) -> TeamMetrics:
    """Recompute metrics for an arbitrary (team_a, team_b) split (e.g. after admin swap)."""
    attendees = team_a + team_b
    if not attendees:
        return TeamMetrics(
            team_a_total=0.0,
            team_b_total=0.0,
            abs_delta=0.0,
            per_axis_a={a: 0.0 for a in AXES},
            per_axis_b={a: 0.0 for a in AXES},
            calibration_confidence="low",
            setter_swap_applied=False,
        )

    composite: dict[int, float] = {}
    statuses: dict[int, str] = {}
    axis_scores: dict[str, dict[int, float]] = {axis: {} for axis in AXES}
    for pid in attendees:
        score, status = composite_score(conn, pid, weights)
        composite[pid] = score
        statuses[pid] = status
        ratings = get_player_ratings(conn, pid)
        for axis in AXES:
            axis_scores[axis][pid] = ratings.get(axis, (0.0, 0, False))[0]

    calibrated_count = sum(1 for pid in attendees if statuses[pid] == "calibrated")
    confidence = _confidence_from_ratio(calibrated_count / len(attendees))

    return TeamMetrics(
        team_a_total=_composite_for_team(composite, team_a),
        team_b_total=_composite_for_team(composite, team_b),
        abs_delta=abs(
            _composite_for_team(composite, team_a) - _composite_for_team(composite, team_b)
        ),
        per_axis_a=_per_axis_totals(axis_scores, team_a),
        per_axis_b=_per_axis_totals(axis_scores, team_b),
        calibration_confidence=confidence,
        setter_swap_applied=False,
    )
