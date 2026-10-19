import { describe, expect, it, vi } from 'vitest';
import { backfillFromCatalog, withoutSeason, type CatalogBackfill } from './origin-backfill';
import type { CatalogWorkSummary } from './types';

const summary = (id: string, title: string, origin: CatalogWorkSummary['origin']): CatalogWorkSummary => ({
  id,
  title,
  kinds: ['anime'],
  entryCount: 1,
  popularity: 1,
  origin,
});

type Mocked = { [K in keyof CatalogBackfill]: ReturnType<typeof vi.fn> };

function deps(over: Partial<CatalogBackfill> = {}): CatalogBackfill & Mocked {
  return {
    search: vi.fn(async (title: string) => [summary(`anilist:${title}`, title, 'novel')]),
    detail: vi.fn(async () => ({ novelCover: 'https://img/ln.jpg', mangaCover: 'https://img/manga.jpg' })),
    setOrigin: vi.fn(async () => {}),
    setCover: vi.fn(async () => true),
    recentlyTried: vi.fn(() => false),
    markTried: vi.fn(async () => {}),
    ...over,
  } as never;
}

describe('withoutSeason', () => {
  it('末尾の期の印（第2期・Season 2・2nd Season）だけ外す', () => {
    expect(withoutSeason('薬屋のひとりごと 第2期')).toBe('薬屋のひとりごと');
    expect(withoutSeason('ゆるキャン△ SEASON３')).toBe('ゆるキャン△');
    expect(withoutSeason('Re:ゼロ 2nd Season')).toBe('Re:ゼロ');
    expect(withoutSeason('3期生の話')).toBe('3期生の話');
  });
});

describe('backfillFromCatalog', () => {
  it('原作の媒体と、表紙の無い文庫・漫画の Edition を同じ題名のカタログ作品から埋める（アニメ・Web は触らない）', async () => {
    const d = deps();
    const result = await backfillFromCatalog(
      [{ id: 'w1', title: '薬屋のひとりごと' }],
      [
        { id: 'ln', workId: 'w1', mediaType: 'light-novel', title: '薬屋のひとりごと' },
        { id: 'mg', workId: 'w1', mediaType: 'manga', title: '薬屋のひとりごと' },
        { id: 'an', workId: 'w1', mediaType: 'anime', title: '薬屋のひとりごと' },
        { id: 'web', workId: 'w1', mediaType: 'web-novel', title: '薬屋のひとりごと' },
      ],
      d,
    );
    expect(result).toEqual({ origins: 1, covers: 2 });
    expect(d.setOrigin).toHaveBeenCalledWith('w1', 'novel');
    expect(d.setCover).toHaveBeenCalledWith('ln', 'https://img/ln.jpg');
    expect(d.setCover).toHaveBeenCalledWith('mg', 'https://img/manga.jpg');
    expect(d.markTried).toHaveBeenCalledWith('w1');
  });

  it('表紙のある Edition・本人が外した表紙・原作が分かっている Work は触らない。足りる時は問い合わせない', async () => {
    const d = deps();
    const result = await backfillFromCatalog(
      [{ id: 'w1', title: 'アオのハコ', origin: 'manga' }],
      [
        { id: 'mg', workId: 'w1', mediaType: 'manga', title: 'アオのハコ', coverUrl: 'https://img/mine.jpg' },
        { id: 'ln', workId: 'w1', mediaType: 'light-novel', title: 'アオのハコ', syncHints: { coverCleared: true } },
      ],
      d,
    );
    expect(result).toEqual({ origins: 0, covers: 0 });
    expect(d.search).not.toHaveBeenCalled();
  });

  it('外伝など題名の違う Edition には本編の表紙を入れない', async () => {
    const d = deps();
    await backfillFromCatalog(
      [{ id: 'w1', title: '薬屋のひとりごと', origin: 'novel' }],
      [{ id: 'side', workId: 'w1', mediaType: 'light-novel', title: '薬屋のひとりごと外伝 小蘭回想録' }],
      d,
    );
    expect(d.setCover).not.toHaveBeenCalled();
  });

  it('期の付いた題名の Work も、期を外した題名のカタログ作品に当てる', async () => {
    const d = deps({ search: vi.fn(async () => [summary('anilist:1', '薬屋のひとりごと', 'novel')]) });
    expect(await backfillFromCatalog([{ id: 'w1', title: '薬屋のひとりごと 第2期' }], [], d)).toEqual({ origins: 1, covers: 0 });
  });

  it('同じ題名の作品が2つ以上あって原作が食い違う時は推測しない', async () => {
    const d = deps({
      search: vi.fn(async () => [summary('a', 'ホワイト', 'manga'), summary('b', 'ホワイト', 'novel')]),
    });
    const result = await backfillFromCatalog(
      [{ id: 'w1', title: 'ホワイト' }],
      [{ id: 'ln', workId: 'w1', mediaType: 'light-novel', title: 'ホワイト' }],
      d,
    );
    expect(result).toEqual({ origins: 0, covers: 0 });
    expect(d.detail).not.toHaveBeenCalled();
    expect(d.markTried).toHaveBeenCalledWith('w1');
  });

  it('最近試して何も無かった Work は飛ばす（毎回の起動で問い合わせない）', async () => {
    const d = deps({ recentlyTried: vi.fn(() => true) });
    await backfillFromCatalog([{ id: 'w1', title: 'なろうの作品' }], [], d);
    expect(d.search).not.toHaveBeenCalled();
  });

  it('自動の表紙を入れられなかった（その間に本人が設定した）分は数えない', async () => {
    const d = deps({ setCover: vi.fn(async () => false) });
    const result = await backfillFromCatalog(
      [{ id: 'w1', title: 'A', origin: 'novel' }],
      [{ id: 'ln', workId: 'w1', mediaType: 'light-novel', title: 'A' }],
      d,
    );
    expect(result.covers).toBe(0);
  });

  it('collector に届かなければそこでやめる（残りの Work に失敗する問い合わせを重ねない）', async () => {
    const d = deps({ search: vi.fn(async () => Promise.reject(new TypeError('Failed to fetch'))) });
    const result = await backfillFromCatalog(
      [
        { id: 'a', title: 'A' },
        { id: 'b', title: 'B' },
      ],
      [],
      d,
    );
    expect(result).toEqual({ origins: 0, covers: 0 });
    expect(d.search).toHaveBeenCalledTimes(1);
    expect(d.markTried).not.toHaveBeenCalled();
  });
});
