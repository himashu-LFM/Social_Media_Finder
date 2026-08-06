-- ============================================================================
--  Curator AI — accounts, sessions and history
--  Run in pgAdmin after 001 and 002.
--
--  Adds the three things a shared internal tool needs before it can be exposed
--  to a team: who is using it, what they uploaded, and what each run produced.
--
--  No password is stored anywhere in this file or in the database. `app_user`
--  holds an Argon2id hash; `user_session` holds a SHA-256 hash of the bearer
--  token, so a database leak yields neither a password nor a usable session.
-- ============================================================================

BEGIN;

-- ── accounts ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS app_user (
    id            BIGSERIAL   PRIMARY KEY,
    email         TEXT        NOT NULL,
    name          TEXT,
    password_hash TEXT        NOT NULL,          -- Argon2id, never a password
    role          TEXT        NOT NULL DEFAULT 'analyst',  -- analyst | admin
    is_active     BOOLEAN     NOT NULL DEFAULT TRUE,
    last_login_at TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Case-insensitive: nobody should be able to register Alice@x and alice@x.
CREATE UNIQUE INDEX IF NOT EXISTS app_user_email_uniq ON app_user (lower(email));

DROP TRIGGER IF EXISTS app_user_set_updated_at ON app_user;
CREATE TRIGGER app_user_set_updated_at
    BEFORE UPDATE ON app_user
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();


-- ── sessions ────────────────────────────────────────────────────────────────
-- Server-side sessions rather than self-contained tokens, so sign-out and
-- "revoke everywhere" actually take effect immediately.
CREATE TABLE IF NOT EXISTS user_session (
    token_hash  TEXT        PRIMARY KEY,          -- sha256 of the bearer token
    user_id     BIGINT      NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
    user_agent  TEXT,
    expires_at  TIMESTAMPTZ NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS user_session_user_idx    ON user_session (user_id);
CREATE INDEX IF NOT EXISTS user_session_expires_idx ON user_session (expires_at);


-- ── upload history ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS upload_history (
    id           BIGSERIAL   PRIMARY KEY,
    job_id       TEXT        REFERENCES job(id) ON DELETE SET NULL,
    filename     TEXT        NOT NULL,
    size_bytes   BIGINT,
    row_count    INTEGER,
    uploaded_by  BIGINT      REFERENCES app_user(id) ON DELETE SET NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS upload_history_created_idx ON upload_history (created_at DESC);


-- ── attribute jobs and decisions to a person ────────────────────────────────
ALTER TABLE job          ADD COLUMN IF NOT EXISTS started_by BIGINT REFERENCES app_user(id) ON DELETE SET NULL;
ALTER TABLE verified_url ADD COLUMN IF NOT EXISTS decided_by BIGINT REFERENCES app_user(id) ON DELETE SET NULL;
ALTER TABLE rejected_url ADD COLUMN IF NOT EXISTS decided_by BIGINT REFERENCES app_user(id) ON DELETE SET NULL;

COMMIT;


-- ============================================================================
--  Creating the first account
--
--  Do NOT insert a password here — this file lives in git. Use the bootstrap
--  script, which hashes the password before it touches the database and never
--  writes it to disk or to the shell history file:
--
--      python create_user.py
--
--  It prompts for the email and password interactively.
-- ============================================================================
--
--  Useful queries:
--    SELECT id, email, name, role, last_login_at FROM app_user ORDER BY id;
--    SELECT count(*) FROM user_session WHERE expires_at > now();   -- live sessions
--    DELETE FROM user_session WHERE expires_at < now();            -- prune expired
