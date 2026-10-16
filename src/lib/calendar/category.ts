// カレンダーの分類（番組・発売・Web）と、日の段・升目とタブの件数。純関数で vitest 固定。

import type { CatalogEvent } from '../catalog/types';
import type { Edition, MediaType, Release } from '../db/schema';
import { timeMinutes } from '../components/release-view';
import { MEDIA_FILTERS, mediaFilterOf, type MediaFilter } from './view';

export type DayCategory = 'program' | 'release' | 'web';

/** 段の並び（番組 → 発売 → Web）。 */
export const DAY_CATEGORIES: readonly DayCategory[] = ['program', 'release', 'web'];

export const CATEGORY_LABELS: Record<DayCategory, string> = { program: '番組', release: '発売', web: 'Web' };

export type CategoryCounts = Record<DayCategory, number>;

type EditionMedia = ReadonlyMap<string, Pick<Edition, 'mediaType'>>;

// ---------------------------------------------------------------------------
// 分類
// ---------------------------------------------------------------------------

/** 絞り込みの値が指す分類（すべては null）。 */
export function categoryOfFilter(filter: MediaFilter): DayCategory | null {
  return MEDIA_FILTERS.find((item) => item.value === filter)?.category ?? null;
}

/**
 * Library の Release の分類: Edition の媒体で決める（アニメ → 番組、Web 小説 → Web、ほか → 発売）。
 * タブの絞り込み（matchesMediaFilter）と同じ表から引くので、タブと段が食い違わない。
 */
export function releaseCategory(mediaType: MediaType | undefined): DayCategory {
  const filter = mediaFilterOf(mediaType);
  return (filter && categoryOfFilter(filter)) ?? 'release';
}

/** カタログの出来事の分類: アニメ → 番組、本 → 発売。 */
export function eventCategory(event: Pick<CatalogEvent, 'kind'>): DayCategory {
  return event.kind === 'anime' ? 'program' : 'release';
}

// ---------------------------------------------------------------------------
// 日の段
// ---------------------------------------------------------------------------

export interface DaySection<R, E> {
  category: DayCategory;
  /** Library の予定（入力の並びのまま）。 */
  releases: R[];
  /** カタログの出来事（番組は時刻順、発売は人気順）。 */
  events: E[];
  /** Library とカタログの合計。 */
  count: number;
}

function byTime<E extends Pick<CatalogEvent, 'time'>>(list: readonly E[]): E[] {
  // sort は安定なので、時刻の無いもの同士は入力の順（人気順）のまま末尾に
  return [...list].sort((a, b) => {
    const ta = timeMinutes(a.time ?? undefined);
    const tb = timeMinutes(b.time ?? undefined);
    return ta === tb ? 0 : ta < tb ? -1 : 1;
  });
}

function byPopularity<E extends Pick<CatalogEvent, 'popularity'>>(list: readonly E[]): E[] {
  return [...list].sort((a, b) => (b.popularity ?? -1) - (a.popularity ?? -1));
}

/**
 * 1日分（リストでは1か月分）を段に分ける。番組 → 発売 → Web の順で、空の段は出さない。
 * Library の並びは保つ（呼ぶ側で日付・時刻順にしてある）。
 */
export function daySections<
  R extends Pick<Release, 'editionId'>,
  E extends Pick<CatalogEvent, 'kind' | 'time' | 'popularity'>,
>(releases: readonly R[], events: readonly E[], editions: EditionMedia): DaySection<R, E>[] {
  const buckets: Record<DayCategory, { releases: R[]; events: E[] }> = {
    program: { releases: [], events: [] },
    release: { releases: [], events: [] },
    web: { releases: [], events: [] },
  };
  for (const release of releases) buckets[releaseCategory(editions.get(release.editionId)?.mediaType)].releases.push(release);
  for (const event of events) buckets[eventCategory(event)].events.push(event);

  const sections: DaySection<R, E>[] = [];
  for (const category of DAY_CATEGORIES) {
    const bucket = buckets[category];
    const count = bucket.releases.length + bucket.events.length;
    if (count === 0) continue;
    const sorted = category === 'program' ? byTime(bucket.events) : byPopularity(bucket.events);
    sections.push({ category, releases: bucket.releases, events: sorted, count });
  }
  return sections;
}

// ---------------------------------------------------------------------------
// 件数（升目・タブ）
// ---------------------------------------------------------------------------

export function emptyCounts(): CategoryCounts {
  return { program: 0, release: 0, web: 0 };
}

/** Library の予定とカタログの出来事を分類ごとに数える。 */
export function categoryCounts(
  releases: readonly Pick<Release, 'editionId'>[],
  events: readonly Pick<CatalogEvent, 'kind'>[],
  editions: EditionMedia,
): CategoryCounts {
  const counts = emptyCounts();
  for (const release of releases) counts[releaseCategory(editions.get(release.editionId)?.mediaType)] += 1;
  for (const event of events) counts[eventCategory(event)] += 1;
  return counts;
}

/** 日付ごとの件数（月の升目に印と数で出す）。 */
export function countsByDate(
  releases: readonly Pick<Release, 'editionId' | 'date'>[],
  events: readonly Pick<CatalogEvent, 'kind' | 'date'>[],
  editions: EditionMedia,
): Map<string, CategoryCounts> {
  const map = new Map<string, CategoryCounts>();
  const add = (date: string, category: DayCategory) => {
    const counts = map.get(date) ?? emptyCounts();
    counts[category] += 1;
    map.set(date, counts);
  };
  for (const release of releases) add(release.date, releaseCategory(editions.get(release.editionId)?.mediaType));
  for (const event of events) add(event.date, eventCategory(event));
  return map;
}

/** 升目に出す件数（番組 → 発売 → Web、0 の分類は出さない）。 */
export function nonZeroCounts(counts: CategoryCounts | undefined): { category: DayCategory; count: number }[] {
  if (!counts) return [];
  return DAY_CATEGORIES.filter((category) => counts[category] > 0).map((category) => ({ category, count: counts[category] }));
}

/** タブに添える件数（すべては合計）。 */
export function filterCounts(counts: CategoryCounts): Record<MediaFilter, number> {
  const total = DAY_CATEGORIES.reduce((sum, category) => sum + counts[category], 0);
  const out = { all: total, anime: 0, book: 0, web: 0 };
  for (const item of MEDIA_FILTERS) {
    if (item.category) out[item.value] = counts[item.category];
  }
  return out;
}
