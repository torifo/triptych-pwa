// 表紙の出どころと「文字の表紙」に組む文字を決める純関数群。
// 色は表紙の画像だけが持つ（specs/cover-design）。画像が無い・壊れている時は文字の表紙で代える。

import type { Edition, MediaType, Work, WorkOrigin } from '../db/schema';
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

// AniList の表紙は small 100px / medium 230px / large 460px の3版。表示の大きさ（2倍密度）に足りる版を選ぶ。
const ANILIST_COVER_RE = /^(https:\/\/s4\.anilist\.co\/file\/anilistcdn\/media\/(?:anime|manga)\/cover\/)(small|medium|large)(\/.+)$/;
const ANILIST_VARIANT: Record<CoverSize, 'small' | 'medium'> = { xs: 'small', s: 'small', m: 'medium', l: 'medium', fill: 'medium' };

/** 表示の大きさに合う解像度の URL。AniList 以外はそのまま。 */
export function sizedCoverUrl(url: string, size: CoverSize): string {
  const match = ANILIST_COVER_RE.exec(url);
  if (!match) return url;
  return `${match[1]}${ANILIST_VARIANT[size]}${match[3]}`;
}

/** 幅いっぱいの表紙の srcset（AniList の medium・large）。幅の決まった表紙や AniList 以外は undefined。 */
export function coverSrcset(url: string, size: CoverSize): string | undefined {
  if (size !== 'fill') return undefined;
  const match = ANILIST_COVER_RE.exec(url);
  if (!match) return undefined;
  return `${match[1]}medium${match[3]} 230w, ${match[1]}large${match[3]} 460w`;
}

/** 原作の媒体ごとの代表表紙の順（アニメの絵は原作の表紙が無い時だけ。Web 小説は文字の表紙の媒体名にだけ使う）。 */
const ORIGIN_MEDIA_ORDER: Record<WorkOrigin, MediaType[]> = {
  novel: ['light-novel', 'web-novel', 'manga', 'anime'],
  manga: ['manga', 'light-novel', 'web-novel', 'anime'],
  anime: ['anime', 'manga', 'light-novel', 'web-novel'],
};

/** Edition ごとの始まり（アニメは放送開始日。library-view の seasonStartByEdition）。 */
export type EditionStarts = ReadonlyMap<string, { date: string }>;

/** 表紙を選ぶのに見る Edition の欄（createdAt は同じ媒体の並びに使う）。 */
export type CoverEdition = Pick<Edition, 'id' | 'coverUrl' | 'mediaType'> & Partial<Pick<Edition, 'createdAt'>>;

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

/** 同じ媒体の中の並び: 放送開始の早い期（分からない期は後ろ）→ 登録の早いもの。第1期・最初のシリーズを先にする。 */
function compareEarliest(a: CoverEdition, b: CoverEdition, starts: EditionStarts | undefined): number {
  const sa = starts?.get(a.id)?.date;
  const sb = starts?.get(b.id)?.date;
  if (sa !== sb) {
    if (!sa) return 1;
    if (!sb) return -1;
    return sa < sb ? -1 : 1;
  }
  const ca = a.createdAt ?? '';
  const cb = b.createdAt ?? '';
  if (ca !== cb) return ca < cb ? -1 : 1;
  return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
}

/** その媒体で表紙のある最初の Edition。 */
function earliestCovered<E extends CoverEdition>(
  editions: readonly E[],
  mediaType: MediaType,
  starts: EditionStarts | undefined,
): E | undefined {
  return editions
    .filter((e) => e.mediaType === mediaType && coverUrlOf(e))
    .sort((a, b) => compareEarliest(a, b, starts))[0];
}

/** Work の代表表紙: coverEditionId → 原作の媒体の表紙（同じ媒体なら最初の期）→ 文字の表紙（原作の順で最初の媒体）。原作が不明なら小説の順。 */
export function workCover(
  work: Pick<Work, 'title' | 'coverEditionId' | 'origin'>,
  editions: readonly CoverEdition[],
  starts?: EditionStarts,
): CoverSource {
  const order = ORIGIN_MEDIA_ORDER[work.origin ?? 'novel'] ?? ORIGIN_MEDIA_ORDER.novel;
  const chosen =
    editions.find((e) => e.id === work.coverEditionId && coverUrlOf(e)) ??
    order
      .filter((m) => m !== 'web-novel')
      .map((m) => earliestCovered(editions, m, starts))
      .find((e) => e !== undefined);
  const mediaType = chosen?.mediaType ?? order.find((m) => editions.some((e) => e.mediaType === m));
  return { url: coverUrlOf(chosen), title: work.title, mediaType };
}

/** Edition に出す表紙。自分の表紙だけで他の媒体の絵は借りない（表紙の無い Web 小説だけ同じ作品の文庫の表紙）。 */
export function editionDisplayCover(
  edition: Pick<Edition, 'coverUrl' | 'title' | 'mediaType'>,
  work: Pick<Work, 'title'> | undefined,
  workEditions: readonly CoverEdition[],
  starts?: EditionStarts,
): CoverSource {
  const own = editionCover(edition, work?.title);
  if (own.url || edition.mediaType !== 'web-novel') return own;
  return { ...own, url: coverUrlOf(earliestCovered(workEditions, 'light-novel', starts)) };
}

/** Work 詳細のヘッダー: 開いている Edition に自分の表紙があればそれ（期ごとの絵・文庫の表紙）、無ければ代表表紙。 */
export function workHeroCover(
  work: Pick<Work, 'title' | 'coverEditionId' | 'origin'>,
  editions: readonly CoverEdition[],
  selected: Pick<Edition, 'coverUrl' | 'mediaType'> | undefined,
  starts?: EditionStarts,
): CoverSource {
  const own = coverUrlOf(selected);
  if (selected && own) return { url: own, title: work.title, mediaType: selected.mediaType };
  return workCover(work, editions, starts);
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
