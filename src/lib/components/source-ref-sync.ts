// ソース（sourceRef）を足した後に、同期で実際にどう使われるかの案内文。判定は sync/engine.ts と sync/detect.ts に合わせる。

import type { Edition, Source } from '../db/schema';
import { STATUS_LABELS } from './labels';
import { isTrackedEdition } from './release-view';

export type SourceRefUse =
  | 'sync' // Release の同期に使う
  | 'detect' // アニメ化・続編の検知にだけ使う
  | 'ndl-series' // NDL の系列指定として、次の同期で発売日の取得に使う
  | 'ndl-series-fixed' // 系列がすでに固定済みで、同期は変わらない
  | 'unused'; // 同期にも検知にも使わない（参照用）

type SourceRefEdition = Pick<Edition, 'mediaType' | 'status' | 'syncHints'>;

const PAPER_BOOKS: readonly Edition['mediaType'][] = ['light-novel', 'manga'];

/** この Edition にその種類のソースを足した時、同期・検知でどう使われるか。 */
export function sourceRefUse(edition: SourceRefEdition, source: Source): SourceRefUse {
  switch (edition.mediaType) {
    case 'anime':
      if (source === 'syoboi') return edition.syncHints?.syoboiTid ? 'unused' : 'sync';
      return source === 'anilist' ? 'sync' : 'unused';
    case 'web-novel':
      if (source === 'narou' || source === 'kakuyomu') return 'sync';
      return source === 'anilist' ? 'detect' : 'unused';
    case 'light-novel':
    case 'manga':
      if (source === 'ndl') return edition.syncHints?.ndlLine === undefined ? 'ndl-series' : 'ndl-series-fixed';
      return source === 'anilist' ? 'detect' : 'unused';
  }
}

/** 追加直後に出す案内。openBD の ISBN などは同期に使われない、と正直に書く。 */
export function sourceRefSyncNote(edition: SourceRefEdition, source: Source): string {
  const use = sourceRefUse(edition, source);
  if (use === 'unused') {
    return PAPER_BOOKS.includes(edition.mediaType)
      ? '文庫・漫画の発売日は作品名で NDL を検索して取得するため、このソースは同期に使われません（参照用）'
      : 'このソースは同期には使われません（参照用）';
  }
  if (use === 'ndl-series-fixed') return '発売日を取得する系列はすでに決まっているため、同期は変わりません';
  if (!isTrackedEdition(edition)) {
    return `ステータスが「${STATUS_LABELS[edition.status]}」の間は同期の対象外です`;
  }
  if (use === 'detect') return 'アニメ化・続編の検知に使われます（発売日の同期には使われません）';
  if (use === 'ndl-series') return '次回の同期から、この系列で発売日を取得します';
  return '次回の同期から反映されます';
}
