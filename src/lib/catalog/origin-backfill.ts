// 原作の媒体と、表紙の無い文庫・漫画の Edition を、カタログの同じ題名の作品で埋める（届かなければ次の起動で）。

import { db, type Edition, type Work, type WorkOrigin } from '../db/schema';
import { fillAutoCover, listEditions, listWorks, updateWork } from '../db/store';
import { normalizeTitle } from '../normalize';
import { fetchCatalogWork, fetchCatalogWorks } from './api';
import type { CatalogWorkDetail, CatalogWorkSummary } from './types';

export interface CatalogBackfill {
  search(title: string): Promise<readonly CatalogWorkSummary[]>;
  detail(id: string): Promise<Pick<CatalogWorkDetail, 'novelCover' | 'mangaCover'>>;
  setOrigin(workId: string, origin: WorkOrigin): Promise<unknown>;
  /** 自動の表紙として入れる。その間に本人が表紙を入れた・外した時は入れずに false。 */
  setCover(editionId: string, url: string): Promise<boolean>;
  /** 最近試した Work（毎回の起動で同じ問い合わせを重ねない）。 */
  recentlyTried(workId: string): boolean;
  markTried(workId: string): Promise<unknown>;
}

type BackfillEdition = Pick<Edition, 'id' | 'workId' | 'mediaType' | 'title' | 'coverUrl' | 'syncHints'>;

// カタログの作品名は期を外してあるので、こちらも外して比べる（backend の _SEASON_LABELS と同じ印）
const SEASON_MARKS = [
  /\s*第[0-9０-９一二三四五六七八九十]+期$/u,
  /\s*(?:season|シーズン)\s*[0-9０-９]+$/iu,
  /\s*[0-9]+(?:st|nd|rd|th)\s*season$/iu,
];

/** 題名の末尾の期の印（第2期・Season 2・2nd Season）を外す。 */
export function withoutSeason(title: string): string {
  let text = title.trim();
  for (const mark of SEASON_MARKS) text = text.replace(mark, '');
  return text.trim() || title.trim();
}

const keyOf = (title: string) => normalizeTitle(withoutSeason(title));

/** 表紙を足せる Edition: 作品と同じ題名の文庫・漫画で、表紙が無く、本人が外してもいないもの（外伝に本編の絵を入れない）。 */
function bareBooks(editions: readonly BackfillEdition[], workId: string, key: string): BackfillEdition[] {
  return editions.filter(
    (e) =>
      e.workId === workId &&
      (e.mediaType === 'light-novel' || e.mediaType === 'manga') &&
      !e.coverUrl?.trim() &&
      !e.syncHints?.coverCleared &&
      keyOf(e.title) === key,
  );
}

export async function backfillFromCatalog(
  works: readonly Pick<Work, 'id' | 'title' | 'origin'>[],
  editions: readonly BackfillEdition[],
  catalog: CatalogBackfill,
): Promise<{ origins: number; covers: number }> {
  const result = { origins: 0, covers: 0 };
  for (const work of works) {
    const key = keyOf(work.title);
    const bare = bareBooks(editions, work.id, key);
    if ((work.origin && bare.length === 0) || catalog.recentlyTried(work.id)) continue;

    let items: readonly CatalogWorkSummary[];
    try {
      items = await catalog.search(withoutSeason(work.title));
    } catch {
      break; // collector に届かない・カタログが無い: 残りの Work にも同じ失敗を重ねない
    }
    const matches = items.filter((item) => keyOf(item.title) === key);
    const origins = new Set(matches.map((m) => m.origin).filter((o): o is WorkOrigin => !!o));
    // 同じ題名の作品の原作が食い違う時は推測しない（別の作品の表紙を入れない）
    if (origins.size === 1) {
      const [origin] = origins;
      if (!work.origin) {
        await catalog.setOrigin(work.id, origin);
        result.origins++;
      }
      if (bare.length > 0) {
        try {
          const covers = await catalog.detail(matches[0].id);
          for (const edition of bare) {
            const url = edition.mediaType === 'light-novel' ? covers.novelCover : covers.mangaCover;
            if (url && (await catalog.setCover(edition.id, url))) result.covers++;
          }
        } catch {
          // 詳細が読めない: この Work は次の機会に
        }
      }
    }
    await catalog.markTried(work.id);
  }
  return result;
}

const TRIED_META_KEY = 'catalogFill';
const RETRY_MS = 7 * 24 * 60 * 60 * 1000; // 週1のカタログ更新で当たるようになるかもしれないので、1週間で試し直す

/** 起動時・検索から足した直後に使う: Library の Work（workIds があればそれだけ・最近試したかを問わず）をカタログで埋める。 */
export async function fillWorksFromCatalog(
  workIds?: readonly string[],
): Promise<{ origins: number; covers: number }> {
  const [works, editions, triedEntry] = await Promise.all([listWorks(), listEditions(), db.meta.get(TRIED_META_KEY)]);
  const tried: Record<string, string> = { ...((triedEntry?.value as Record<string, string> | undefined) ?? {}) };
  const forced = new Set(workIds ?? []);
  const targets = workIds ? works.filter((w) => forced.has(w.id)) : works;
  return backfillFromCatalog(targets, editions, {
    search: async (title) => (await fetchCatalogWorks({ q: title, per: 20 })).items,
    detail: (id) => fetchCatalogWork(id),
    setOrigin: (id, origin) => updateWork(id, { origin }),
    setCover: (id, url) => fillAutoCover(id, url, 'catalog'),
    recentlyTried: (id) => !forced.has(id) && !!tried[id] && Date.now() - Date.parse(tried[id]) < RETRY_MS,
    markTried: async (id) => {
      tried[id] = new Date().toISOString();
      await db.meta.put({ key: TRIED_META_KEY, value: tried });
    },
  });
}
