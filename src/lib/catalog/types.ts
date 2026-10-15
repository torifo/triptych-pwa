// Catalog API shapes (`/api/catalog/*`). Mirrors backend/catalog/dto.py.

export type CatalogKind = 'anime' | 'manga' | 'novel' | 'web-novel' | 'book';
export type CatalogSort = 'popularity' | 'recent' | 'upcoming' | 'title';
export type CatalogPrecision = 'day' | 'month' | 'year';

export interface CatalogSourceCount {
  source: string;
  entries: number;
  lastHarvestAt?: string | null;
  errorSlices: number;
}

export interface CatalogStats {
  ready: boolean;
  works: number;
  entries: number;
  links: number;
  sources: CatalogSourceCount[];
  kinds: Partial<Record<CatalogKind, number>>;
  lastLinkAt?: string | null;
}

export interface CatalogWorkSummary {
  id: string;
  title: string;
  kinds: CatalogKind[];
  entryCount: number;
  animeStatus?: string | null;
  firstYear?: number | null;
  lastYear?: number | null;
  nextDate?: string | null;
  nextPrecision?: CatalogPrecision | null;
  nextLabel?: string | null;
  popularity: number;
  cover?: string | null;
}

export interface CatalogWorkPage {
  total: number;
  page: number;
  per: number;
  items: CatalogWorkSummary[];
}

export interface CatalogEntry {
  id: string; // "<source>:<externalId>"
  source: string;
  externalId: string;
  kind: CatalogKind;
  format?: string | null;
  title: string;
  titleNative?: string | null;
  titleRomaji?: string | null;
  titleEnglish?: string | null;
  synonyms: string[];
  status?: string | null;
  startDate?: string | null;
  startPrecision?: CatalogPrecision | null;
  endDate?: string | null;
  endPrecision?: CatalogPrecision | null;
  season?: string | null;
  seasonYear?: number | null;
  episodes?: number | null;
  chapters?: number | null;
  volumes?: number | null;
  genres: string[];
  studios: string[];
  creators: string[];
  popularity?: number | null;
  score?: number | null;
  url?: string | null;
  cover?: string | null;
  extra: Record<string, unknown>;
  linkMethod?: string | null;
  linkConfidence?: number | null;
}

export interface CatalogWorkDetail {
  id: string;
  title: string;
  kinds: CatalogKind[];
  entries: CatalogEntry[];
  candidates: CatalogEntry[];
}

export interface CatalogQuery {
  q?: string;
  kind?: CatalogKind;
  has?: CatalogKind[];
  status?: 'RELEASING' | 'NOT_YET_RELEASED' | 'FINISHED';
  year?: number;
  season?: 'WINTER' | 'SPRING' | 'SUMMER' | 'FALL';
  sort?: CatalogSort;
  page?: number;
  per?: number;
}

// ---------------------------------------------------------------------------
// カレンダーの重ね表示（GET /api/catalog/events）
// ---------------------------------------------------------------------------

export type CatalogEventKind = 'anime' | 'book';
/** episode は放送中のアニメの次の話（label は「第3話」）。 */
export type CatalogEventType = 'episode' | 'season-start' | 'movie' | 'release';

export interface CatalogEvent {
  clusterId: string;
  entryId: string; // "<source>:<externalId>"
  title: string;
  kind: CatalogEventKind;
  event: CatalogEventType;
  date: string; // YYYY-MM-DD
  /** JST の実時刻 'HH:MM'（放送の話など、分かるものだけ）。 */
  time?: string | null;
  label: string;
  cover?: string | null;
  popularity?: number | null;
}

export interface CatalogEventPage {
  items: CatalogEvent[];
  /** 上限（600 件）で切れたか。 */
  truncated: boolean;
}

export interface CatalogEventQuery {
  from: string; // YYYY-MM-DD
  to: string; // YYYY-MM-DD（from から最大 62 日）
  kinds?: CatalogEventKind[];
}
