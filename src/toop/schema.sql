-- توپ — SQLite schema. Applied idempotently on startup.
-- Rating model: independent absolute 1–5 scoring across 6 indicators
--   (attack, receive, block, setting, serve, positioning).
-- Privacy posture (B2, decided 2026-06-05): scores are voter-linked — the
-- `scores` table carries voter_id AND the raw 1–5 score — so per-rater bias can
-- be normalized at refit time. Privacy is deliberately relaxed vs the retired
-- pairwise model; only the admin has DB access.

-- Presence log of everyone who has DM'd the bot. Standalone — NEVER joined to
-- players or any score table. Exists only so the admin can see who is reachable
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
    -- only scored ON. Linked to a real account later via link_ghost_player.
    is_ghost        INTEGER NOT NULL DEFAULT 0,
    -- Optional custom profile photo set by the admin via /set_photo. Stores the
    -- Telegram file_id (reusable to re-send the same image); NULL = no photo, so
    -- the rating card falls back to text. Original bytes are also backed up under
    -- data/photos/ because file_ids die if the bot is ever recreated from scratch.
    photo_file_id   TEXT
);

-- Global key/value bot state, distinct from any per-player or per-session row.
-- Currently holds 'events_paused_until' (an ISO-8601 UTC timestamp): while that
-- instant is in the future, the weekly attendance-poll job and the auto-snapshot
-- job skip, so no session is created in that window. Cleared by /resume_events.
CREATE TABLE IF NOT EXISTS bot_state (
    key             TEXT PRIMARY KEY,
    value           TEXT NOT NULL,
    updated_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
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

-- Bot-owned polls posted to the group, keyed by Telegram's poll_id so an
-- incoming poll_answer maps back to its session + kind. 'attendance' is the
-- weekly بلی/خیر poll (its yes-votes feed the rsvps table); 'reservation' is
-- the waitlist poll opened once attendance caps. quorum_announced / cap_closed
-- are one-shot latches on the attendance poll so each threshold fires once.
CREATE TABLE IF NOT EXISTS session_polls (
    poll_id          TEXT PRIMARY KEY,
    session_id       INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    kind             TEXT NOT NULL CHECK (kind IN ('attendance', 'reservation')),
    message_id       INTEGER,
    closed           INTEGER NOT NULL DEFAULT 0,
    quorum_announced INTEGER NOT NULL DEFAULT 0,
    cap_closed       INTEGER NOT NULL DEFAULT 0,
    created_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Per-voter, per-player, per-indicator absolute score on a 1–5 scale.
-- The PRIMARY KEY dedupes (one score per voter/player/indicator); writing via
-- UPSERT makes a score editable (a voter can re-tap to change it). The row
-- carries voter_id AND the raw score (B2) — this is what enables per-rater
-- normalization in rating.refresh_ratings.
CREATE TABLE IF NOT EXISTS scores (
    voter_id        INTEGER NOT NULL REFERENCES players(telegram_id) ON DELETE CASCADE,
    player_id       INTEGER NOT NULL REFERENCES players(telegram_id) ON DELETE CASCADE,
    indicator       TEXT NOT NULL CHECK (indicator IN
                        ('attack', 'receive', 'block', 'setting', 'serve', 'positioning')),
    score           INTEGER NOT NULL CHECK (score BETWEEN 1 AND 5),
    updated_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (voter_id, player_id, indicator),
    CHECK (voter_id != player_id)
);

-- Voter-side "skip / 🤷 don't know" dedupe. Carries no score; only prevents the
-- (voter, player, indicator) target from being re-asked.
CREATE TABLE IF NOT EXISTS score_skips (
    voter_id        INTEGER NOT NULL REFERENCES players(telegram_id) ON DELETE CASCADE,
    player_id       INTEGER NOT NULL REFERENCES players(telegram_id) ON DELETE CASCADE,
    indicator       TEXT NOT NULL CHECK (indicator IN
                        ('attack', 'receive', 'block', 'setting', 'serve', 'positioning')),
    skipped_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (voter_id, player_id, indicator),
    CHECK (voter_id != player_id)
);

-- Reserve queue, filled by the reservation poll opened once attendance caps.
-- Ordered FIFO (created_at, then telegram_id) so promotion suggestions are
-- stable. A row here means "willing to take a freed seat".
CREATE TABLE IF NOT EXISTS waitlist (
    session_id      INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    telegram_id     INTEGER NOT NULL REFERENCES players(telegram_id) ON DELETE CASCADE,
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (session_id, telegram_id)
);

-- Dedupe for post-snapshot attendance-drift DMs: stores the last drift
-- signature the admin was notified about, so an unchanged drift state (or a
-- vote that doesn't move the attendee set) never re-pings.
CREATE TABLE IF NOT EXISTS drift_notices (
    session_id      INTEGER PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
    last_signature  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS snapshots (
    session_id      INTEGER PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
    team_a_json     TEXT NOT NULL,
    team_b_json     TEXT NOT NULL,
    cut_json        TEXT NOT NULL DEFAULT '[]',
    metrics_json    TEXT NOT NULL,
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Cached per-indicator rating. `score` holds the rater-normalized, shrunk
-- estimate computed by rating.refresh_ratings (NOT a raw mean and NOT the old
-- Bradley-Terry log-skill).
CREATE TABLE IF NOT EXISTS player_ratings (
    telegram_id     INTEGER NOT NULL REFERENCES players(telegram_id) ON DELETE CASCADE,
    indicator       TEXT NOT NULL CHECK (indicator IN
                        ('attack', 'receive', 'block', 'setting', 'serve', 'positioning')),
    score           REAL NOT NULL,
    vote_count      INTEGER NOT NULL DEFAULT 0,
    calibrated      INTEGER NOT NULL DEFAULT 0,
    computed_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (telegram_id, indicator)
);

CREATE INDEX IF NOT EXISTS idx_scores_voter
    ON scores(voter_id);

CREATE INDEX IF NOT EXISTS idx_scores_player_indicator
    ON scores(player_id, indicator);

CREATE INDEX IF NOT EXISTS idx_rsvps_session_status
    ON rsvps(session_id, status);

CREATE INDEX IF NOT EXISTS idx_attendance_telegram
    ON attendance(telegram_id, session_id);
