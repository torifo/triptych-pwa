// 原作の媒体と、表紙の無い文庫・漫画の Edition を、カタログの同じ題名の作品で埋める（届かなければ次の起動で）。

import type { Edition, Work, WorkOrigin } from '../db/schema';
import { listEditions, listWorks, updateEdition, updateEditionSyncHints, updateWork } from '../db/store';
import { normalizeTitle } from '../normalize';
import { fetchCatalogWork, fetchCatalogWorks } from './api';
import type { CatalogWorkDetail, CatalogWorkSummary } from './types';

export interface CatalogBackfill {
  search(title: string): Promise<readonly CatalogWorkSummary[]>;
  detail(id: string): Promise<Pick<CatalogWorkDetail, 'novelCover' | 'mangaCover'>>;
  setOrigin(workId: string, origin: WorkOrigin): Promise<unknown>;
  /** 自動の表紙として入れる（手で入れた表紙と見分けられるように）。 */
  setCover(editionId: string, url: string): Promise<unknown>;
}

type BackfillEdition = Pick<Edition, 'id' | 'workId' | 'mediaType' | 'coverUrl'>;

/** 表紙を足せる Edition: 文庫・漫画で表紙が無いもの（アニメは同期が期ごとの表紙を入れ、Web 小説は文庫の表紙を借りる）。 */
function bareBooks(editions: readonly BackfillEdition[], workId: string): BackfillEdition[] {
  return editions.filter(
    (e) => e.workId === workId && (e.mediaType === 'light-novel' || e.mediaType === 'manga') && !e.coverUrl?.trim(),
  );
}

export async function backfillFromCatalog(
  works: readonly Pick<Work, 'id' | 'title' | 'origin'>[],
  editions: readonly BackfillEdition[],
  catalog: CatalogBackfill,
): Promise<{ origins: number; covers: number }> {
  const result = { origins: 0, covers: 0 };
  for (const work of works) {
    const bare = bareBooks(editions, work.id);
    if (work.origin && bare.length === 0) continue;
    try {
      const key = normalizeTitle(work.title);
      const match = (await catalog.search(work.title)).find((item) => normalizeTitle(item.title) === key);
      if (!match) continue;
      if (!work.origin && match.origin) {
        await catalog.setOrigin(work.id, match.origin);
        result.origins++;
      }
      if (bare.length === 0) continue;
      const covers = await catalog.detail(match.id);
      for (const edition of bare) {
        const url = edition.mediaType === 'light-novel' ? covers.novelCover : covers.mangaCover;
        if (!url) continue;
        await catalog.setCover(edition.id, url);
        result.covers++;
      }
    } catch {
      // 届かない・壊れた応答: この Work は次の機会に
    }
  }
  return result;
}

/** 起動時・検索から足した直後に使う: Library の Work（workIds があればそれだけ）をカタログで埋める。 */
export async function fillWorksFromCatalog(
  workIds?: readonly string[],
): Promise<{ origins: number; covers: number }> {
  const [works, editions] = await Promise.all([listWorks(), listEditions()]);
  const targets = workIds ? works.filter((w) => workIds.includes(w.id)) : works;
  return backfillFromCatalog(targets, editions, {
    search: async (title) => (await fetchCatalogWorks({ q: title, per: 5 })).items,
    detail: (id) => fetchCatalogWork(id),
    setOrigin: (id, origin) => updateWork(id, { origin }),
    setCover: async (id, url) => {
      await updateEdition(id, { coverUrl: url });
      await updateEditionSyncHints(id, { coverFrom: 'catalog' });
    },
  });
}
