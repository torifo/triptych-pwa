import { describe, expect, it } from 'vitest';
import { makeEntry } from '../catalog/fixtures.test-util';
import type { CatalogWorkDetail } from '../catalog/types';
import { catalogCoverUrl, detailHeroCover, entryCover, heroCover, summaryMediaType } from './catalog-cover';

describe('catalogCoverUrl', () => {
  it('http(s) の URL だけを表紙にする', () => {
    expect(catalogCoverUrl('https://s4.anilist.co/a.jpg')).toBe('https://s4.anilist.co/a.jpg');
    expect(catalogCoverUrl(' https://s4.anilist.co/a.jpg ')).toBe('https://s4.anilist.co/a.jpg');
    expect(catalogCoverUrl('javascript:alert(1)')).toBeUndefined();
    expect(catalogCoverUrl('data:image/png;base64,AAAA')).toBeUndefined();
    expect(catalogCoverUrl('')).toBeUndefined();
    expect(catalogCoverUrl(null)).toBeUndefined();
    expect(catalogCoverUrl(undefined)).toBeUndefined();
  });
});

describe('summaryMediaType', () => {
  it('原作の媒体があればそれ（小説原作で原作小説の面が無ければ Web 小説）', () => {
    expect(summaryMediaType(['novel', 'anime'], 'anime')).toBe('anime');
    expect(summaryMediaType(['anime', 'manga'], 'manga')).toBe('manga');
    expect(summaryMediaType(['anime', 'novel', 'web-novel'], 'novel')).toBe('light-novel');
    expect(summaryMediaType(['anime', 'web-novel'], 'novel')).toBe('web-novel');
    expect(summaryMediaType(['book'], 'novel')).toBe('light-novel');
  });

  it('原作が分からなければ原作らしい面から（原作小説・漫画・Web 小説・アニメの順）', () => {
    expect(summaryMediaType(['novel', 'anime'])).toBe('light-novel');
    expect(summaryMediaType(['anime', 'manga'], null)).toBe('manga');
    expect(summaryMediaType(['anime', 'web-novel'])).toBe('web-novel');
    expect(summaryMediaType(['anime'])).toBe('anime');
  });

  it('紙の本だけなら媒体を決めない', () => {
    expect(summaryMediaType(['book'])).toBeUndefined();
    expect(summaryMediaType([])).toBeUndefined();
  });
});

describe('entryCover', () => {
  const covers = { novelCover: 'https://x/novel.jpg', mangaCover: 'https://x/manga.jpg' };
  const book = (kindHint: string, cover: string | null = null) =>
    makeEntry({ source: 'ndl', externalId: `b-${kindHint}`, kind: 'book', title: '本', cover, extra: { kindHint, series: '本' } });

  it('行の題名・媒体・表紙を渡す', () => {
    const entry = makeEntry({ source: 'anilist', externalId: '1', kind: 'anime', title: '薬屋', cover: 'https://x/a.jpg' });
    expect(entryCover(entry)).toEqual({ url: 'https://x/a.jpg', title: '薬屋', mediaType: 'anime' });
  });

  it('壊れた URL は表紙にしない', () => {
    const entry = makeEntry({ source: 'narou', externalId: 'n1', kind: 'web-novel', cover: 'ftp://x/a.jpg' });
    expect(entryCover(entry).url).toBeUndefined();
    expect(entryCover(entry).mediaType).toBe('web-novel');
  });

  it('表紙の無い本は種別に合う原作の表紙（小説の本は原作小説、漫画の本は漫画）', () => {
    expect(entryCover(book('novel'), covers)).toEqual({ url: 'https://x/novel.jpg', title: '本', mediaType: 'light-novel' });
    expect(entryCover(book('manga'), covers)).toEqual({ url: 'https://x/manga.jpg', title: '本', mediaType: 'manga' });
    expect(entryCover(book('manga'), { novelCover: 'https://x/novel.jpg' }).url).toBeUndefined();
    expect(entryCover(book('novel', 'https://x/own.jpg'), covers).url).toBe('https://x/own.jpg');
  });

  it('なろう・カクヨムの行は原作小説の表紙', () => {
    for (const source of ['narou', 'kakuyomu']) {
      const entry = makeEntry({ source, externalId: 'w1', kind: 'web-novel' });
      expect(entryCover(entry, covers).url).toBe('https://x/novel.jpg');
      expect(entryCover(entry, { mangaCover: 'https://x/manga.jpg' }).url).toBeUndefined();
    }
  });

  it('アニメ・漫画・原作小説の行は自分の表紙だけ（借りない）、壊れた借り先も使わない', () => {
    for (const kind of ['anime', 'manga', 'novel'] as const) {
      expect(entryCover(makeEntry({ source: 'anilist', externalId: '9', kind }), covers).url).toBeUndefined();
    }
    expect(entryCover(book('novel'), { novelCover: 'javascript:alert(1)' }).url).toBeUndefined();
  });
});

describe('heroCover', () => {
  const anime = (id: string, over = {}) => makeEntry({ source: 'anilist', externalId: id, kind: 'anime', cover: `https://x/anime-${id}.jpg`, ...over });
  const manga = (id: string, over = {}) => makeEntry({ source: 'anilist', externalId: id, kind: 'manga', cover: `https://x/manga-${id}.jpg`, ...over });
  const novel = (id: string, over = {}) => makeEntry({ source: 'anilist', externalId: id, kind: 'novel', cover: `https://x/novel-${id}.jpg`, ...over });

  it('原作の媒体の表紙を先にする（人気の高いアニメより原作）', () => {
    const entries = [anime('1', { popularity: 9000 }), manga('2', { popularity: 500 }), novel('3', { popularity: 100 })];
    expect(heroCover(entries, '作品', 'novel')).toEqual({ url: 'https://x/novel-3.jpg', title: '作品', mediaType: 'light-novel' });
    expect(heroCover(entries, '作品', 'manga').url).toBe('https://x/manga-2.jpg');
    expect(heroCover(entries, '作品', 'anime').url).toBe('https://x/anime-1.jpg');
  });

  it('原作が分からなければ原作小説・漫画・アニメの順', () => {
    const entries = [anime('1'), manga('2'), novel('3')];
    expect(heroCover(entries, '作品').url).toBe('https://x/novel-3.jpg');
    expect(heroCover(entries.slice(0, 2), '作品').url).toBe('https://x/manga-2.jpg');
    expect(heroCover(entries.slice(0, 1), '作品', 'novel').url).toBe('https://x/anime-1.jpg');
  });

  it('同じ面なら最初の期・最初のシリーズ（開始の早いもの）、小説はノベライズを後ろに', () => {
    const seasons = [anime('3', { startDate: '2024-04-04', popularity: 900 }), anime('1', { startDate: '2018-01-04', popularity: 10 }), anime('2', { startDate: '2021-01-07' })];
    expect(heroCover(seasons, '作品', 'anime').url).toBe('https://x/anime-1.jpg');
    const novels = [novel('1', { startDate: '2010-01-01', sourceMaterial: 'MANGA' }), novel('2', { startDate: '2014-01-01' })];
    expect(heroCover(novels, '作品', 'novel').url).toBe('https://x/novel-2.jpg');
  });

  it('開始日が同じ（不明）なら人気の高い行を選ぶ', () => {
    const entries = [anime('1', { popularity: 10 }), anime('2', { popularity: 80 }), anime('3')];
    expect(heroCover(entries, '作品', 'anime').url).toBe('https://x/anime-2.jpg');
  });

  it('同じ面なら AniList 以外より AniList、原作の本の表紙は漫画・アニメより先', () => {
    const jproNovel = makeEntry({ source: 'jpro', externalId: 'j1', kind: 'book', cover: 'https://x/book.jpg', extra: { kindHint: 'novel' } });
    expect(heroCover([jproNovel, novel('3')], '作品', 'novel').url).toBe('https://x/novel-3.jpg');
    expect(heroCover([anime('1'), manga('2'), jproNovel], '作品', 'novel')).toEqual({ url: 'https://x/book.jpg', title: '作品', mediaType: 'light-novel' });
  });

  it('Web 小説の行の表紙は代表にしない', () => {
    const web = makeEntry({ source: 'narou', externalId: 'n1', kind: 'web-novel', cover: 'https://x/web.jpg' });
    expect(heroCover([web, anime('1', { cover: null })], '作品', 'novel')).toEqual({ title: '作品', mediaType: 'web-novel' });
  });

  it('安全でない URL は選ばない', () => {
    expect(heroCover([novel('1', { cover: 'javascript:alert(1)' }), manga('2')], '作品', 'novel').url).toBe('https://x/manga-2.jpg');
  });

  it('表紙がどの行にも無ければ文字の表紙（媒体は原作、分からなければ原作らしい面）', () => {
    const entries = [
      makeEntry({ source: 'narou', externalId: 'n1', kind: 'web-novel' }),
      makeEntry({ source: 'anilist', externalId: '2', kind: 'manga' }),
    ];
    expect(heroCover(entries, '作品')).toEqual({ title: '作品', mediaType: 'manga' });
    expect(heroCover(entries, '作品', 'novel')).toEqual({ title: '作品', mediaType: 'web-novel' });
    expect(heroCover([makeEntry({ source: 'ndl', externalId: 'b', kind: 'book', extra: { kindHint: 'manga' } })], '作品')).toEqual({ title: '作品', mediaType: 'manga' });
    expect(heroCover([], '作品')).toEqual({ title: '作品', mediaType: undefined });
  });
});

describe('detailHeroCover', () => {
  const detail: CatalogWorkDetail = {
    id: 'c1',
    title: '本好きの下剋上',
    kinds: ['anime', 'novel'],
    entries: [
      makeEntry({ source: 'anilist', externalId: '1', kind: 'anime', cover: 'https://x/anime.jpg' }),
      makeEntry({ source: 'anilist', externalId: '2', kind: 'novel', cover: 'https://x/novel.jpg' }),
    ],
    candidates: [],
  };

  it('collector の原作の表紙（cover）を使う', () => {
    expect(detailHeroCover({ ...detail, cover: 'https://x/original.jpg', origin: 'novel' })).toEqual({
      url: 'https://x/original.jpg',
      title: '本好きの下剋上',
      mediaType: 'light-novel',
    });
  });

  it('cover が無い・壊れていれば行から原作の順で選ぶ', () => {
    expect(detailHeroCover(detail).url).toBe('https://x/novel.jpg');
    expect(detailHeroCover({ ...detail, cover: 'javascript:alert(1)', origin: 'anime' }).url).toBe('https://x/anime.jpg');
  });
});
