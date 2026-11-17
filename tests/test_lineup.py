from __future__ import annotations

import sqlite3

from toop.lineup import BACK, lineup_for_team, suggest_lineup
from toop.players import add_player
from toop.rating import INDICATORS

# Volleyball rotation (FIVB 7.6.2): every player moves one spot clockwise.
_NEXT = {2: 1, 1: 6, 6: 5, 5: 4, 4: 3, 3: 2}


def _scores(n: int, **overrides: dict[int, float]) -> dict[str, dict[int, float]]:
    scores = {ind: dict.fromkeys(range(1, n + 1), 1.0) for ind in INDICATORS}
    for ind, values in overrides.items():
        scores[ind].update(values)
    return scores


def _rotations(court: dict[int, int]) -> list[dict[int, int]]:
    out = [court]
    for _ in range(5):
        out.append({_NEXT[pos]: pid for pos, pid in out[-1].items()})
    return out


def test_whoever_is_at_front_middle_sets_so_a_weak_setter_gets_the_best_hitters() -> None:
    # No fixed setter: player 1 (weakest setter) sets in the rotation that puts
    # them at 3, so the two strong hitters (2 and 3) should flank them at 4 and 2.
    scores = _scores(6, setting={1: -5.0}, attack={2: 5.0, 3: 5.0})
    lineup = suggest_lineup([1, 2, 3, 4, 5, 6], scores)
    assert lineup is not None
    assert sorted(lineup.court) == [1, 2, 3, 4, 5, 6]
    assert sorted(lineup.court.values()) == [1, 2, 3, 4, 5, 6]
    when_1_sets = next(c for c in _rotations(lineup.court) if c[3] == 1)
    assert {when_1_sets[4], when_1_sets[2]} == {2, 3}


def test_two_weak_passers_never_share_the_back_row() -> None:
    scores = _scores(6, receive={5: -3.0, 6: -3.0})
    lineup = suggest_lineup([1, 2, 3, 4, 5, 6], scores)
    assert lineup is not None
    for court in _rotations(lineup.court):
        back = {court[p] for p in BACK}
        assert {5, 6} - back, "both weak passers in the back row together"


def test_steadiest_server_starts_at_position_one_or_two() -> None:
    scores = _scores(6, serve={4: 5.0})
    lineup = suggest_lineup([1, 2, 3, 4, 5, 6], scores)
    assert lineup is not None
    assert 4 in (lineup.court[1], lineup.court[2])


def test_other_team_sizes_get_no_lineup() -> None:
    assert suggest_lineup([1, 2, 3, 4, 5], _scores(5)) is None
    assert suggest_lineup(list(range(1, 8)), _scores(7)) is None
    assert suggest_lineup(list(range(1, 9)), _scores(8)) is None


def test_same_input_gives_same_lineup() -> None:
    scores = _scores(6, setting={1: 2.0, 4: 2.0})
    assert suggest_lineup([1, 2, 3, 4, 5, 6], scores) == suggest_lineup([6, 5, 4, 3, 2, 1], scores)


def test_lineup_for_team_reads_stored_ratings(conn: sqlite3.Connection) -> None:
    for pid in range(1, 7):
        add_player(conn, pid, f"P{pid}", f"p{pid}")
        conn.execute(
            "INSERT INTO player_ratings (telegram_id, indicator, score) VALUES (?, 'receive', 1.0)",
            (pid,),
        )
    conn.commit()
    lineup = lineup_for_team(conn, [1, 2, 3, 4, 5, 6])
    assert lineup is not None
    # Every other skill is unrated (0): 3 back passers at 1.0 + half the weakest.
    assert lineup.worst_rotation == 3.5
