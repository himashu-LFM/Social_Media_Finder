-- ============================================================================
--  Curator AI — job persistence
--  Run in pgAdmin after 001_create_url_tables.sql.
--
--  Why: jobs and their results previously lived only in a Python dict and on
--  local disk, so a restart lost every in-flight job and every export. On a
--  container platform that restarts routinely, that is data loss by design.
--
--  `rows` stores the finished result set as JSONB, so the Results page can be
--  served without the .xlsx file existing at all — the workbook becomes a
--  convenience artifact rather than the system of record.
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS job (
    id                  TEXT        PRIMARY KEY,           -- uuid from the API
    status              TEXT        NOT NULL,              -- queued|running|cancelling|cancelled|completed|failed
    source_filename     TEXT,
    names               JSONB       NOT NULL DEFAULT '[]', -- per-row progress state
    rows                JSONB,                             -- final result rows
    output_path         TEXT,
    serper_output_path  TEXT,
    error               TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Newest-first listing, and a cheap way to find work abandoned by a restart.
CREATE INDEX IF NOT EXISTS job_created_at_idx ON job (created_at DESC);
CREATE INDEX IF NOT EXISTS job_status_idx     ON job (status);

DROP TRIGGER IF EXISTS job_set_updated_at ON job;
CREATE TRIGGER job_set_updated_at
    BEFORE UPDATE ON job
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();   -- defined in 001

COMMIT;

-- ============================================================================
--  Useful after a restart: any job left mid-flight is orphaned, because the
--  process that owned it is gone. Mark them so the UI stops showing a spinner.
-- ============================================================================
--
--  UPDATE job SET status = 'failed',
--                 error  = 'Interrupted by a server restart.'
--  WHERE status IN ('queued', 'running', 'cancelling');
--
--  SELECT id, status, jsonb_array_length(names) AS rows,
--         created_at, error
--  FROM job ORDER BY created_at DESC LIMIT 20;
