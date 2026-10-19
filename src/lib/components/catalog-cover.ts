// カタログの表紙の選び方（純関数）。一覧は CatalogWorkSummary.cover、詳細は cover（原作の表紙）か entries[].cover から。
// 表紙は原作の絵だけ。http/https のものだけを使い、無ければ文字の表紙（Cover.svelte）に代える。

import { compareFirstSeries } from '../catalog/origin';
import { entryMediaType, ndlKind } from '../catalog/register';
import type { CatalogEntry, CatalogKind, CatalogOrigin, CatalogWorkDetail, OriginCovers } from '../catalog/types';
import type { MediaType } from '../db/schema';
import type { CoverSource } from './cover';
import { isSafeHttpUrl } from './release-view';

type Face = 'novel' | 'manga' | 'anime';

/** 原作の媒体ごとの、代表表紙に選ぶ面の順。原作が分からなければ小説の順（アニメの絵を先にしない）。 */
const HERO_FACE_ORDER: Record<CatalogOrigin, readonly Face[]> = {
  novel: ['novel', 'manga', 'anime'],
  manga: ['manga', 'novel', 'anime'],
  anime: ['anime', 'manga', 'novel'],
};

/** 原作が分からない時に文字の表紙の媒体を決める、原作らしい面の順。 */
const LIKELY_ORIGIN_KINDS: readonly [CatalogKind, MediaType][] = [
  ['novel', 'light-novel'],
  ['manga', 'manga'],
  ['web-novel', 'web-novel'],
  ['anime', 'anime'],
];

/** 表紙の URL（http/https のみ）。無ければ undefined。 */
export function catalogCoverUrl(cover: string | null | undefined): string | undefined {
  const url = cover?.trim();
  return url && isSafeHttpUrl(url) ? url : undefined;
}

/** 作品の「文字の表紙」に出す媒体。原作の媒体があればそれ、無ければ原作らしい面（紙の本だけなら不明のまま）。 */
export function summaryMediaType(kinds: readonly CatalogKind[], origin?: CatalogOrigin | null): MediaType | undefined {
  if (origin === 'anime' || origin === 'manga') return origin;
  if (origin === 'novel') return !kinds.includes('novel') && kinds.includes('web-novel') ? 'web-novel' : 'light-novel';
  return LIKELY_ORIGIN_KINDS.find(([kind]) => kinds.includes(kind))?.[1];
}

/** 詳細の行1つ分の表紙。表紙の無い本は種別に合う原作の表紙、Web 小説は原作小説の表紙（ほかの行は借りない）。 */
export function entryCover(
  entry: Pick<CatalogEntry, 'cover' | 'title' | 'kind' | 'extra'>,
  covers: OriginCovers = {},
): CoverSource {
  const borrowed =
    entry.kind === 'book'
      ? ndlKind(entry) === 'manga'
        ? covers.mangaCover
        : covers.novelCover
      : entry.kind === 'web-novel'
        ? covers.novelCover
        : undefined;
  return {
    url: catalogCoverUrl(entry.cover) ?? catalogCoverUrl(borrowed),
    title: entry.title,
    mediaType: entryMediaType(entry),
  };
}

/** 代表表紙の面。本は種別（小説・漫画）、Web 小説は表紙を持たないので面にしない。 */
function heroFace(entry: Pick<CatalogEntry, 'kind' | 'extra'>): Face | undefined {
  if (entry.kind === 'book') return ndlKind(entry);
  return entry.kind === 'web-novel' ? undefined : entry.kind;
}

/** 詳細ヘッダーの代表表紙: 原作の面から、同じ面なら AniList → ノベライズでない → 最初のシリーズ。無ければ文字の表紙。 */
export function heroCover(entries: readonly CatalogEntry[], title: string, origin?: CatalogOrigin | null): CoverSource {
  const order = HERO_FACE_ORDER[origin ?? 'novel'] ?? HERO_FACE_ORDER.novel;
  const ranked = entries
    .map((entry, index) => ({ entry, index, face: heroFace(entry) }))
    .filter((c): c is { entry: CatalogEntry; index: number; face: Face } => !!c.face && !!catalogCoverUrl(c.entry.cover))
    .sort((a, b) => {
      const face = order.indexOf(a.face) - order.indexOf(b.face);
      if (face !== 0) return face;
      const anilist = Number(b.entry.source === 'anilist') - Number(a.entry.source === 'anilist');
      if (anilist !== 0) return anilist;
      return compareFirstSeries(a.entry, b.entry, a.face === 'novel') || a.index - b.index;
    });
  const best = ranked[0]?.entry;
  if (best) return { url: catalogCoverUrl(best.cover), title, mediaType: entryMediaType(best) };

  const kinds = [...new Set(entries.map((e) => e.kind))];
  return { title, mediaType: summaryMediaType(kinds, origin) ?? (entries[0] ? entryMediaType(entries[0]) : undefined) };
}

/** 詳細ヘッダーの表紙: collector の原作の表紙（cover）→ 行から原作の順で選んだ表紙。 */
export function detailHeroCover(
  detail: Pick<CatalogWorkDetail, 'title' | 'kinds' | 'entries' | 'cover' | 'origin'>,
): CoverSource {
  const url = catalogCoverUrl(detail.cover);
  if (url) return { url, title: detail.title, mediaType: summaryMediaType(detail.kinds, detail.origin) };
  return heroCover(detail.entries, detail.title, detail.origin);
}
