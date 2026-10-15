// Work 詳細の補助（Edition のタブ・最初に開くタブ・作品の媒体の並び・今日の放送）。純関数で vitest 固定。

import type { Edition, MediaType, Release } from '../db/schema';
import { MEDIA_TYPE_LABELS, MEDIA_TYPES, STATUS_LABELS } from './labels';
import { editionDisambiguators, sortLibraryEditions, type SeasonStart } from './library-view';
import { BROADCAST_KINDS, compareReleases, precisionOf } from './release-view';

type TabEdition = Pick<Edition, 'id' | 'workId' | 'mediaType' | 'title' | 'createdAt' | 'status'>;

export interface EditionTab {
  id: string;
  /** 媒体名（'ANIME'） */
  media: string;
  /** 同じ媒体が複数ある時の見分け用の副題（'2026年10月〜'）。1 つだけなら空。 */
  detail: string;
  /** 状態（'進行中'） */
  status: string;
}

/** Work 詳細の Edition の並び（媒体 → 開始日 → 登録順）。Library の棚と同じ順にする。 */
export function orderWorkEditions<T extends TabEdition>(
  editions: readonly T[],
  seasonStarts: ReadonlyMap<string, SeasonStart>,
): T[] {
  return sortLibraryEditions(editions, () => '', seasonStarts);
}

/** タブの見出し。副題は Library と同じ editionDisambiguators で作る（同じ媒体が 2 つ以上の時だけ）。 */
export function editionTabs(
  ordered: readonly TabEdition[],
  workTitle: string,
  seasonStarts: ReadonlyMap<string, SeasonStart>,
): EditionTab[] {
  const details = editionDisambiguators(ordered, () => workTitle, seasonStarts);
  return ordered.map((edition) => ({
    id: edition.id,
    media: MEDIA_TYPE_LABELS[edition.mediaType],
    detail: details.get(edition.id) ?? '',
    status: STATUS_LABELS[edition.status],
  }));
}

/** 最初に開くタブ: URL で指定された Edition → 今日放送・配信がある Edition → 先頭。 */
export function initialEditionId(
  ordered: readonly Pick<Edition, 'id'>[],
  requested: string | undefined,
  onAir: ReadonlySet<string>,
): string | undefined {
  if (requested && ordered.some((e) => e.id === requested)) return requested;
  return ordered.find((e) => onAir.has(e.id))?.id ?? ordered[0]?.id;
}

/** 作品にある媒体（重複なし・WEB → 文庫 → 漫画 → ANIME の順）。ヘッダーの媒体の並びに使う。 */
export function workMediaTypes(editions: readonly Pick<Edition, 'mediaType'>[]): MediaType[] {
  const present = new Set(editions.map((e) => e.mediaType));
  return MEDIA_TYPES.filter((m) => present.has(m));
}

/** Edition 追加フォームの媒体の既定: まだ無い媒体のうち先頭。全部そろっていれば WEB。 */
export function suggestedMediaType(editions: readonly Pick<Edition, 'mediaType'>[]): MediaType {
  const present = new Set(editions.map((e) => e.mediaType));
  return MEDIA_TYPES.find((m) => !present.has(m)) ?? MEDIA_TYPES[0];
}

/** 今日（放送日）の放送・配信のうち最初の時刻（'25:30'）。onAir に入っている Edition だけを見る。 */
export function firstAiringTimeToday(
  releases: readonly Release[],
  onAir: ReadonlySet<string>,
  today: string,
): string | undefined {
  return releases
    .filter(
      (r) =>
        onAir.has(r.editionId) &&
        BROADCAST_KINDS.includes(r.kind) &&
        precisionOf(r) === 'day' &&
        r.date === today &&
        !!r.time,
    )
    .sort(compareReleases)[0]?.time;
}
