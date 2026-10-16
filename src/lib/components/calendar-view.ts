// カレンダーの月の升目（ON AIR の印・極小の表紙・選んだ日）。純関数で vitest 固定。

import type { Release } from '../db/schema';

const ON_AIR_KINDS = new Set<Release['kind']>(['episode', 'stream-start']);

/** 升目の ON AIR の印（今日 'today' / 先の日 'upcoming'）。過ぎた日・巻だけの日は付けない。 */
export function dayOnAirMark(
  cellReleases: readonly Pick<Release, 'kind'>[],
  date: string,
  today: string,
): 'today' | 'upcoming' | null {
  if (date < today || !cellReleases.some((r) => ON_AIR_KINDS.has(r.kind))) return null;
  return date === today ? 'today' : 'upcoming';
}

/** 升目に並べる表紙（同じ Edition は1つ）と、出しきれない Edition の数。 */
export function cellThumbs<T extends Pick<Release, 'editionId'>>(
  cellReleases: readonly T[],
  max: number,
): { shown: T[]; rest: number } {
  const unique: T[] = [];
  const seen = new Set<string>();
  for (const release of cellReleases) {
    if (seen.has(release.editionId)) continue;
    seen.add(release.editionId);
    unique.push(release);
  }
  return { shown: unique.slice(0, max), rest: Math.max(0, unique.length - max) };
}

/**
 * 升目の表紙: Library の Edition を先に置き、空いた枠をカタログの出来事（同じ作品は1つ・人気順）で埋める。
 * rest は出しきれない Library の Edition とカタログの作品の合計。
 */
export function cellCovers<
  R extends Pick<Release, 'editionId'>,
  E extends { clusterId: string; popularity?: number | null },
>(
  cellReleases: readonly R[],
  catalogEvents: readonly E[],
  max: number,
): { library: R[]; catalog: E[]; rest: number } {
  const own = cellThumbs(cellReleases, max);
  const unique: E[] = [];
  const seen = new Set<string>();
  for (const event of catalogEvents) {
    if (seen.has(event.clusterId)) continue;
    seen.add(event.clusterId);
    unique.push(event);
  }
  // sort は安定なので、人気が同じ（または無い）もの同士は入力の順のまま
  const byPopularity = [...unique].sort((a, b) => (b.popularity ?? 0) - (a.popularity ?? 0));
  const catalog = byPopularity.slice(0, max - own.shown.length);
  return { library: own.shown, catalog, rest: own.rest + unique.length - catalog.length };
}

/** 一覧に出す日: 選んだ日がこの月ならその日、無ければ今日（この月の時だけ）。 */
export function calendarDay(
  selected: string | null,
  today: string,
  year: number,
  month: number,
): string | null {
  const prefix = `${String(year).padStart(4, '0')}-${String(month).padStart(2, '0')}-`;
  if (selected?.startsWith(prefix)) return selected;
  return today.startsWith(prefix) ? today : null;
}

/** 升目の読み上げ（'10月15日 3件 放送・配信あり カタログ 12件'）。 */
export function dayCellLabel(
  date: string,
  count: number,
  mark: 'today' | 'upcoming' | null,
  catalogCount = 0,
): string {
  const label = `${Number(date.slice(5, 7))}月${Number(date.slice(8, 10))}日`;
  const parts = [label];
  if (count > 0) parts.push(`${count}件`);
  if (mark) parts.push('放送・配信あり');
  if (catalogCount > 0) parts.push(`カタログ ${catalogCount}件`);
  return parts.join(' ');
}
