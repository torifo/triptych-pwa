// カタログの表紙の選び方（純関数）。一覧は CatalogWorkSummary.cover、詳細は entries[].cover から。
// 表紙は http/https のものだけを使い、無ければ文字の表紙（Cover.svelte）に代える。

import { entryMediaType } from '../catalog/register';
import type { CatalogEntry, CatalogKind } from '../catalog/types';
import type { MediaType } from '../db/schema';
import type { CoverSource } from './cover';
import { isSafeHttpUrl } from './release-view';

/** 詳細ヘッダーの代表表紙に選ぶ面の順（アニメ・漫画・原作小説・Web 小説・紙の本）。 */
const HERO_KIND_ORDER: readonly CatalogKind[] = ['anime', 'manga', 'novel', 'web-novel', 'book'];

/** 表紙の URL（http/https のみ）。無ければ undefined。 */
export function catalogCoverUrl(cover: string | null | undefined): string | undefined {
  const url = cover?.trim();
  return url && isSafeHttpUrl(url) ? url : undefined;
}

/** 一覧の作品の「文字の表紙」に出す媒体。面の並びで最初に当たるもの（紙の本だけなら不明のまま）。 */
export function summaryMediaType(kinds: readonly CatalogKind[]): MediaType | undefined {
  if (kinds.includes('anime')) return 'anime';
  if (kinds.includes('manga')) return 'manga';
  if (kinds.includes('novel')) return 'light-novel';
  if (kinds.includes('web-novel')) return 'web-novel';
  return undefined;
}

/** 詳細の行1つ分の表紙。 */
export function entryCover(entry: Pick<CatalogEntry, 'cover' | 'title' | 'kind' | 'extra'>): CoverSource {
  return { url: catalogCoverUrl(entry.cover), title: entry.title, mediaType: entryMediaType(entry) };
}

function popularityOf(entry: Pick<CatalogEntry, 'popularity'>): number {
  return entry.popularity ?? -1;
}

/**
 * 詳細ヘッダーの代表表紙。AniList の行の表紙を優先し（アニメ・漫画・原作小説の順、同じ面なら人気の高い方）、
 * 無ければ表紙のある行のどれか。どれにも無ければ文字の表紙（媒体は最初の行のもの）。
 */
export function heroCover(entries: readonly CatalogEntry[], title: string): CoverSource {
  const ranked = entries
    .filter((e) => catalogCoverUrl(e.cover))
    .map((entry, index) => ({ entry, index }))
    .sort((a, b) => {
      const anilist = Number(b.entry.source === 'anilist') - Number(a.entry.source === 'anilist');
      if (anilist !== 0) return anilist;
      const kind = HERO_KIND_ORDER.indexOf(a.entry.kind) - HERO_KIND_ORDER.indexOf(b.entry.kind);
      if (kind !== 0) return kind;
      return popularityOf(b.entry) - popularityOf(a.entry) || a.index - b.index;
    });
  const best = ranked[0]?.entry;
  if (best) return { url: catalogCoverUrl(best.cover), title, mediaType: entryMediaType(best) };

  const first = [...entries].sort((a, b) => HERO_KIND_ORDER.indexOf(a.kind) - HERO_KIND_ORDER.indexOf(b.kind))[0];
  return { title, mediaType: first ? entryMediaType(first) : undefined };
}
