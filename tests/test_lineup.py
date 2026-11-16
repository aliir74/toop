from __future__ import annotations

import sqlite3

from toop.lineup import BACK, FRONT, lineup_for_team, suggest_lineup
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


def test_six_players_one_designated_setter_always_in_front_row() -> None:
    scores = _scores(6, setting={3: 4.0, 5: 3.0})
    lineup = suggest_lineup([1, 2, 3, 4, 5, 6], scores)
    assert lineup is not None
    assert lineup.setters == (3, 5)
    assert sorted(lineup.court) == [1, 2, 3, 4, 5, 6]
    assert sorted(lineup.court.values()) == [1, 2, 3, 4, 5, 6]
    assert lineup.bench is None
    for court in _rotations(lineup.court):
        front = {court[p] for p in FRONT}
        assert len(front & {3, 5}) == 1


def test_two_weak_passers_never_share_the_back_row() -> None:
    scores = _scores(6, setting={1: 4.0, 2: 3.0}, receive={5: -3.0, 6: -3.0})
    lineup = suggest_lineup([1, 2, 3, 4, 5, 6], scores)
    assert lineup is not None
    for court in _rotations(lineup.court):
        back = {court[p] for p in BACK}
        assert {5, 6} - back, "both weak passers in the back row together"


def test_steadiest_server_starts_at_position_one_or_two() -> None:
    scores = _scores(6, setting={1: 4.0, 2: 3.0}, serve={4: 5.0})
    lineup = suggest_lineup([1, 2, 3, 4, 5, 6], scores)
    assert lineup is not None
    assert 4 in (lineup.court[1], lineup.court[2])


def test_seven_players_put_one_on_the_bench() -> None:
    scores = _scores(7, setting={7: 4.0, 2: 3.0})
    lineup = suggest_lineup([1, 2, 3, 4, 5, 6, 7], scores)
    assert lineup is not None
    assert lineup.setters == (7, 2)
    assert lineup.bench is not None
    assert sorted([*lineup.court.values(), lineup.bench]) == [1, 2, 3, 4, 5, 6, 7]
    assert sorted(lineup.court) == [1, 2, 3, 4, 5, 6]


def test_other_team_sizes_get_no_lineup() -> None:
    assert suggest_lineup([1, 2, 3, 4, 5], _scores(5)) is None
    assert suggest_lineup(list(range(1, 9)), _scores(8)) is None


def test_same_input_gives_same_lineup() -> None:
    scores = _scores(6, setting={1: 2.0, 4: 2.0})
    assert suggest_lineup([1, 2, 3, 4, 5, 6], scores) == suggest_lineup([6, 5, 4, 3, 2, 1], scores)


def test_lineup_for_team_reads_stored_ratings(conn: sqlite3.Connection) -> None:
    for pid in range(1, 7):
        add_player(conn, pid, f"P{pid}", f"p{pid}")
    for pid, score in ((2, 4.5), (6, 4.0)):
        conn.execute(
            "INSERT INTO player_ratings (telegram_id, indicator, score) VALUES (?, 'setting', ?)",
            (pid, score),
        )
    conn.commit()
    lineup = lineup_for_team(conn, [1, 2, 3, 4, 5, 6])
    assert lineup is not None
    assert lineup.setters == (2, 6)


def test_designated_setters_start_opposite_each_other() -> None:
    # Mixed ratings where, without the 4-2 rule, the best-scoring order puts the
    # two setters side by side instead of opposite.
    scores = {
        "attack": {1: 2.0, 2: 5.0, 3: 1.0, 4: 3.0, 5: 1.0, 6: 4.0},
        "receive": {1: 4.0, 2: 4.0, 3: 4.0, 4: 2.0, 5: 1.0, 6: 4.0},
        "block": {1: 1.0, 2: 4.0, 3: 4.0, 4: 5.0, 5: 1.0, 6: 4.0},
        "setting": {1: 3.0, 2: 2.0, 3: 5.0, 4: 1.0, 5: 3.0, 6: 1.0},
        "serve": {1: 1.0, 2: 1.0, 3: 5.0, 4: 1.0, 5: 4.0, 6: 2.0},
        "positioning": {1: 4.0, 2: 1.0, 3: 5.0, 4: 2.0, 5: 4.0, 6: 4.0},
    }
    lineup = suggest_lineup([1, 2, 3, 4, 5, 6], scores)
    assert lineup is not None
    where = {pid: pos for pos, pid in lineup.court.items()}
    opposite = {1: 4, 4: 1, 2: 5, 5: 2, 3: 6, 6: 3}
    assert opposite[where[lineup.setters[0]]] == where[lineup.setters[1]]
