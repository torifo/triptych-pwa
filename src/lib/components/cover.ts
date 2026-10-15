// 表紙の出どころと「文字の表紙」に組む文字を決める純関数群。
// 色は表紙の画像だけが持つ（specs/cover-design）。画像が無い・壊れている時は文字の表紙で代える。

import type { Edition, MediaType, Work } from '../db/schema';
import { MEDIA_TYPE_LABELS } from './labels';
import { isSafeHttpUrl } from './release-view';

/** xs 28px（行）/ s 48px（カタログの行）/ m 64px（横並び）/ l 112px（詳細ヘッダー）/ fill 親の幅いっぱい（棚）。 */
export type CoverSize = 'xs' | 's' | 'm' | 'l' | 'fill';

export interface CoverSource {
  url?: string;
  title: string;
  mediaType?: MediaType;
}

export const COVER_WIDTH: Record<Exclude<CoverSize, 'fill'>, number> = { xs: 28, s: 48, m: 64, l: 112 };

/** 代表表紙に選ぶ媒体の順（色の強い・確実に表紙があるものから）。 */
const COVER_MEDIA_ORDER: MediaType[] = ['anime', 'manga', 'light-novel', 'web-novel'];

/** 表紙の URL（http/https のみ）。無ければ undefined。 */
export function coverUrlOf(edition: Pick<Edition, 'coverUrl'> | undefined): string | undefined {
  const url = edition?.coverUrl?.trim();
  return url && isSafeHttpUrl(url) ? url : undefined;
}

/** Edition 1つの表紙。題名は Work 名があればそちら（同じ作品の表紙が同じ名前で並ぶように）。 */
export function editionCover(
  edition: Pick<Edition, 'coverUrl' | 'title' | 'mediaType'>,
  workTitle?: string,
): CoverSource {
  return { url: coverUrlOf(edition), title: workTitle || edition.title, mediaType: edition.mediaType };
}

/** Work の代表表紙: coverEditionId → 表紙のある Edition（アニメ・漫画・文庫・Web の順）→ 文字の表紙。 */
export function workCover(
  work: Pick<Work, 'title' | 'coverEditionId'>,
  editions: readonly Pick<Edition, 'id' | 'coverUrl' | 'mediaType'>[],
): CoverSource {
  const chosen = editions.find((e) => e.id === work.coverEditionId && coverUrlOf(e));
  const withCover =
    chosen ??
    [...editions]
      .filter((e) => coverUrlOf(e))
      .sort((a, b) => COVER_MEDIA_ORDER.indexOf(a.mediaType) - COVER_MEDIA_ORDER.indexOf(b.mediaType))[0];
  const mediaType = withCover?.mediaType ?? editions[0]?.mediaType;
  return { url: coverUrlOf(withCover), title: work.title, mediaType };
}

// 副題の区切り。'Re:ゼロ' のように最初の区切りが早すぎる時は題名全体を使う。
const SUBTITLE_DELIMITER = /[\s~〜～\-‐–—―:：「『【〈《（(［[]/u;
const FALLBACK_MAX_CHARS = 12;
const FALLBACK_MIN_HEAD = 3;

/** 文字の表紙に組む題名（副題を落とし、長ければ 12 字で切る）と媒体名。 */
export function fallbackCoverText(title: string, mediaType?: MediaType): { title: string; media: string } {
  const text = title.trim();
  const head = text.split(SUBTITLE_DELIMITER)[0]?.trim() ?? '';
  const base = [...head].length >= FALLBACK_MIN_HEAD ? head : text;
  const chars = [...base];
  const short = chars.length > FALLBACK_MAX_CHARS ? `${chars.slice(0, FALLBACK_MAX_CHARS - 1).join('')}…` : base;
  return { title: short || '—', media: mediaType ? MEDIA_TYPE_LABELS[mediaType] : '' };
}
