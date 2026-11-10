from __future__ import annotations

import json
import sqlite3
import zlib
from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.constants import ChatType
from telegram.error import BadRequest, Forbidden, NetworkError, TimedOut

import toop.handlers.vote_widget as vw
from toop.config import settings
from toop.handlers.voting import _send_next_prompt, handle_vote_command
from toop.i18n import indicator_label, score_word, t
from toop.players import add_ghost_player, add_player
from toop.rating import INDICATORS
from toop.sessions import open_session

VOTER = 1


def _seed(conn: sqlite3.Connection) -> None:
    add_player(conn, VOTER, "Alice", "alice")
    add_player(conn, 2, "Bob", "bob")
    add_player(conn, 3, "Carol", "carol")


def _scores(conn: sqlite3.Connection, player_id: int) -> dict[str, int]:
    rows = conn.execute(
        "SELECT indicator, score FROM scores WHERE voter_id=? AND player_id=?",
        (VOTER, player_id),
    ).fetchall()
    return {r["indicator"]: r["score"] for r in rows}


# --- HTML -----------------------------------------------------------------


@pytest.mark.parametrize("lang", ["fa", "en"])
def test_widget_html_fits_telegram_limits_and_carries_the_catalog(lang: str) -> None:
    html = vw.build_widget_html(lang)
    # Telegram refuses widgets over 32 KB raw or 4 KB zlib-9 compressed.
    assert len(html.encode()) <= 32768
    assert len(zlib.compress(html.encode(), 9)) <= 4096
    assert "__" not in html.replace("__proto__", "")
    for ind in INDICATORS:
        assert indicator_label(ind, lang) in html
    for n in range(1, 6):
        assert score_word(n, lang) in html
    assert t("vote.widget_submit", lang) in html


def test_widget_html_lays_out_persian_right_to_left_by_hand() -> None:
    # The sandbox ignores CSS `direction`, so the row order itself must flip.
    fa, en = vw.build_widget_html("fa"), vw.build_widget_html("en")
    assert "flex-direction:row-reverse" in fa and "text-align:right" in fa
    assert "flex-direction:row-reverse" not in en and "text-align:left" in en
    assert '"rtl": true' in fa and '"rtl": false' in en


# --- states ---------------------------------------------------------------


def test_states_are_versioned_ordered_and_small() -> None:
    first = vw.rating_state(2, "x" * 500)
    later = vw.end_state()
    assert first["v"] == 1 and later["v"] == 1
    assert later["q"] >= first["q"]
    assert len(first["n"]) == vw.MAX_NAME_CHARS
    # chat_data and answer data are capped at 1024 bytes.
    worst = {**vw.rating_state(2, "ن" * 500), "d": "ن" * vw.MAX_NAME_CHARS}
    assert len(vw.dump_state(worst).encode()) <= 1024


# --- applying requests ----------------------------------------------------


def test_submit_records_all_six_scores_and_goes_straight_to_the_next_player(
    conn: sqlite3.Connection,
) -> None:
    _seed(conn)
    state = vw.apply_query(conn, VOTER, "1r:2:543215")
    assert state is not None
    # The next card is Carol's, and it confirms that Bob was saved.
    assert (state["p"], state["n"], state["d"]) == (3, "Carol", "Bob")
    assert _scores(conn, 2) == dict(zip(INDICATORS, [5, 4, 3, 2, 1, 5], strict=True))


def test_ghost_players_can_be_rated(conn: sqlite3.Connection) -> None:
    # Ghosts (players without a Telegram account yet) have negative ids.
    add_player(conn, VOTER, "Alice")
    ghost = add_ghost_player(conn, "Nima")
    assert ghost.telegram_id < 0
    state = vw.apply_query(conn, VOTER, f"1r:{ghost.telegram_id}:453524")
    assert state is not None and state["d"] == "Nima"
    assert _scores(conn, ghost.telegram_id)["attack"] == 4
    assert vw.apply_query(conn, VOTER, f"1k:{ghost.telegram_id}") is not None


def test_dont_know_records_one_skip_and_moves_to_another_player(
    conn: sqlite3.Connection,
) -> None:
    _seed(conn)
    session = open_session(conn, date(2026, 11, 16))
    state = vw.apply_query(conn, VOTER, "1k:2")
    assert state is not None and state["p"] == 3
    rows = conn.execute("SELECT player_id, session_id FROM score_skips").fetchall()
    assert [(r["player_id"], r["session_id"]) for r in rows] == [(2, session.id)]
    assert _scores(conn, 2) == {}


def test_dont_know_without_a_session_still_records_the_skip(conn: sqlite3.Connection) -> None:
    _seed(conn)
    vw.apply_query(conn, VOTER, "1k:2")
    row = conn.execute("SELECT session_id FROM score_skips").fetchone()
    assert row["session_id"] is None


def test_next_moves_on_without_recording_anything(conn: sqlite3.Connection) -> None:
    _seed(conn)
    state = vw.apply_query(conn, VOTER, "1n:2")
    assert state is not None and state["p"] == 3
    assert conn.execute("SELECT COUNT(*) FROM scores").fetchone()[0] == 0


def test_next_on_the_only_player_left_ends_instead_of_repeating(
    conn: sqlite3.Connection,
) -> None:
    add_player(conn, VOTER, "Alice")
    add_player(conn, 2, "Bob")
    state = vw.apply_query(conn, VOTER, "1n:2")
    assert state is not None and state.get("e") == 1


def test_rating_the_last_player_ends_and_confirms_the_save(conn: sqlite3.Connection) -> None:
    add_player(conn, VOTER, "Alice")
    add_player(conn, 2, "Bob")
    state = vw.apply_query(conn, VOTER, "1r:2:333333")
    assert state is not None and state.get("e") == 1 and state["d"] == "Bob"


def test_a_card_for_a_removed_player_moves_on(conn: sqlite3.Connection) -> None:
    _seed(conn)
    state = vw.apply_query(conn, VOTER, "1r:999:333333")
    assert state is not None and state["p"] in (2, 3)
    assert conn.execute("SELECT COUNT(*) FROM scores").fetchone()[0] == 0


@pytest.mark.parametrize(
    ("voter", "data"),
    [
        (42, "1r:2:333333"),  # not on the roster
        (VOTER, "garbage"),
        (VOTER, "2r:2:333333"),  # unknown format version
        (VOTER, "1r:2:333336"),  # score out of range
        (VOTER, "1r:2"),  # submit without scores
        (VOTER, "1k:2:333333"),  # scores on a non-submit request
        (VOTER, f"1r:{VOTER}:333333"),  # rating yourself
    ],
)
def test_bad_requests_change_nothing(conn: sqlite3.Connection, voter: int, data: str) -> None:
    _seed(conn)
    assert vw.apply_query(conn, voter, data) is None
    assert conn.execute("SELECT COUNT(*) FROM scores").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM score_skips").fetchone()[0] == 0


def test_next_state_ends_when_the_offered_player_has_no_row(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(conn)
    monkeypatch.setattr(vw, "_player_name", lambda _c, _id: None)
    assert vw.next_state(conn, VOTER, 2, require_other=False).get("e") == 1


# --- Telegram calls -------------------------------------------------------


def _ctx(conn: sqlite3.Connection) -> MagicMock:
    ctx = MagicMock()
    ctx.bot_data = {"conn": conn}
    ctx.bot.do_api_request = AsyncMock()
    ctx.bot.send_message = AsyncMock()
    ctx.tasks = []
    ctx.application.create_task = MagicMock(side_effect=lambda coro, **_: ctx.tasks.append(coro))
    return ctx


async def _run_tasks(ctx: MagicMock) -> None:
    while ctx.tasks:
        await ctx.tasks.pop(0)


async def test_send_widget_card_sends_a_queryable_card_for_the_player(
    conn: sqlite3.Connection,
) -> None:
    ctx = _ctx(conn)
    assert await vw.send_widget_card(ctx, 7, 2, "Bob", "en") is True
    method = ctx.bot.do_api_request.call_args.args[0]
    kwargs = ctx.bot.do_api_request.call_args.kwargs["api_kwargs"]
    assert method == "sendWidget"
    assert kwargs["chat_id"] == 7 and kwargs["allow_queries"] is True
    assert kwargs["html"] == vw.build_widget_html("en")
    assert json.loads(kwargs["chat_data"])["p"] == 2
    assert kwargs["caption"] == t("vote.widget_caption", "en")


async def test_send_widget_card_treats_a_timeout_as_sent(conn: sqlite3.Connection) -> None:
    ctx = _ctx(conn)
    ctx.bot.do_api_request.side_effect = TimedOut()
    assert await vw.send_widget_card(ctx, 7, 2, "Bob", "en") is True


async def test_send_widget_card_reports_a_refusal(conn: sqlite3.Connection) -> None:
    ctx = _ctx(conn)
    ctx.bot.do_api_request.side_effect = BadRequest("method not found")
    assert await vw.send_widget_card(ctx, 7, 2, "Bob", "en") is False


async def test_send_widget_card_lets_forbidden_reach_the_caller(conn: sqlite3.Connection) -> None:
    ctx = _ctx(conn)
    ctx.bot.do_api_request.side_effect = Forbidden("bot was blocked")
    with pytest.raises(Forbidden):
        await vw.send_widget_card(ctx, 7, 2, "Bob", "en")


@pytest.mark.parametrize(
    "error",
    [None, BadRequest("Message is not modified"), BadRequest("chat not found"), NetworkError("x")],
)
async def test_persist_state_never_raises(
    conn: sqlite3.Connection, error: Exception | None
) -> None:
    ctx = _ctx(conn)
    ctx.bot.do_api_request.side_effect = error
    state = vw.end_state()
    edits = vw._card_edits(ctx, 7, 70)
    edits.latest = state["q"]
    await vw._persist_state(ctx, 7, 70, state, edits)
    assert ctx.bot.do_api_request.call_args.args[0] == "editMessageWidgetData"
    assert ctx.bot.do_api_request.call_args.kwargs["api_kwargs"]["message_id"] == 70


def _widget_update(**query: object) -> MagicMock:
    update = MagicMock()
    update.api_kwargs = {"widget_query": query}
    return update


async def test_widget_query_is_answered_with_the_new_state_then_persisted(
    conn: sqlite3.Connection,
) -> None:
    _seed(conn)
    ctx = _ctx(conn)
    update = _widget_update(
        id="q1", data="1r:2:333333", chat={"id": VOTER}, message_id=70, **{"from": {"id": VOTER}}
    )
    await vw.handle_widget_query(update, ctx)
    (method,) = ctx.bot.do_api_request.call_args.args
    answer = ctx.bot.do_api_request.call_args.kwargs["api_kwargs"]
    assert method == "answerWidgetQuery" and answer["widget_query_id"] == "q1"
    assert json.loads(answer["data"])["d"] == "Bob"
    ctx.application.create_task.assert_called_once()
    await _run_tasks(ctx)


async def test_widget_query_answer_failure_still_persists(conn: sqlite3.Connection) -> None:
    _seed(conn)
    ctx = _ctx(conn)
    ctx.bot.do_api_request.side_effect = BadRequest("query is too old")
    update = _widget_update(
        id="q1", data="1n:2", chat={"id": VOTER}, message_id=70, **{"from": {"id": VOTER}}
    )
    await vw.handle_widget_query(update, ctx)
    ctx.application.create_task.assert_called_once()
    await _run_tasks(ctx)


async def test_ignored_widget_query_gets_an_empty_answer_and_no_edit(
    conn: sqlite3.Connection,
) -> None:
    _seed(conn)
    ctx = _ctx(conn)
    update = _widget_update(id="q1", data="nonsense", **{"from": {"id": "not-an-id"}})
    await vw.handle_widget_query(update, ctx)
    assert ctx.bot.do_api_request.call_args.kwargs["api_kwargs"] == {"widget_query_id": "q1"}
    ctx.application.create_task.assert_not_called()


async def test_widget_query_without_a_message_is_answered_but_not_persisted(
    conn: sqlite3.Connection,
) -> None:
    _seed(conn)
    ctx = _ctx(conn)
    update = _widget_update(id="q1", data="1n:2", **{"from": {"id": VOTER}})
    await vw.handle_widget_query(update, ctx)
    assert "data" in ctx.bot.do_api_request.call_args.kwargs["api_kwargs"]
    ctx.application.create_task.assert_not_called()


@pytest.mark.parametrize("api_kwargs", [None, {}, {"widget_query": "x"}, {"widget_query": {}}])
async def test_other_updates_pass_through_untouched(
    conn: sqlite3.Connection, api_kwargs: dict | None
) -> None:
    ctx = _ctx(conn)
    update = MagicMock()
    update.api_kwargs = api_kwargs
    await vw.handle_widget_query(update, ctx)
    ctx.bot.do_api_request.assert_not_called()


async def test_an_older_state_never_overwrites_a_newer_one(conn: sqlite3.Connection) -> None:
    _seed(conn)
    ctx = _ctx(conn)

    def tap(data: str) -> MagicMock:
        return _widget_update(
            id=data, data=data, chat={"id": VOTER}, message_id=70, **{"from": {"id": VOTER}}
        )

    await vw.handle_widget_query(tap("1r:2:333333"), ctx)
    await vw.handle_widget_query(tap("1n:2"), ctx)
    ctx.bot.do_api_request.reset_mock()
    await _run_tasks(ctx)
    # Only the newest state is written: the card after the skip, not the one
    # the submit produced.
    edits = [
        c for c in ctx.bot.do_api_request.call_args_list if c.args[0] == "editMessageWidgetData"
    ]
    assert len(edits) == 1
    written = json.loads(edits[0].kwargs["api_kwargs"]["chat_data"])
    assert "d" not in written and written["p"] == 3


async def test_the_first_tap_clears_the_update_your_app_caption_once(
    conn: sqlite3.Connection,
) -> None:
    _seed(conn)
    ctx = _ctx(conn)
    for data in ("1n:2", "1n:3"):
        update = _widget_update(
            id=data, data=data, chat={"id": VOTER}, message_id=70, **{"from": {"id": VOTER}}
        )
        await vw.handle_widget_query(update, ctx)
        await _run_tasks(ctx)
    methods = [c.args[0] for c in ctx.bot.do_api_request.call_args_list]
    assert methods.count("editMessageCaption") == 1


async def test_a_failed_caption_clear_still_writes_the_state(conn: sqlite3.Connection) -> None:
    _seed(conn)
    ctx = _ctx(conn)

    async def fail_caption(method: str, **_: object) -> None:
        if method == "editMessageCaption":
            raise BadRequest("message can't be edited")

    ctx.bot.do_api_request.side_effect = fail_caption
    update = _widget_update(
        id="q", data="1n:2", chat={"id": VOTER}, message_id=70, **{"from": {"id": VOTER}}
    )
    await vw.handle_widget_query(update, ctx)
    await _run_tasks(ctx)
    methods = [c.args[0] for c in ctx.bot.do_api_request.call_args_list]
    assert methods[-1] == "editMessageWidgetData"


# --- the voting flow ------------------------------------------------------


async def test_fresh_prompt_is_a_widget_when_enabled(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(conn)
    monkeypatch.setattr(settings, "VOTE_WIDGET", True)
    ctx = _ctx(conn)
    await _send_next_prompt(conn, ctx, chat_id=VOTER, voter_id=VOTER)
    # The send runs in the background, so a slow sendWidget never holds up other updates.
    ctx.bot.do_api_request.assert_not_called()
    await _run_tasks(ctx)
    assert ctx.bot.do_api_request.call_args.args[0] == "sendWidget"
    ctx.bot.send_message.assert_not_called()


async def test_refused_widget_falls_back_to_the_button_card(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(conn)
    monkeypatch.setattr(settings, "VOTE_WIDGET", True)
    ctx = _ctx(conn)
    ctx.bot.do_api_request.side_effect = BadRequest("method not found")
    await _send_next_prompt(conn, ctx, chat_id=VOTER, voter_id=VOTER)
    await _run_tasks(ctx)
    assert ctx.bot.send_message.call_args.kwargs["reply_markup"] is not None


async def test_buttons_stay_when_the_widget_is_off(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(conn)
    monkeypatch.setattr(settings, "VOTE_WIDGET", False)
    ctx = _ctx(conn)
    await _send_next_prompt(conn, ctx, chat_id=VOTER, voter_id=VOTER)
    ctx.bot.do_api_request.assert_not_called()
    ctx.bot.send_message.assert_called_once()


async def test_a_blocked_widget_dm_calls_back_the_caller(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(conn)
    monkeypatch.setattr(settings, "VOTE_WIDGET", True)
    ctx = _ctx(conn)
    ctx.bot.do_api_request.side_effect = Forbidden("bot was blocked by the user")
    on_forbidden = AsyncMock()
    await _send_next_prompt(conn, ctx, chat_id=VOTER, voter_id=VOTER, on_forbidden=on_forbidden)
    await _run_tasks(ctx)
    on_forbidden.assert_awaited_once()


@pytest.mark.parametrize("error", [Forbidden("blocked"), NetworkError("down")])
async def test_background_send_failures_never_raise(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    _seed(conn)
    monkeypatch.setattr(settings, "VOTE_WIDGET", True)
    ctx = _ctx(conn)
    ctx.bot.do_api_request.side_effect = error
    await _send_next_prompt(conn, ctx, chat_id=VOTER, voter_id=VOTER)
    await _run_tasks(ctx)
    ctx.bot.send_message.assert_not_called()


async def test_group_vote_with_a_blocked_dm_still_nudges_in_the_group(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(conn)
    monkeypatch.setattr(settings, "VOTE_WIDGET", True)
    ctx = _ctx(conn)
    ctx.bot.username = "toop_bot"
    ctx.bot.delete_message = AsyncMock()
    ctx.bot.send_message = AsyncMock(return_value=MagicMock(message_id=9))
    ctx.bot.do_api_request.side_effect = Forbidden("bot can't initiate conversation")
    update = MagicMock()
    update.effective_user = MagicMock(id=VOTER, username="alice", full_name="Alice")
    update.effective_chat = MagicMock(id=-100123, type=ChatType.GROUP)
    update.effective_message = MagicMock(message_id=555)
    await handle_vote_command(update, ctx)
    await _run_tasks(ctx)
    nudge = ctx.bot.send_message.call_args.kwargs
    assert nudge["chat_id"] == -100123 and "@alice" in nudge["text"]
