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
