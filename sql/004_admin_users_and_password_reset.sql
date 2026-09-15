-- ============================================================================
--  Curator AI — admin-created accounts and self-service password reset
--  Run in pgAdmin after 003.
--
--  Two changes:
--
--  1. `must_change_password` on app_user. An admin creates an account with a
--     temporary password; the holder is forced to replace it on first sign-in.
--     Without this flag a temp password becomes a permanent one, and the admin
--     who chose it can sign in as that person indefinitely.
--
--  2. `pw_reset_codes` — one live 6-digit code per email address, stored as a
--     SHA-256 hash. Six digits is only a million possibilities, so the attempt
--     cap below is what actually makes it safe, not the length of the code.
-- ============================================================================

BEGIN;

-- ── forced password change on first sign-in ─────────────────────────────────
ALTER TABLE app_user
    ADD COLUMN IF NOT EXISTS must_change_password BOOLEAN NOT NULL DEFAULT FALSE;

-- Who created the account, for an audit trail. NULL for the bootstrap account
-- made with scripts/create_user.py, which by definition has no creator.
ALTER TABLE app_user
    ADD COLUMN IF NOT EXISTS created_by BIGINT REFERENCES app_user(id) ON DELETE SET NULL;


-- ── password-reset codes ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS pw_reset_codes (
    -- One live code per address: requesting a new code REPLACES the old one, so
    -- an attacker cannot accumulate valid codes by spamming the endpoint.
    email       TEXT        PRIMARY KEY,
    -- sha256 hex of the 6 digits. NEVER the code itself — a database peek must
    -- not let anyone take over an account.
    code_hash   TEXT        NOT NULL,
    expires_at  TIMESTAMPTZ NOT NULL,
    -- Wrong guesses. At 5 the row is deleted and the code is burned.
    attempts    INTEGER     NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Lets the periodic cleanup find expired rows without a full scan.
CREATE INDEX IF NOT EXISTS pw_reset_codes_expires_idx ON pw_reset_codes (expires_at);

COMMIT;


-- ============================================================================
--  Promoting the first admin
--
--  Only an admin can create accounts from the portal, so exactly one account
--  has to be made outside it:
--
--      python scripts/create_user.py --admin
--
--  It prompts, so the password never reaches your shell history. No manual SQL
--  is needed — but if you ever need to promote an existing account:
--
--      UPDATE app_user SET role = 'admin' WHERE lower(email) = 'you@example.com';
--
--  Useful queries:
--    SELECT id, email, role, is_active, must_change_password FROM app_user ORDER BY id;
--    SELECT email, expires_at, attempts FROM pw_reset_codes;      -- live codes
--    DELETE FROM pw_reset_codes WHERE expires_at < now();         -- manual prune
-- ============================================================================
