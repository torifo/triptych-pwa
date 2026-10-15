import { describe, expect, it } from 'vitest';
import { makeEntry } from '../catalog/fixtures.test-util';
import { catalogCoverUrl, entryCover, heroCover, summaryMediaType } from './catalog-cover';

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
  it('面の並びで最初に当たる媒体を返す', () => {
    expect(summaryMediaType(['novel', 'anime'])).toBe('anime');
    expect(summaryMediaType(['novel', 'manga'])).toBe('manga');
    expect(summaryMediaType(['book', 'novel'])).toBe('light-novel');
    expect(summaryMediaType(['web-novel'])).toBe('web-novel');
  });
  it('紙の本だけなら媒体を決めない', () => {
    expect(summaryMediaType(['book'])).toBeUndefined();
    expect(summaryMediaType([])).toBeUndefined();
  });
});

describe('entryCover', () => {
  it('行の題名・媒体・表紙を渡す', () => {
    const entry = makeEntry({ source: 'anilist', externalId: '1', kind: 'anime', title: '薬屋', cover: 'https://x/a.jpg' });
    expect(entryCover(entry)).toEqual({ url: 'https://x/a.jpg', title: '薬屋', mediaType: 'anime' });
  });
  it('壊れた URL は表紙にしない', () => {
    const entry = makeEntry({ source: 'narou', externalId: 'n1', kind: 'web-novel', cover: 'ftp://x/a.jpg' });
    expect(entryCover(entry).url).toBeUndefined();
    expect(entryCover(entry).mediaType).toBe('web-novel');
  });
});

describe('heroCover', () => {
  it('AniList の行を優先し、アニメ・漫画・原作小説の順に選ぶ', () => {
    const entries = [
      makeEntry({ source: 'anilist', externalId: '3', kind: 'novel', cover: 'https://x/novel.jpg', popularity: 900 }),
      makeEntry({ source: 'anilist', externalId: '2', kind: 'manga', cover: 'https://x/manga.jpg', popularity: 500 }),
      makeEntry({ source: 'anilist', externalId: '1', kind: 'anime', cover: 'https://x/anime.jpg', popularity: 100 }),
    ];
    expect(heroCover(entries, '作品')).toEqual({ url: 'https://x/anime.jpg', title: '作品', mediaType: 'anime' });
    expect(heroCover(entries.slice(0, 2), '作品').url).toBe('https://x/manga.jpg');
  });

  it('同じ面なら人気の高い行を選ぶ', () => {
    const entries = [
      makeEntry({ source: 'anilist', externalId: '1', kind: 'anime', cover: 'https://x/a1.jpg', popularity: 10 }),
      makeEntry({ source: 'anilist', externalId: '2', kind: 'anime', cover: 'https://x/a2.jpg', popularity: 80 }),
      makeEntry({ source: 'anilist', externalId: '3', kind: 'anime', cover: 'https://x/a3.jpg' }),
    ];
    expect(heroCover(entries, '作品').url).toBe('https://x/a2.jpg');
  });

  it('AniList 以外の表紙より AniList の表紙を優先する', () => {
    const entries = [
      makeEntry({ source: 'narou', externalId: 'n1', kind: 'web-novel', cover: 'https://x/web.jpg' }),
      makeEntry({ source: 'anilist', externalId: '3', kind: 'novel', cover: 'https://x/novel.jpg' }),
    ];
    expect(heroCover(entries, '作品')).toEqual({ url: 'https://x/novel.jpg', title: '作品', mediaType: 'light-novel' });
  });

  it('AniList に表紙が無ければ表紙のある別の行を使う', () => {
    const entries = [
      makeEntry({ source: 'anilist', externalId: '1', kind: 'anime', cover: null }),
      makeEntry({ source: 'narou', externalId: 'n1', kind: 'web-novel', cover: 'https://x/web.jpg' }),
    ];
    expect(heroCover(entries, '作品')).toEqual({ url: 'https://x/web.jpg', title: '作品', mediaType: 'web-novel' });
  });

  it('安全でない URL は選ばない', () => {
    const entries = [
      makeEntry({ source: 'anilist', externalId: '1', kind: 'anime', cover: 'javascript:alert(1)' }),
      makeEntry({ source: 'anilist', externalId: '2', kind: 'manga', cover: 'https://x/manga.jpg' }),
    ];
    expect(heroCover(entries, '作品').url).toBe('https://x/manga.jpg');
  });

  it('表紙がどの行にも無ければ文字の表紙（媒体は面の並びで最初の行）', () => {
    const entries = [
      makeEntry({ source: 'narou', externalId: 'n1', kind: 'web-novel' }),
      makeEntry({ source: 'anilist', externalId: '2', kind: 'manga' }),
    ];
    expect(heroCover(entries, '作品')).toEqual({ title: '作品', mediaType: 'manga' });
    expect(heroCover([], '作品')).toEqual({ title: '作品', mediaType: undefined });
  });
});
