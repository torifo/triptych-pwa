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
