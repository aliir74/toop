-- توپ — SQLite schema. Applied idempotently on startup.
-- Privacy invariant: vote_aggregates and answered_prompts are NEVER joined.

CREATE TABLE IF NOT EXISTS players (
    telegram_id     INTEGER PRIMARY KEY,
    username        TEXT,
    display_name    TEXT NOT NULL,
    joined_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    active          INTEGER NOT NULL DEFAULT 1,
    is_calibrating  INTEGER NOT NULL DEFAULT 1
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
-- This table NEVER stores voter identity.
CREATE TABLE IF NOT EXISTS vote_aggregates (
    player_a        INTEGER NOT NULL REFERENCES players(telegram_id) ON DELETE CASCADE,
    player_b        INTEGER NOT NULL REFERENCES players(telegram_id) ON DELETE CASCADE,
    axis            TEXT NOT NULL CHECK (axis IN ('attack', 'defense', 'setting')),
    a_wins          INTEGER NOT NULL DEFAULT 0,
    b_wins          INTEGER NOT NULL DEFAULT 0,
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
