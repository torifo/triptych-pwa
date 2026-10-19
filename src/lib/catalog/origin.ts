// カタログ作品の原作の媒体と、原作の表紙を選ぶ手がかり（ノベライズやアニメの絵を原作の代わりにしない）。

import type { CatalogEntry, CatalogOrigin, CatalogWorkDetail, CatalogWorkSummary } from './types';

const ORIGINS: readonly CatalogOrigin[] = ['novel', 'manga', 'anime'];

/** AniList の source がこれらなら、その小説はノベライズ（原作ではない）。backend の NOVELIZATION_SOURCES と同じ。 */
const NOVELIZATION_SOURCES = new Set([
  'MANGA',
  'ANIME',
  'VISUAL_NOVEL',
  'VIDEO_GAME',
  'GAME',
  'LIVE_ACTION',
  'COMIC',
  'MULTIMEDIA_PROJECT',
  'PICTURE_BOOK',
]);

/** 原作の媒体。無い・知らない値は undefined。 */
export function catalogOrigin(value: unknown): CatalogOrigin | undefined {
  return ORIGINS.find((o) => o === value);
}

function coverText(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null;
}

/** 一覧の作品の origin を整える（古い collector の応答は null）。 */
export function normalizeWorkSummary(summary: CatalogWorkSummary): CatalogWorkSummary {
  return { ...summary, origin: catalogOrigin(summary.origin) ?? null };
}

/** 詳細の原作まわりの欄（origin・表紙3種）を整える。無い・壊れた値は null。 */
export function normalizeWorkDetail(detail: CatalogWorkDetail): CatalogWorkDetail {
  return {
    ...detail,
    origin: catalogOrigin(detail.origin) ?? null,
    cover: coverText(detail.cover),
    novelCover: coverText(detail.novelCover),
    mangaCover: coverText(detail.mangaCover),
  };
}

/** 漫画・アニメ・ゲームなどから作った小説か。source が分からなければ原作扱い。 */
export function isNovelization(entry: Pick<CatalogEntry, 'sourceMaterial'>): boolean {
  return !!entry.sourceMaterial && NOVELIZATION_SOURCES.has(entry.sourceMaterial);
}

/** 最初のシリーズ: 開始の早いもの（日付の無いものは後ろ）→ 人気の高いもの → 先に出てきたもの。preferOriginal ならノベライズを後ろへ。 */
export function firstSeries<T extends Pick<CatalogEntry, 'startDate' | 'popularity' | 'sourceMaterial'>>(
  entries: readonly T[],
  options: { preferOriginal?: boolean } = {},
): T | undefined {
  let best: T | undefined;
  for (const entry of entries) {
    if (!best || compareFirstSeries(entry, best, options.preferOriginal) < 0) best = entry;
  }
  return best;
}

/** firstSeries の並び。負なら a が先。 */
export function compareFirstSeries(
  a: Pick<CatalogEntry, 'startDate' | 'popularity' | 'sourceMaterial'>,
  b: Pick<CatalogEntry, 'startDate' | 'popularity' | 'sourceMaterial'>,
  preferOriginal = false,
): number {
  if (preferOriginal) {
    const byOriginal = Number(isNovelization(a)) - Number(isNovelization(b));
    if (byOriginal !== 0) return byOriginal;
  }
  const sa = a.startDate || '9999-12-31';
  const sb = b.startDate || '9999-12-31';
  if (sa !== sb) return sa < sb ? -1 : 1;
  return (b.popularity ?? -1) - (a.popularity ?? -1);
}
