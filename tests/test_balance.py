from __future__ import annotations

import sqlite3

import pytest

from toop.balance import (
    compute_metrics,
    generate_teams,
    skill_balance_bars,
    swap_players,
)
from toop.players import add_player
from toop.rating import INDICATORS

WEIGHTS = dict.fromkeys(INDICATORS, 1.0 / 6.0)
NON_SETTING = [ind for ind in INDICATORS if ind != "setting"]


def _set_rating(
    conn: sqlite3.Connection, pid: int, indicator: str, score: float, calibrated: int = 1
) -> None:
    conn.execute(
        """
        INSERT INTO player_ratings (telegram_id, indicator, score, vote_count, calibrated)
        VALUES (?, ?, ?, 20, ?)
        ON CONFLICT(telegram_id, indicator) DO UPDATE SET
            score=excluded.score, calibrated=excluded.calibrated
        """,
        (pid, indicator, score, calibrated),
    )
    conn.commit()


def _seed_balanced(conn: sqlite3.Connection, n: int) -> None:
    for i in range(1, n + 1):
        add_player(conn, i, f"P{i}", f"p{i}")
        base = (n + 1 - i) / n  # P1 strongest, Pn weakest
        for ind in INDICATORS:
            _set_rating(conn, i, ind, base)


def _set_composite(conn: sqlite3.Connection, pid: int, score: float, calibrated: int = 1) -> None:
    """Set all 6 indicators to score so composite == score under equal weights (w=1/6)."""
    for ind in INDICATORS:
        _set_rating(conn, pid, ind, score, calibrated)


def test_generate_teams_14_split_seven_seven(conn: sqlite3.Connection) -> None:
    _seed_balanced(conn, 14)
    team_a, team_b, metrics = generate_teams(conn, list(range(1, 15)), WEIGHTS)
    assert len(team_a) == 7
    assert len(team_b) == 7
    assert set(team_a).isdisjoint(team_b)
    assert metrics.abs_delta < 0.1


def test_optimal_beats_snake_skewed_13_players(conn: sqlite3.Connection) -> None:
    """Snake draft gives delta=9 on this score distribution; optimal gives delta≤1.

    Scores: one elite (10), three mid (5,5,5), one good (4), nine zeros.
    Snake: A={10,5,4,0,0,0,0}=19, B={5,5,0,0,0,0}=10 → delta=9
    Optimal: B={10,4,0,0,0,0}=14, A={5,5,5,0,0,0,0}=15 → delta=1
    """
    scores = [10, 5, 5, 5, 4, 0, 0, 0, 0, 0, 0, 0, 0]
    for i, score in enumerate(scores, start=1):
        add_player(conn, i, f"P{i}", f"p{i}")
        _set_composite(conn, i, score)

    team_a, team_b, metrics = generate_teams(conn, list(range(1, 14)), WEIGHTS)
    assert len(team_a) == 7
    assert len(team_b) == 6
    assert set(team_a).isdisjoint(team_b)
    assert metrics.abs_delta < 2.0  # snake would give 9; optimal gives 1


def test_balances_each_skill_not_just_composite(conn: sqlite3.Connection) -> None:
    """The whole point of the weighted-per-skill objective: a split with a perfect
    composite total but one lopsided skill must be rejected in favour of one that
    balances every skill.

    Construct two "attack specialists" and two "block specialists" plus fillers.
    Putting both attackers on one team and both blockers on the other gives a
    perfect composite (the skills cancel) but a brutal per-skill gap. The optimal
    split must instead spread one attacker and one blocker onto each team.
    """
    # P1,P2: high attack, low block.  P3,P4: low attack, high block.  P5,P6: neutral.
    specs = {
        1: {"attack": 2.0, "block": -2.0},
        2: {"attack": 2.0, "block": -2.0},
        3: {"attack": -2.0, "block": 2.0},
        4: {"attack": -2.0, "block": 2.0},
        5: {"attack": 0.0, "block": 0.0},
        6: {"attack": 0.0, "block": 0.0},
    }
    for pid, sk in specs.items():
        add_player(conn, pid, f"P{pid}", f"p{pid}")
        for ind in INDICATORS:
            _set_rating(conn, pid, ind, sk.get(ind, 0.0))

    team_a, team_b, _ = generate_teams(conn, [1, 2, 3, 4, 5, 6], WEIGHTS)

    # Each team must get exactly one attacker (1 or 2) and one blocker (3 or 4),
    # never both specialists of one kind together.
    a_attackers = len({1, 2} & set(team_a))
    a_blockers = len({3, 4} & set(team_a))
    assert a_attackers == 1, f"attackers split unevenly: Team A has {a_attackers}"
    assert a_blockers == 1, f"blockers split unevenly: Team A has {a_blockers}"

    metrics = compute_metrics(conn, team_a, team_b, WEIGHTS)
    assert abs(metrics.per_indicator_a["attack"] - metrics.per_indicator_b["attack"]) < 1e-6
    assert abs(metrics.per_indicator_a["block"] - metrics.per_indicator_b["block"]) < 1e-6


def test_metrics_high_confidence_when_all_calibrated(conn: sqlite3.Connection) -> None:
    _seed_balanced(conn, 14)
    _, _, metrics = generate_teams(conn, list(range(1, 15)), WEIGHTS)
    assert metrics.calibration_confidence == "high"


def test_metrics_low_confidence_when_none_calibrated(conn: sqlite3.Connection) -> None:
    for i in range(1, 15):
        add_player(conn, i, f"P{i}", f"p{i}")
        for ind in INDICATORS:
            _set_rating(conn, i, ind, 0.5, calibrated=0)
    _, _, metrics = generate_teams(conn, list(range(1, 15)), WEIGHTS)
    assert metrics.calibration_confidence == "low"


def test_setter_constraint_holds_after_generate(conn: sqlite3.Connection) -> None:
    """Top setters must not all land on one team."""
    for i in range(1, 9):
        add_player(conn, i, f"P{i}", f"p{i}")
        for ind in NON_SETTING:
            _set_rating(conn, i, ind, (9 - i) / 8)
        _set_rating(conn, i, "setting", 5.0 if i <= 2 else 0.0)
    team_a, team_b, _ = generate_teams(conn, list(range(1, 9)), WEIGHTS)
    a_setters = sum(1 for p in team_a if p in {1, 2})
    b_setters = sum(1 for p in team_b if p in {1, 2})
    assert a_setters >= 1 and b_setters >= 1


def test_setter_constraint_team_a_donates(conn: sqlite3.Connection) -> None:
    """Optimal puts the top setter on Team A; swap moves it to Team B (line 103 path).

    6 players: P1 strongest overall (composite=3), P2 top setter (setting=8,
    composite≈2.58), P3 decent (2.5), P4-P6 weak (0.5).
    Optimal: B={P1,P4,P5}, A={P2,P3,P6} → P2 (top setter) on A, none on B →
    Team A donates P2 to Team B.
    """
    add_player(conn, 1, "P1", "p1")
    _set_composite(conn, 1, 3.0)

    add_player(conn, 2, "P2", "p2")
    for ind in NON_SETTING:
        _set_rating(conn, 2, ind, 1.5)
    _set_rating(conn, 2, "setting", 8.0)

    add_player(conn, 3, "P3", "p3")
    _set_composite(conn, 3, 2.5)

    for i in (4, 5, 6):
        add_player(conn, i, f"P{i}", f"p{i}")
        _set_composite(conn, i, 0.5)

    team_a, team_b, _ = generate_teams(conn, [1, 2, 3, 4, 5, 6], WEIGHTS)
    assert 2 in team_b, "Setter swap (A→B) should have placed top setter on Team B"
    assert set(team_a) | set(team_b) == {1, 2, 3, 4, 5, 6}
    assert set(team_a) & set(team_b) == set()


def test_per_indicator_metrics_have_six_keys(conn: sqlite3.Connection) -> None:
    _seed_balanced(conn, 4)
    _, _, metrics = generate_teams(conn, [1, 2, 3, 4], WEIGHTS)
    assert set(metrics.per_indicator_a.keys()) == set(INDICATORS)
    assert set(metrics.per_indicator_b.keys()) == set(INDICATORS)


def test_empty_attendees(conn: sqlite3.Connection) -> None:
    team_a, team_b, metrics = generate_teams(conn, [], WEIGHTS)
    assert team_a == []
    assert team_b == []
    assert metrics.abs_delta == 0.0


def test_swap_players_swaps_correctly() -> None:
    new_a, new_b = swap_players([1, 2, 3], [4, 5, 6], 2, 5)
    assert sorted(new_a) == [1, 3, 5]
    assert sorted(new_b) == [2, 4, 6]


def test_swap_players_reverse_args() -> None:
    new_a, new_b = swap_players([1, 2, 3], [4, 5, 6], 5, 2)
    assert sorted(new_a) == [1, 3, 5]
    assert sorted(new_b) == [2, 4, 6]


def test_swap_players_raises_when_same_team() -> None:
    with pytest.raises(ValueError):
        swap_players([1, 2, 3], [4, 5, 6], 1, 2)


def test_skill_balance_bars_one_row_per_skill() -> None:
    bars = skill_balance_bars({}, {})
    lines = bars.splitlines()
    assert len(lines) == len(INDICATORS)
    # Empty score dicts → every gap is 0.00 and fully balanced.
    for line in lines:
        assert "0.00" in line
        assert "🟢" in line


def test_skill_balance_bars_fairness_marks() -> None:
    a = {"attack": 1.0, "receive": 0.6}  # other skills default to 0.0
    b: dict[str, float] = {}
    rows = {line.split()[0]: line for line in skill_balance_bars(a, b).splitlines()}
    assert "🟢" in rows["Block"]  # gap 0.00 → balanced
    assert "🟡" in rows["Receive"]  # gap 0.60 → ok (0.40 < g ≤ 0.80)
    assert "🔴" in rows["Attack"]  # gap 1.00 → lopsided (> 0.80)


def test_skill_balance_bars_caps_at_scale() -> None:
    # A gap beyond `scale` fills the whole bar (no overflow past `width`).
    a = {"attack": 9.0}
    bars = skill_balance_bars(a, {}, width=10, scale=2.5)
    attack_line = next(line for line in bars.splitlines() if line.startswith("Attack"))
    assert attack_line.count("█") == 10
    assert "░" not in attack_line


def test_compute_metrics_recomputes_after_manual_swap(conn: sqlite3.Connection) -> None:
    _seed_balanced(conn, 6)
    initial_a, initial_b, initial_metrics = generate_teams(conn, [1, 2, 3, 4, 5, 6], WEIGHTS)
    new_a, new_b = swap_players(initial_a, initial_b, initial_a[0], initial_b[0])
    new_metrics = compute_metrics(conn, new_a, new_b, WEIGHTS)
    assert new_metrics.team_a_total != initial_metrics.team_a_total
