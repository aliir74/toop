from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from itertools import combinations

from toop.rating import INDICATORS, composite_score, get_player_ratings


@dataclass(frozen=True)
class TeamMetrics:
    team_a_total: float
    team_b_total: float
    abs_delta: float
    per_indicator_a: dict[str, float]
    per_indicator_b: dict[str, float]
    calibration_confidence: str
    setter_swap_applied: bool


# Per-skill balance display ----------------------------------------------------
# English labels keep the bar columns aligned regardless of the bot's UI language:
# Telegram renders ``` code blocks ``` left-to-right, so RTL (Persian) labels would
# scramble the alignment. Volleyball skill names are widely understood in English.
_SKILL_BAR_LABELS = {
    "attack": "Attack",
    "receive": "Receive",
    "block": "Block",
    "setting": "Setting",
    "serve": "Serve",
    "positioning": "Position",
}
_BAR_FILLED = "█"
_BAR_EMPTY = "░"
# Fairness thresholds mirror the HTML comparison report (green ≤0.40, amber ≤0.80).
_FAIR_BALANCED = 0.40
_FAIR_OK = 0.80


def _fairness_mark(gap: float) -> str:
    if gap <= _FAIR_BALANCED:
        return "🟢"
    if gap <= _FAIR_OK:
        return "🟡"
    return "🔴"


def skill_balance_bars(
    per_indicator_a: dict[str, float],
    per_indicator_b: dict[str, float],
    *,
    width: int = 12,
    scale: float = 2.5,
) -> str:
    """Monospace per-skill gap bars: one row per skill, bar length = how far apart
    the two teams are in that skill (shorter = more balanced), with the numeric gap
    and a fairness dot. Returned WITHOUT code-block fences so the caller can wrap it
    for its channel. Mirrors the HTML balance report so the group can see, at a
    glance, that every skill is close between the teams.
    """
    label_w = max(len(v) for v in _SKILL_BAR_LABELS.values())
    lines = []
    for ind in INDICATORS:
        gap = abs(per_indicator_a.get(ind, 0.0) - per_indicator_b.get(ind, 0.0))
        filled = round(min(gap, scale) / scale * width)
        bar = _BAR_FILLED * filled + _BAR_EMPTY * (width - filled)
        label = _SKILL_BAR_LABELS[ind].ljust(label_w)
        lines.append(f"{label}  {bar}  {gap:4.2f} {_fairness_mark(gap)}")
    return "\n".join(lines)


def _composite_for_team(scores: dict[int, float], team: list[int]) -> float:
    return sum(scores[pid] for pid in team)


def _per_indicator_totals(
    indicator_scores: dict[str, dict[int, float]], team: list[int]
) -> dict[str, float]:
    return {ind: sum(indicator_scores[ind].get(pid, 0.0) for pid in team) for ind in INDICATORS}


def _optimal_assign(
    attendees: list[int],
    indicator_scores: dict[str, dict[int, float]],
    weights: dict[str, float],
) -> tuple[list[int], list[int]]:
    """Exhaustive search for the (ceil(n/2), floor(n/2)) split that minimises the
    weighted sum of per-indicator gaps: Σ_k weight_k · |Σ_A score_k − Σ_B score_k|.

    Balancing this instead of a single composite delta stops a strong-attack /
    weak-defence imbalance from cancelling into a fake-balanced total — each skill
    is balanced in proportion to its weight, so no individual skill ends up
    lopsided. (Minimising |composite_A − composite_B| = |Σ_k w_k·(A_k − B_k)|
    lets opposite-sign skill gaps cancel; moving the abs inside the sum prevents
    that.) C(n, floor(n/2)) ≤ C(20, 10) = 184 756 — fast enough for any real group.
    """
    n = len(attendees)
    size_b = n // 2
    indicators = list(weights)
    w = [weights[ind] for ind in indicators]
    # Per-player score vector aligned to `indicators`, plus the all-attendee totals
    # (so each indicator gap = |total − 2·b_sum|).
    vecs = [[indicator_scores[ind].get(pid, 0.0) for ind in indicators] for pid in attendees]
    totals = [sum(vec[j] for vec in vecs) for j in range(len(indicators))]

    best_obj = float("inf")
    best_b_idx: tuple[int, ...] = tuple(range(size_b))

    for b_idx in combinations(range(n), size_b):
        obj = 0.0
        for j in range(len(indicators)):
            b_sum = sum(vecs[i][j] for i in b_idx)
            obj += w[j] * abs(totals[j] - 2 * b_sum)
        if obj < best_obj:
            best_obj = obj
            best_b_idx = b_idx

    b_set = set(best_b_idx)
    team_b = [attendees[i] for i in best_b_idx]
    team_a = [attendees[i] for i in range(n) if i not in b_set]
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


def _empty_metrics() -> TeamMetrics:
    return TeamMetrics(
        team_a_total=0.0,
        team_b_total=0.0,
        abs_delta=0.0,
        per_indicator_a={ind: 0.0 for ind in INDICATORS},
        per_indicator_b={ind: 0.0 for ind in INDICATORS},
        calibration_confidence="low",
        setter_swap_applied=False,
    )


def _gather_scores(
    conn: sqlite3.Connection, attendees: list[int], weights: dict[str, float]
) -> tuple[dict[int, float], dict[int, str], dict[str, dict[int, float]]]:
    composite: dict[int, float] = {}
    statuses: dict[int, str] = {}
    indicator_scores: dict[str, dict[int, float]] = {ind: {} for ind in INDICATORS}
    for pid in attendees:
        score, status = composite_score(conn, pid, weights)
        composite[pid] = score
        statuses[pid] = status
        ratings = get_player_ratings(conn, pid)
        for ind in INDICATORS:
            indicator_scores[ind][pid] = ratings.get(ind, (0.0, 0, False))[0]
    return composite, statuses, indicator_scores


def generate_teams(
    conn: sqlite3.Connection,
    attendees: list[int],
    weights: dict[str, float],
) -> tuple[list[int], list[int], TeamMetrics]:
    """Snake-draft attendees by composite score, then enforce setter constraint.

    Returns (team_a, team_b, metrics).
    """
    if not attendees:
        return [], [], _empty_metrics()

    composite, statuses, indicator_scores = _gather_scores(conn, attendees, weights)

    team_a, team_b = _optimal_assign(attendees, indicator_scores, weights)

    top_quartile_count = max(1, len(attendees) // 4)
    setting_ranked = sorted(attendees, key=lambda pid: (-indicator_scores["setting"][pid], pid))
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
        per_indicator_a=_per_indicator_totals(indicator_scores, team_a),
        per_indicator_b=_per_indicator_totals(indicator_scores, team_b),
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
    weights: dict[str, float],
) -> TeamMetrics:
    """Recompute metrics for an arbitrary (team_a, team_b) split (e.g. after admin swap)."""
    attendees = team_a + team_b
    if not attendees:
        return _empty_metrics()

    composite, statuses, indicator_scores = _gather_scores(conn, attendees, weights)

    calibrated_count = sum(1 for pid in attendees if statuses[pid] == "calibrated")
    confidence = _confidence_from_ratio(calibrated_count / len(attendees))

    return TeamMetrics(
        team_a_total=_composite_for_team(composite, team_a),
        team_b_total=_composite_for_team(composite, team_b),
        abs_delta=abs(
            _composite_for_team(composite, team_a) - _composite_for_team(composite, team_b)
        ),
        per_indicator_a=_per_indicator_totals(indicator_scores, team_a),
        per_indicator_b=_per_indicator_totals(indicator_scores, team_b),
        calibration_confidence=confidence,
        setter_swap_applied=False,
    )
