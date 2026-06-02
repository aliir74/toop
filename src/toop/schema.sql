-- توپ — SQLite schema. Applied idempotently on startup.
-- Privacy invariant: vote_aggregates and answered_prompts are NEVER joined.

-- Presence log of everyone who has DM'd the bot. Standalone — NEVER joined to
-- players or any vote table. Exists only so the admin can see who is reachable
-- (i.e. resolvable by /add_player) before adding them to the roster.
CREATE TABLE IF NOT EXISTS contacts (
    telegram_id     INTEGER PRIMARY KEY,
    username        TEXT,
    display_name    TEXT,
    first_seen_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS players (
    telegram_id     INTEGER PRIMARY KEY,
    username        TEXT,
    display_name    TEXT NOT NULL,
    joined_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    active          INTEGER NOT NULL DEFAULT 1,
    is_calibrating  INTEGER NOT NULL DEFAULT 1,
    -- Rating-pool membership. in_pool=0 is a manual "stop asking others to rate
    -- this player" toggle; pool_paused_until is the same thing on a timer.
    -- A player is rateable iff active=1 AND in_pool=1 AND not currently paused.
    in_pool         INTEGER NOT NULL DEFAULT 1,
    pool_paused_until TIMESTAMP,
    -- Accountless "ghost" player: synthetic negative telegram_id, never DM'd,
    -- only voted ON. Linked to a real account later via link_ghost_player.
    is_ghost        INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_date    DATE NOT NULL,
    snapshot_at     TIMESTAMP,
    status          TEXT NOT NULL DEFAULT 'open'
                    CHECK (status IN ('open', 'snapshotted', 'published', 'done'))
);

CREATE TABLE IF NOT EXISTS rsvps (
    session_id      INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    telegram_id     INTEGER NOT NULL REFERENCES players(telegram_id) ON DELETE CASCADE,
    status          TEXT NOT NULL CHECK (status IN ('yes', 'no', 'maybe')),
    locked_in       INTEGER NOT NULL DEFAULT 0,
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (session_id, telegram_id)
);

CREATE TABLE IF NOT EXISTS attendance (
    session_id      INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    telegram_id     INTEGER NOT NULL REFERENCES players(telegram_id) ON DELETE CASCADE,
    was_attendee    INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (session_id, telegram_id)
);

-- Pairwise outcome counts. Invariant: player_a < player_b (enforced via CHECK).
-- This table NEVER stores voter identity. dont_know counts "🤷 Don't know" taps
-- on this pair (no winner) — an aggregate signal that nobody can rate the pair.
CREATE TABLE IF NOT EXISTS vote_aggregates (
    player_a        INTEGER NOT NULL REFERENCES players(telegram_id) ON DELETE CASCADE,
    player_b        INTEGER NOT NULL REFERENCES players(telegram_id) ON DELETE CASCADE,
    axis            TEXT NOT NULL CHECK (axis IN ('attack', 'defense', 'setting')),
    a_wins          INTEGER NOT NULL DEFAULT 0,
    b_wins          INTEGER NOT NULL DEFAULT 0,
    dont_know       INTEGER NOT NULL DEFAULT 0,
    updated_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (player_a, player_b, axis),
    CHECK (player_a < player_b)
);

CREATE TABLE IF NOT EXISTS pending_prompts (
    voter_id        INTEGER NOT NULL REFERENCES players(telegram_id) ON DELETE CASCADE,
    player_a        INTEGER NOT NULL REFERENCES players(telegram_id) ON DELETE CASCADE,
    player_b        INTEGER NOT NULL REFERENCES players(telegram_id) ON DELETE CASCADE,
    axis            TEXT NOT NULL CHECK (axis IN ('attack', 'defense', 'setting')),
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    info_gain       REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (voter_id, player_a, player_b, axis),
    CHECK (player_a < player_b),
    CHECK (voter_id != player_a AND voter_id != player_b)
);

-- Voter-side dedupe. NO outcome stored here — outcome flows to vote_aggregates only.
CREATE TABLE IF NOT EXISTS answered_prompts (
    voter_id        INTEGER NOT NULL REFERENCES players(telegram_id) ON DELETE CASCADE,
    player_a        INTEGER NOT NULL REFERENCES players(telegram_id) ON DELETE CASCADE,
    player_b        INTEGER NOT NULL REFERENCES players(telegram_id) ON DELETE CASCADE,
    axis            TEXT NOT NULL CHECK (axis IN ('attack', 'defense', 'setting')),
    answered_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (voter_id, player_a, player_b, axis),
    CHECK (player_a < player_b)
);

CREATE TABLE IF NOT EXISTS snoozes (
    voter_id        INTEGER NOT NULL REFERENCES players(telegram_id) ON DELETE CASCADE,
    axis            TEXT NOT NULL CHECK (axis IN ('attack', 'defense', 'setting')),
    snoozed_until   TIMESTAMP NOT NULL,
    PRIMARY KEY (voter_id, axis)
);

CREATE TABLE IF NOT EXISTS snapshots (
    session_id      INTEGER PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
    team_a_json     TEXT NOT NULL,
    team_b_json     TEXT NOT NULL,
    cut_json        TEXT NOT NULL DEFAULT '[]',
    metrics_json    TEXT NOT NULL,
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS player_ratings (
    telegram_id     INTEGER NOT NULL REFERENCES players(telegram_id) ON DELETE CASCADE,
    axis            TEXT NOT NULL CHECK (axis IN ('attack', 'defense', 'setting')),
    score           REAL NOT NULL,
    vote_count      INTEGER NOT NULL DEFAULT 0,
    calibrated      INTEGER NOT NULL DEFAULT 0,
    computed_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (telegram_id, axis)
);

CREATE INDEX IF NOT EXISTS idx_pending_prompts_voter_gain
    ON pending_prompts(voter_id, info_gain DESC);

CREATE INDEX IF NOT EXISTS idx_vote_aggregates_pair_axis
    ON vote_aggregates(player_a, player_b, axis);

CREATE INDEX IF NOT EXISTS idx_rsvps_session_status
    ON rsvps(session_id, status);

CREATE INDEX IF NOT EXISTS idx_attendance_telegram
    ON attendance(telegram_id, session_id);
