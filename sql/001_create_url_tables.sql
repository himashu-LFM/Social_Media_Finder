-- ============================================================================
--  Curator AI — analyst decision tables
--  Run this once in pgAdmin against your target database (Query Tool → Execute).
--
--  Two tables, identical shape:
--    verified_url  — profiles an analyst confirmed
--    rejected_url  — profiles an analyst rejected (never suggest these again)
--
--  ONE ROW PER TITLE. Saving an Instagram URL and later a TikTok URL for the
--  same title updates the SAME row rather than creating a second one. That is
--  enforced by the unique index on lower(title) plus the COALESCE upsert at the
--  bottom of this file — the upsert only overwrites the column being saved and
--  leaves every other platform untouched.
-- ============================================================================

BEGIN;

-- ── shared: keep updated_at honest ──────────────────────────────────────────
CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;


-- ── verified_url ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS verified_url (
    id                BIGSERIAL   PRIMARY KEY,
    title             TEXT        NOT NULL,
    title_category    TEXT,
    title_subcategory TEXT,
    instagram_url     TEXT,
    facebook_url      TEXT,
    tiktok_url        TEXT,
    twitter_url       TEXT,
    youtube_url       TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Case-insensitive one-row-per-title guarantee. Indexing the expression rather
-- than adding a normalised column keeps the table schema clean.
CREATE UNIQUE INDEX IF NOT EXISTS verified_url_title_uniq
    ON verified_url (lower(title));

DROP TRIGGER IF EXISTS verified_url_set_updated_at ON verified_url;
CREATE TRIGGER verified_url_set_updated_at
    BEFORE UPDATE ON verified_url
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();


-- ── rejected_url ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS rejected_url (
    id                BIGSERIAL   PRIMARY KEY,
    title             TEXT        NOT NULL,
    title_category    TEXT,
    title_subcategory TEXT,
    instagram_url     TEXT,
    facebook_url      TEXT,
    tiktok_url        TEXT,
    twitter_url       TEXT,
    youtube_url       TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS rejected_url_title_uniq
    ON rejected_url (lower(title));

DROP TRIGGER IF EXISTS rejected_url_set_updated_at ON rejected_url;
CREATE TRIGGER rejected_url_set_updated_at
    BEFORE UPDATE ON rejected_url
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

COMMIT;


-- ============================================================================
--  Reference: the upsert the application uses.
--  Pass NULL for every platform except the one being saved. COALESCE keeps the
--  other columns as they were, so the row accumulates instead of being wiped.
--  (The app builds this for you — shown here so the behaviour is reviewable.)
-- ============================================================================
--
--  INSERT INTO verified_url
--      (title, title_category, title_subcategory,
--       instagram_url, facebook_url, tiktok_url, twitter_url, youtube_url)
--  VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
--  ON CONFLICT (lower(title)) DO UPDATE SET
--      title_category    = COALESCE(EXCLUDED.title_category,    verified_url.title_category),
--      title_subcategory = COALESCE(EXCLUDED.title_subcategory, verified_url.title_subcategory),
--      instagram_url     = COALESCE(EXCLUDED.instagram_url,     verified_url.instagram_url),
--      facebook_url      = COALESCE(EXCLUDED.facebook_url,      verified_url.facebook_url),
--      tiktok_url        = COALESCE(EXCLUDED.tiktok_url,        verified_url.tiktok_url),
--      twitter_url       = COALESCE(EXCLUDED.twitter_url,       verified_url.twitter_url),
--      youtube_url       = COALESCE(EXCLUDED.youtube_url,       verified_url.youtube_url);
--
-- ============================================================================
--  Sanity checks — run these after the app has saved a few rows.
-- ============================================================================
--
--  SELECT title, title_category, instagram_url, facebook_url,
--         tiktok_url, twitter_url, youtube_url, updated_at
--  FROM verified_url ORDER BY updated_at DESC LIMIT 20;
--
--  -- Should return zero rows: proves nothing is scattered across duplicates.
--  SELECT lower(title), count(*) FROM verified_url
--  GROUP BY 1 HAVING count(*) > 1;
