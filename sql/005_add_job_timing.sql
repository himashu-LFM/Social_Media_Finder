-- ============================================================================
--  Curator AI — persist a job's run timing
--  Run in pgAdmin after 002_create_job_tables.sql.
--
--  Why: started_at / finished_at / duration_seconds lived only in the in-memory
--  job dict, so a job restored from Postgres after a restart (or shown in history)
--  reported a 0:00 execution time. Persisting them lets the timer survive a reload.
--
--  Epoch seconds (DOUBLE PRECISION), matching time.time() in the API.
-- ============================================================================

BEGIN;

ALTER TABLE job ADD COLUMN IF NOT EXISTS started_at       DOUBLE PRECISION;
ALTER TABLE job ADD COLUMN IF NOT EXISTS finished_at      DOUBLE PRECISION;
ALTER TABLE job ADD COLUMN IF NOT EXISTS duration_seconds DOUBLE PRECISION;

COMMIT;
