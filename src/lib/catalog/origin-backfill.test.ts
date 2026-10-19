import { describe, expect, it, vi } from 'vitest';
import { backfillFromCatalog, type CatalogBackfill } from './origin-backfill';
import type { CatalogWorkSummary } from './types';

const summary = (id: string, title: string, origin: CatalogWorkSummary['origin']): CatalogWorkSummary => ({
  id,
  title,
  kinds: ['anime'],
  entryCount: 1,
  popularity: 1,
  origin,
});

function deps(over: Partial<CatalogBackfill> = {}): CatalogBackfill & { [K in keyof CatalogBackfill]: ReturnType<typeof vi.fn> } {
  return {
    search: vi.fn(async (title: string) => [summary(`anilist:${title}`, title, 'novel')]),
    detail: vi.fn(async () => ({ novelCover: 'https://img/ln.jpg', mangaCover: 'https://img/manga.jpg' })),
    setOrigin: vi.fn(async () => {}),
    setCover: vi.fn(async () => {}),
    ...over,
  } as never;
}

describe('backfillFromCatalog', () => {
  it('原作の媒体と、表紙の無い文庫・漫画の Edition を同じ題名のカタログ作品から埋める（アニメ・Web は触らない）', async () => {
    const d = deps();
    const result = await backfillFromCatalog(
      [{ id: 'w1', title: '薬屋のひとりごと' }],
      [
        { id: 'ln', workId: 'w1', mediaType: 'light-novel' },
        { id: 'mg', workId: 'w1', mediaType: 'manga' },
        { id: 'an', workId: 'w1', mediaType: 'anime' },
        { id: 'web', workId: 'w1', mediaType: 'web-novel' },
      ],
      d,
    );
    expect(result).toEqual({ origins: 1, covers: 2 });
    expect(d.setOrigin).toHaveBeenCalledWith('w1', 'novel');
    expect(d.setCover).toHaveBeenCalledWith('ln', 'https://img/ln.jpg');
    expect(d.setCover).toHaveBeenCalledWith('mg', 'https://img/manga.jpg');
    expect(d.setCover).toHaveBeenCalledTimes(2);
  });

  it('表紙のある Edition（手で入れたものも）と、原作が分かっている Work は変えない。足りる時はカタログに問い合わせない', async () => {
    const d = deps();
    const result = await backfillFromCatalog(
      [{ id: 'w1', title: 'アオのハコ', origin: 'manga' }],
      [{ id: 'mg', workId: 'w1', mediaType: 'manga', coverUrl: 'https://img/mine.jpg' }],
      d,
    );
    expect(result).toEqual({ origins: 0, covers: 0 });
    expect(d.search).not.toHaveBeenCalled();
  });

  it('題名が一致しない作品・原作の分からない作品からは取らない。その媒体の表紙が無ければ文字の表紙のまま', async () => {
    const d = deps({
      search: vi.fn(async () => [summary('x', '別の作品', 'manga'), summary('y', 'ブラッククローバー', null)]),
      detail: vi.fn(async () => ({ novelCover: null, mangaCover: 'https://img/manga.jpg' })),
    });
    const result = await backfillFromCatalog(
      [{ id: 'w1', title: 'ブラッククローバー' }],
      [
        { id: 'ln', workId: 'w1', mediaType: 'light-novel' },
        { id: 'mg', workId: 'w1', mediaType: 'manga' },
      ],
      d,
    );
    expect(result).toEqual({ origins: 0, covers: 1 });
    expect(d.detail).toHaveBeenCalledWith('y');
    expect(d.setCover).toHaveBeenCalledWith('mg', 'https://img/manga.jpg');
  });

  it('collector に届かなくても例外を出さず、残りの Work は続ける', async () => {
    const d = deps({
      search: vi.fn(async (title: string) => {
        if (title === 'A') throw new TypeError('Failed to fetch');
        return [summary('b', 'B', 'anime')];
      }),
    });
    const result = await backfillFromCatalog([{ id: 'a', title: 'A' }, { id: 'b', title: 'B' }], [], d);
    expect(result).toEqual({ origins: 1, covers: 0 });
  });
});
