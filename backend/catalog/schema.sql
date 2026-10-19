-- triptych catalog (backend/catalog.db). Reference data only — never user data.

PRAGMA journal_mode = WAL;

-- 1 row = one work from one source (a book is one volume).
CREATE TABLE IF NOT EXISTS entries (
  id              TEXT PRIMARY KEY,           -- "<source>:<external_id>"
  source          TEXT NOT NULL,              -- anilist | syoboi | narou | kakuyomu | ndl
  external_id     TEXT NOT NULL,
  kind            TEXT NOT NULL,              -- anime | manga | novel | web-novel | book
  format          TEXT,                       -- TV / MOVIE / OVA / MANGA / NOVEL / ONE_SHOT / 文庫 ...
  title           TEXT NOT NULL,              -- display title (Japanese first)
  title_native    TEXT,
  title_romaji    TEXT,
  title_english   TEXT,
  title_reading   TEXT,                       -- よみ (syoboi TitleYomi, NDL titleTranscription)
  synonyms        TEXT NOT NULL DEFAULT '[]', -- JSON array of strings
  status          TEXT,                       -- RELEASING / NOT_YET_RELEASED / FINISHED / CANCELLED / HIATUS / 連載中 / 完結
  start_date      TEXT,                       -- YYYY-MM-DD (filler 01 for month/year precision)
  start_precision TEXT,                       -- day | month | year
  end_date        TEXT,
  end_precision   TEXT,
  season          TEXT,                       -- WINTER / SPRING / SUMMER / FALL
  season_year     INTEGER,
  episodes        INTEGER,                    -- anime episodes / web-novel parts
  chapters        INTEGER,
  volumes         INTEGER,
  genres          TEXT NOT NULL DEFAULT '[]', -- JSON array
  tags            TEXT NOT NULL DEFAULT '[]', -- JSON array
  studios         TEXT NOT NULL DEFAULT '[]', -- JSON array (anime) / author (web-novel, book) goes to creators
  creators        TEXT NOT NULL DEFAULT '[]', -- JSON array: 作者・漫画家
  popularity      INTEGER,                    -- AniList popularity / なろう global_point / カクヨム totalReviewPoint
  score           INTEGER,                    -- AniList averageScore (0-100)
  source_material TEXT,                       -- AniList source enum (LIGHT_NOVEL / MANGA / WEB_NOVEL / ORIGINAL ...)
  url             TEXT,
  cover           TEXT,
  extra           TEXT NOT NULL DEFAULT '{}', -- JSON: source specific (isbn, publisher, ndc, line, tid, ncode genre, hasPublication ...)
  fetched_at      TEXT NOT NULL,              -- ISO 8601 UTC
  UNIQUE (source, external_id)
);
CREATE INDEX IF NOT EXISTS entries_kind ON entries(kind);
CREATE INDEX IF NOT EXISTS entries_source ON entries(source);
CREATE INDEX IF NOT EXISTS entries_start ON entries(start_date);

-- AniList relation edges. to_id may point outside the catalog.
CREATE TABLE IF NOT EXISTS relations (
  from_id       TEXT NOT NULL,
  to_id         TEXT NOT NULL,
  relation_type TEXT NOT NULL,
  PRIMARY KEY (from_id, to_id, relation_type)
);
CREATE INDEX IF NOT EXISTS relations_to ON relations(to_id);

-- Cross-source links made by the linker.
CREATE TABLE IF NOT EXISTS links (
  a_id       TEXT NOT NULL,                   -- non-AniList entry (syoboi / narou / kakuyomu / ndl)
  b_id       TEXT NOT NULL,                   -- AniList entry
  method     TEXT NOT NULL,                   -- title-exact / title-year / synonym / title-prefix / series
  confidence REAL NOT NULL,
  PRIMARY KEY (a_id, b_id)
);
CREATE INDEX IF NOT EXISTS links_b ON links(b_id);

-- entry -> work (cluster). cluster_id is the smallest member id, so it is stable across runs.
CREATE TABLE IF NOT EXISTS clusters (
  entry_id   TEXT PRIMARY KEY,
  cluster_id TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS clusters_cluster ON clusters(cluster_id);

CREATE TABLE IF NOT EXISTS cluster_summary (
  cluster_id      TEXT PRIMARY KEY,
  title           TEXT NOT NULL,              -- representative title
  title_norm      TEXT NOT NULL,
  kinds           TEXT NOT NULL,              -- JSON array of kinds present
  has_anime       INTEGER NOT NULL DEFAULT 0,
  has_manga       INTEGER NOT NULL DEFAULT 0,
  has_novel       INTEGER NOT NULL DEFAULT 0,
  has_web_novel   INTEGER NOT NULL DEFAULT 0,
  has_book        INTEGER NOT NULL DEFAULT 0,
  entry_count     INTEGER NOT NULL,
  anime_status    TEXT,                       -- most "live" anime status (RELEASING > NOT_YET_RELEASED > FINISHED)
  first_year      INTEGER,
  last_year       INTEGER,
  seasons         TEXT NOT NULL DEFAULT '[]', -- JSON array of "YYYY-SEASON"
  next_date       TEXT,                       -- nearest upcoming date (anime start / next episode / book) >= today at link time
  next_precision  TEXT,
  next_label      TEXT,
  popularity      INTEGER NOT NULL DEFAULT 0, -- max AniList popularity in the cluster (others scaled to 0)
  cover           TEXT,                       -- the original medium's cover (novel / manga / the first anime season)
  origin          TEXT,                       -- the original medium: novel / manga / anime (NULL when unknown)
  novel_cover     TEXT,                       -- the original novel's cover (AniList NOVEL), for the work's novel books
  manga_cover     TEXT                        -- the main manga's cover, for the work's manga books
);
CREATE INDEX IF NOT EXISTS summary_pop ON cluster_summary(popularity DESC);
CREATE INDEX IF NOT EXISTS summary_next ON cluster_summary(next_date);
CREATE INDEX IF NOT EXISTS summary_last ON cluster_summary(last_year DESC);

-- Full-text search over titles (trigram: 3+ chars). Rebuilt by the linker.
-- text = every name of the entry, then those names folded to NFKC + lowercase + single spaces when that
-- differs (see search_text() in text.py), so full-width / half-width spellings find each other.
-- The API folds the user's input the same way; 1-2 character queries cannot use the trigram index and
-- scan this column instead (instr(lower(text), ...)).
CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5(
  entry_id UNINDEXED,
  cluster_id UNINDEXED,
  text,
  tokenize = 'trigram'
);

-- Resume checkpoints: one row per harvester slice.
CREATE TABLE IF NOT EXISTS harvest_state (
  source     TEXT NOT NULL,
  slice_key  TEXT NOT NULL,
  status     TEXT NOT NULL,                   -- pending | done | error
  fetched    INTEGER NOT NULL DEFAULT 0,
  cursor     TEXT,                            -- page / st / idx to resume from
  message    TEXT,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (source, slice_key)
);

CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
