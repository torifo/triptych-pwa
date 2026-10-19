import { describe, expect, it } from 'vitest';
import { makeEntry } from './fixtures.test-util';
import { catalogOrigin, firstSeries, isNovelization, normalizeWorkDetail, normalizeWorkSummary } from './origin';
import type { CatalogWorkDetail, CatalogWorkSummary } from './types';

describe('catalogOrigin', () => {
  it('原作の媒体（novel / manga / anime）だけを通す', () => {
    expect(catalogOrigin('novel')).toBe('novel');
    expect(catalogOrigin('manga')).toBe('manga');
    expect(catalogOrigin('anime')).toBe('anime');
  });

  it('無い・知らない値は undefined（古い collector の応答）', () => {
    expect(catalogOrigin(undefined)).toBeUndefined();
    expect(catalogOrigin(null)).toBeUndefined();
    expect(catalogOrigin('')).toBeUndefined();
    expect(catalogOrigin('web-novel')).toBeUndefined();
    expect(catalogOrigin(1)).toBeUndefined();
  });
});

describe('normalizeWorkSummary / normalizeWorkDetail', () => {
  const summary: CatalogWorkSummary = { id: 'c1', title: '薬屋のひとりごと', kinds: ['anime'], entryCount: 1, popularity: 0 };
  const detail: CatalogWorkDetail = { id: 'c1', title: '薬屋のひとりごと', kinds: [], entries: [], candidates: [] };

  it('新しい欄が無い応答は null で埋める', () => {
    expect(normalizeWorkSummary(summary).origin).toBeNull();
    expect(normalizeWorkDetail(detail)).toMatchObject({ origin: null, cover: null, novelCover: null, mangaCover: null });
  });

  it('知らない origin と文字列でない表紙は null、表紙の前後の空白は落とす', () => {
    const raw = { ...detail, origin: 'game', cover: 42, novelCover: ' https://x/novel.jpg ', mangaCover: '' } as unknown as CatalogWorkDetail;
    expect(normalizeWorkDetail(raw)).toMatchObject({ origin: null, cover: null, novelCover: 'https://x/novel.jpg', mangaCover: null });
    expect(normalizeWorkSummary({ ...summary, origin: 'novel' }).origin).toBe('novel');
  });

  it('ほかの欄はそのまま', () => {
    expect(normalizeWorkSummary({ ...summary, cover: 'https://x/a.jpg' }).cover).toBe('https://x/a.jpg');
    expect(normalizeWorkDetail({ ...detail, origin: 'manga' })).toMatchObject({ id: 'c1', title: '薬屋のひとりごと', origin: 'manga' });
  });
});

describe('isNovelization', () => {
  it('漫画・アニメ・ゲームなどから作った小説はノベライズ', () => {
    for (const source of ['MANGA', 'ANIME', 'VISUAL_NOVEL', 'VIDEO_GAME', 'GAME', 'LIVE_ACTION', 'COMIC', 'MULTIMEDIA_PROJECT', 'PICTURE_BOOK']) {
      expect(isNovelization({ sourceMaterial: source })).toBe(true);
    }
  });

  it('原作・Web 小説から・不明はノベライズでない', () => {
    expect(isNovelization({ sourceMaterial: 'ORIGINAL' })).toBe(false);
    expect(isNovelization({ sourceMaterial: 'WEB_NOVEL' })).toBe(false);
    expect(isNovelization({ sourceMaterial: null })).toBe(false);
    expect(isNovelization({})).toBe(false);
  });
});

describe('firstSeries', () => {
  const entry = (id: string, over = {}) => makeEntry({ source: 'anilist', externalId: id, kind: 'novel', ...over });

  it('開始の早いもの（日付の無いものは後ろ）、同じなら人気の高いもの、それも同じなら先に出てきたもの', () => {
    const late = entry('1', { startDate: '2015-01-01', popularity: 900 });
    const early = entry('2', { startDate: '2012-05-01', popularity: 10 });
    const undated = entry('3', { popularity: 5000 });
    expect(firstSeries([late, undated, early])).toBe(early);
    expect(firstSeries([undated, entry('4', { popularity: 9000 })])?.externalId).toBe('4');
    expect(firstSeries([entry('5'), entry('6')])?.externalId).toBe('5');
    expect(firstSeries([])).toBeUndefined();
  });

  it('preferOriginal ならノベライズを後ろに回す', () => {
    const novelization = entry('1', { startDate: '2010-01-01', sourceMaterial: 'MANGA' });
    const original = entry('2', { startDate: '2014-01-01', sourceMaterial: 'WEB_NOVEL' });
    expect(firstSeries([novelization, original], { preferOriginal: true })).toBe(original);
    expect(firstSeries([novelization, original])).toBe(novelization);
    expect(firstSeries([novelization], { preferOriginal: true })).toBe(novelization);
  });
});
