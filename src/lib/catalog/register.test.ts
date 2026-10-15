import { describe, expect, it } from 'vitest';
import type { Edition, Work } from '../db/schema';
import { makeEntry } from './fixtures.test-util';
import type { CatalogEntry } from './types';
import {
  defaultSelection,
  entryMediaType,
  findExistingWork,
  isRegistered,
  ndlSeriesExternalId,
  registeredKeys,
  toEditionInput,
  uniqueByRef,
} from './register';

function edition(workId: string, sourceRefs: Edition['sourceRefs']): Pick<Edition, 'workId' | 'sourceRefs'> {
  return { workId, sourceRefs };
}

function work(id: string, title: string, titleNormalized: string): Work {
  return { id, title, titleNormalized, aliases: [], createdAt: '', updatedAt: '' };
}

const book = (externalId: string, over: Partial<CatalogEntry> = {}) =>
  makeEntry({
    source: 'ndl',
    externalId,
    kind: 'book',
    title: `本好きの下剋上 ${externalId}`,
    extra: { kindHint: 'novel', line: '', series: '本好きの下剋上' },
    ...over,
  });

describe('entryMediaType', () => {
  it('maps each kind', () => {
    const k = (kind: CatalogEntry['kind'], extra = {}) =>
      entryMediaType(makeEntry({ source: 'anilist', externalId: '1', kind, extra }));
    expect(k('anime')).toBe('anime');
    expect(k('manga')).toBe('manga');
    expect(k('novel')).toBe('light-novel');
    expect(k('web-novel')).toBe('web-novel');
  });

  it('maps a book by extra.kindHint (manga -> manga, otherwise light-novel)', () => {
    expect(entryMediaType(book('1', { extra: { kindHint: 'manga' } }))).toBe('manga');
    expect(entryMediaType(book('1', { extra: { kindHint: 'novel' } }))).toBe('light-novel');
    expect(entryMediaType(book('1', { extra: {} }))).toBe('light-novel');
    expect(entryMediaType(book('1', { extra: { kindHint: 'whatever' } }))).toBe('light-novel');
  });
});

describe('toEditionInput', () => {
  it('uses the AniList id and url for anime', () => {
    const input = toEditionInput(
      makeEntry({
        source: 'anilist',
        externalId: '101',
        kind: 'anime',
        title: '薬屋のひとりごと',
        url: 'https://anilist.co/anime/101',
        cover: 'https://img.example/c.jpg',
      }),
    );
    expect(input).toEqual({
      mediaType: 'anime',
      title: '薬屋のひとりごと',
      sourceRefs: [{ source: 'anilist', externalId: '101', url: 'https://anilist.co/anime/101' }],
      coverUrl: 'https://img.example/c.jpg',
      coverFrom: 'anilist',
      linkSource: 'manual',
    });
  });

  it('uses the AniList id for manga and novels', () => {
    const manga = toEditionInput(makeEntry({ source: 'anilist', externalId: '5', kind: 'manga', url: 'https://anilist.co/manga/5' }));
    expect(manga.mediaType).toBe('manga');
    expect(manga.sourceRefs).toEqual([{ source: 'anilist', externalId: '5', url: 'https://anilist.co/manga/5' }]);
    const novel = toEditionInput(makeEntry({ source: 'anilist', externalId: '6', kind: 'novel', url: 'https://anilist.co/manga/6' }));
    expect(novel.mediaType).toBe('light-novel');
    expect(novel.sourceRefs[0]).toMatchObject({ source: 'anilist', externalId: '6' });
  });

  it('uses the Syoboi TID for syoboi rows', () => {
    const input = toEditionInput(
      makeEntry({ source: 'syoboi', externalId: '7653', kind: 'anime', url: 'https://cal.syoboi.jp/tid/7653' }),
    );
    expect(input.sourceRefs).toEqual([{ source: 'syoboi', externalId: '7653', url: 'https://cal.syoboi.jp/tid/7653' }]);
  });

  it('passes narou and kakuyomu through (narou ncode is lowercased like the search results)', () => {
    const narou = toEditionInput(
      makeEntry({ source: 'narou', externalId: 'N2267BE', kind: 'web-novel', url: 'https://ncode.syosetu.com/n2267be/' }),
    );
    expect(narou.mediaType).toBe('web-novel');
    expect(narou.sourceRefs).toEqual([
      { source: 'narou', externalId: 'n2267be', url: 'https://ncode.syosetu.com/n2267be/' },
    ]);
    const kaku = toEditionInput(
      makeEntry({ source: 'kakuyomu', externalId: '1177354054', kind: 'web-novel', url: 'https://kakuyomu.jp/works/1177354054' }),
    );
    expect(kaku.sourceRefs).toEqual([
      { source: 'kakuyomu', externalId: '1177354054', url: 'https://kakuyomu.jp/works/1177354054' },
    ]);
  });

  it('falls back to a source url when the row has none or an unsafe one', () => {
    const anilist = toEditionInput(makeEntry({ source: 'anilist', externalId: '9', kind: 'anime', url: null }));
    expect(anilist.sourceRefs[0].url).toBe('https://anilist.co/anime/9');
    const unsafe = toEditionInput(makeEntry({ source: 'anilist', externalId: '9', kind: 'manga', url: 'javascript:alert(1)' }));
    expect(unsafe.sourceRefs[0].url).toBe('https://anilist.co/manga/9');
    expect(toEditionInput(makeEntry({ source: 'syoboi', externalId: '1', kind: 'anime' })).sourceRefs[0].url).toBe(
      'https://cal.syoboi.jp/tid/1',
    );
  });

  it('drops covers that are not http(s)', () => {
    const input = toEditionInput(makeEntry({ source: 'anilist', externalId: '1', kind: 'anime', cover: 'data:image/png;base64,AAAA' }));
    expect(input.coverUrl).toBeUndefined();
  });

  it('uses the NDL series reference "<kind>:<line>:<series>" for a book', () => {
    const input = toEditionInput(
      book('9784867940228', { extra: { kindHint: 'novel', line: 'TOブックス', series: '本好きの下剋上' } }),
    );
    expect(input.mediaType).toBe('light-novel');
    expect(input.title).toBe('本好きの下剋上');
    expect(input.sourceRefs).toHaveLength(1);
    expect(input.sourceRefs[0].source).toBe('ndl');
    expect(input.sourceRefs[0].externalId).toBe('novel:TOブックス:本好きの下剋上');
    expect(input.sourceRefs[0].url).toMatch(/^https:\/\/ndlsearch\.ndl\.go\.jp\/search\?cs=bib&keyword=/);
  });

  it('uses manga as the NDL kind when kindHint is manga, and an empty line for the main line', () => {
    const entry = book('1', { extra: { kindHint: 'manga', line: '', series: '本好きの下剋上' } });
    expect(ndlSeriesExternalId(entry)).toBe('manga::本好きの下剋上');
    expect(toEditionInput(entry).mediaType).toBe('manga');
  });

  it('falls back to the title when extra.series / extra.line are missing', () => {
    const entry = book('1', { title: 'ある本', extra: {} });
    expect(ndlSeriesExternalId(entry)).toBe('novel::ある本');
  });

  it('keeps the series ref readable by the sync engine (line is the second field)', () => {
    const ref = toEditionInput(book('1', { extra: { kindHint: 'novel', line: 'ジュニア文庫', series: 'A:B' } })).sourceRefs[0];
    const first = ref.externalId.indexOf(':');
    const second = ref.externalId.indexOf(':', first + 1);
    expect(ref.externalId.slice(first + 1, second)).toBe('ジュニア文庫');
  });
});

describe('toEditionInput: 本の表紙をカタログの同じ作品から借りる', () => {
  const anilist = (id: string, kind: CatalogEntry['kind'], cover: string | null, over: Partial<CatalogEntry> = {}) =>
    makeEntry({ source: 'anilist', externalId: id, kind, cover, ...over });
  const novelBook = (over: Partial<CatalogEntry> = {}) => book('9784867940228', over);
  const mangaBook = (over: Partial<CatalogEntry> = {}) =>
    book('9784867940229', { extra: { kindHint: 'manga', line: '', series: '本好きの下剋上' }, ...over });

  it('表紙の無い本は AniList の小説の表紙を借り、coverFrom を catalog にする', () => {
    const siblings = [
      anilist('1', 'anime', 'https://img.example/anime.jpg'),
      anilist('2', 'novel', 'https://img.example/novel.jpg'),
    ];
    const input = toEditionInput(novelBook(), siblings);
    expect(input.coverUrl).toBe('https://img.example/novel.jpg');
    expect(input.coverFrom).toBe('catalog');
  });

  it('本の種別に合う面を先に使う（漫画の本は AniList の漫画、文庫の本は AniList の小説）', () => {
    const siblings = [
      anilist('2', 'novel', 'https://img.example/novel.jpg'),
      anilist('3', 'manga', 'https://img.example/manga.jpg'),
    ];
    expect(toEditionInput(mangaBook(), siblings).coverUrl).toBe('https://img.example/manga.jpg');
    expect(toEditionInput(novelBook(), siblings).coverUrl).toBe('https://img.example/novel.jpg');
  });

  it('合う面に表紙が無ければ、もう一方の漫画・小説、それも無ければアニメの表紙を使う', () => {
    const anime = anilist('1', 'anime', 'https://img.example/anime.jpg');
    const manga = anilist('3', 'manga', 'https://img.example/manga.jpg');
    expect(toEditionInput(novelBook(), [anime, manga]).coverUrl).toBe('https://img.example/manga.jpg');
    expect(toEditionInput(novelBook(), [anime]).coverUrl).toBe('https://img.example/anime.jpg');
    expect(toEditionInput(mangaBook(), [anime, anilist('2', 'novel', null)]).coverUrl).toBe(
      'https://img.example/anime.jpg',
    );
  });

  it('同じ面に複数あれば人気が最大の表紙（同点は先に出てきた方）', () => {
    const siblings = [
      anilist('2', 'novel', 'https://img.example/a.jpg', { popularity: 100 }),
      anilist('4', 'novel', 'https://img.example/b.jpg', { popularity: 900 }),
      anilist('5', 'novel', 'https://img.example/c.jpg', { popularity: 900 }),
    ];
    expect(toEditionInput(novelBook(), siblings).coverUrl).toBe('https://img.example/b.jpg');
  });

  it('http(s) でない・空の表紙と、AniList 以外の行は借りない', () => {
    const siblings = [
      anilist('2', 'novel', 'data:image/png;base64,AAAA'),
      anilist('3', 'manga', ''),
      anilist('4', 'novel', '   '),
      makeEntry({ source: 'narou', externalId: 'n1', kind: 'web-novel', cover: 'https://img.example/web.jpg' }),
      makeEntry({ source: 'syoboi', externalId: '9', kind: 'anime', cover: 'https://img.example/syoboi.jpg' }),
    ];
    const input = toEditionInput(novelBook(), siblings);
    expect(input.coverUrl).toBeUndefined();
    expect(input.coverFrom).toBeUndefined();
  });

  it('本自身の行（entries に本が含まれる）や別の本の表紙は借りない', () => {
    const siblings = [novelBook({ cover: 'https://img.example/other-book.jpg', externalId: '2' })];
    const input = toEditionInput(novelBook(), siblings);
    expect(input.coverUrl).toBeUndefined();
    expect(input.coverFrom).toBeUndefined();
  });

  it('本に自分の表紙があればそれを使い、カタログから入れた印を付ける（手で設定した表紙と区別する）', () => {
    const input = toEditionInput(novelBook({ cover: 'https://img.example/own.jpg' }), [
      anilist('2', 'novel', 'https://img.example/novel.jpg'),
    ]);
    expect(input.coverUrl).toBe('https://img.example/own.jpg');
    expect(input.coverFrom).toBe('catalog');
  });

  it('AniList の行の表紙は AniList から入れた印にする（同期が新しい表紙に入れ替えられるように）', () => {
    const input = toEditionInput(anilist('7', 'anime', 'https://img.example/anime.jpg'));
    expect(input.coverUrl).toBe('https://img.example/anime.jpg');
    expect(input.coverFrom).toBe('anilist');
  });

  it('本自身の表紙が http(s) でなければ借りる', () => {
    const input = toEditionInput(novelBook({ cover: 'javascript:alert(1)' }), [
      anilist('2', 'novel', 'https://img.example/novel.jpg'),
    ]);
    expect(input.coverUrl).toBe('https://img.example/novel.jpg');
    expect(input.coverFrom).toBe('catalog');
  });

  it('同じ作品の行が無ければ表紙なしのまま、印も付けない（引数を省いた呼び出しも同じ）', () => {
    for (const input of [toEditionInput(novelBook()), toEditionInput(novelBook(), [])]) {
      expect(input.coverUrl).toBeUndefined();
      expect(input.coverFrom).toBeUndefined();
    }
  });

  it('本以外の行は借りない（表紙の無いアニメ・漫画はそのまま）', () => {
    const siblings = [anilist('9', 'manga', 'https://img.example/manga.jpg')];
    for (const kind of ['anime', 'manga', 'novel'] as const) {
      const input = toEditionInput(anilist('1', kind, null), siblings);
      expect(input.coverUrl).toBeUndefined();
      expect(input.coverFrom).toBeUndefined();
    }
  });
});

describe('registeredKeys / isRegistered', () => {
  const editions = [
    edition('w1', [{ source: 'anilist', externalId: '101', url: '' }]),
    edition('w1', [
      { source: 'narou', externalId: 'n2267be', url: '' },
      { source: 'kakuyomu', externalId: '99', url: '' },
    ]),
    edition('w2', [{ source: 'ndl', externalId: 'novel::本好きの下剋上', url: '' }]),
  ];

  it('collects source:externalId from every edition', () => {
    expect(registeredKeys(editions)).toEqual(
      new Set(['anilist:101', 'narou:n2267be', 'kakuyomu:99', 'ndl:novel::本好きの下剋上']),
    );
  });

  it('detects registered rows by (source, externalId)', () => {
    const keys = registeredKeys(editions);
    expect(isRegistered(makeEntry({ source: 'anilist', externalId: '101', kind: 'anime' }), keys)).toBe(true);
    expect(isRegistered(makeEntry({ source: 'anilist', externalId: '102', kind: 'anime' }), keys)).toBe(false);
    expect(isRegistered(makeEntry({ source: 'kakuyomu', externalId: '99', kind: 'web-novel' }), keys)).toBe(true);
    // 同じ ID でもソースが違えば別物
    expect(isRegistered(makeEntry({ source: 'syoboi', externalId: '101', kind: 'anime' }), keys)).toBe(false);
  });

  it('treats the narou ncode case-insensitively', () => {
    const keys = registeredKeys(editions);
    expect(isRegistered(makeEntry({ source: 'narou', externalId: 'N2267BE', kind: 'web-novel' }), keys)).toBe(true);
  });

  it('detects a book by its NDL series reference, whichever volume it is', () => {
    const keys = registeredKeys(editions);
    expect(isRegistered(book('9784867940228'), keys)).toBe(true);
    expect(isRegistered(book('9784867940999'), keys)).toBe(true);
    // 別の系統（line）や種別（manga）は別シリーズ
    expect(isRegistered(book('1', { extra: { kindHint: 'novel', line: 'ジュニア文庫', series: '本好きの下剋上' } }), keys)).toBe(false);
    expect(isRegistered(book('1', { extra: { kindHint: 'manga', line: '', series: '本好きの下剋上' } }), keys)).toBe(false);
  });
});

describe('uniqueByRef', () => {
  it('keeps one book per series but both lines', () => {
    const a = book('1');
    const b = book('2');
    const c = book('3', { extra: { kindHint: 'novel', line: 'ジュニア文庫', series: '本好きの下剋上' } });
    expect(uniqueByRef([a, b, c])).toEqual([a, c]);
  });
});

describe('findExistingWork', () => {
  const works = [work('w1', '薬屋のひとりごと', '薬屋のひとりごと'), work('w2', '別の作品', '別の作品')];

  it('prefers the work that already holds a row of this catalog work', () => {
    const entries = [makeEntry({ source: 'anilist', externalId: '101', kind: 'anime', title: '薬屋のひとりごと' })];
    const editions = [edition('w2', [{ source: 'anilist', externalId: '101', url: '' }])];
    expect(findExistingWork('薬屋のひとりごと', entries, works, editions)?.id).toBe('w2');
  });

  it('falls back to the work with the same normalized title', () => {
    expect(findExistingWork('薬屋の ひとりごと！', [], works, [])?.id).toBe('w1');
    expect(findExistingWork('知らない作品', [], works, [])).toBeUndefined();
  });
});

describe('defaultSelection', () => {
  const entries = [
    makeEntry({ source: 'anilist', externalId: '1', kind: 'anime', popularity: 100 }),
    makeEntry({ source: 'anilist', externalId: '2', kind: 'anime', popularity: 900 }),
    makeEntry({ source: 'syoboi', externalId: '3', kind: 'anime', popularity: 5000 }),
    makeEntry({ source: 'anilist', externalId: '4', kind: 'manga', popularity: 10 }),
    makeEntry({ source: 'anilist', externalId: '5', kind: 'manga', popularity: 20 }),
    makeEntry({ source: 'anilist', externalId: '6', kind: 'novel', popularity: 7 }),
    makeEntry({ source: 'narou', externalId: 'n1', kind: 'web-novel', popularity: 3 }),
    makeEntry({ source: 'kakuyomu', externalId: 'k1', kind: 'web-novel', popularity: 30 }),
    book('b1', { startDate: '2025-01-10', volumes: 1 }),
    book('b3', { startDate: '2026-11-10', volumes: 3 }),
    book('b2', { startDate: '2025-06-10', volumes: 2 }),
  ];

  it('picks one representative per kind', () => {
    const sel = defaultSelection({ entries });
    expect(Object.keys(sel).sort()).toEqual(['anime', 'book', 'manga', 'novel', 'web-novel']);
    // anime / 原作は AniList の人気最大（しょぼかるの値は見ない）
    expect(sel.anime?.id).toBe('anilist:2');
    expect(sel.manga?.id).toBe('anilist:5');
    expect(sel.novel?.id).toBe('anilist:6');
    // web-novel は人気最大（ソースを問わない）
    expect(sel['web-novel']?.id).toBe('kakuyomu:k1');
    // book は最新の巻
    expect(sel.book?.id).toBe('ndl:b3');
  });

  it('prefers the airing anime season over a more popular finished one', () => {
    const seasons = [
      makeEntry({ source: 'anilist', externalId: '1', kind: 'anime', popularity: 900, status: 'FINISHED', format: 'TV' }),
      makeEntry({ source: 'anilist', externalId: '2', kind: 'anime', popularity: 300, status: 'RELEASING', format: 'TV' }),
      makeEntry({ source: 'anilist', externalId: '3', kind: 'anime', popularity: 50, status: 'NOT_YET_RELEASED', format: 'TV' }),
    ];
    expect(defaultSelection({ entries: seasons }).anime?.id).toBe('anilist:2');
  });

  it('among airing anime, prefers a TV season over a more popular short or ONA', () => {
    const airing = [
      makeEntry({ source: 'anilist', externalId: '1', kind: 'anime', popularity: 900, status: 'RELEASING', format: 'ONA' }),
      makeEntry({ source: 'anilist', externalId: '2', kind: 'anime', popularity: 100, status: 'RELEASING', format: 'TV' }),
    ];
    expect(defaultSelection({ entries: airing }).anime?.id).toBe('anilist:2');
  });

  it('keeps the most popular anime when nothing is airing', () => {
    const finished = [
      makeEntry({ source: 'anilist', externalId: '1', kind: 'anime', popularity: 900, status: 'FINISHED' }),
      makeEntry({ source: 'anilist', externalId: '3', kind: 'anime', popularity: 50, status: 'NOT_YET_RELEASED' }),
    ];
    expect(defaultSelection({ entries: finished }).anime?.id).toBe('anilist:1');
  });

  it('omits kinds with no rows', () => {
    const sel = defaultSelection({ entries: [entries[0]] });
    expect(Object.keys(sel)).toEqual(['anime']);
    expect(defaultSelection({ entries: [] })).toEqual({});
  });

  it('falls back to the other sources when a kind has no AniList row', () => {
    const only = [makeEntry({ source: 'syoboi', externalId: '3', kind: 'anime', popularity: null })];
    expect(defaultSelection({ entries: only }).anime?.id).toBe('syoboi:3');
  });

  it('breaks popularity ties by order and treats a missing popularity as lowest', () => {
    const tie = [
      makeEntry({ source: 'anilist', externalId: 'a', kind: 'anime', popularity: null }),
      makeEntry({ source: 'anilist', externalId: 'b', kind: 'anime', popularity: 0 }),
      makeEntry({ source: 'anilist', externalId: 'c', kind: 'anime', popularity: 0 }),
    ];
    expect(defaultSelection({ entries: tie }).anime?.id).toBe('anilist:b');
  });

  it('chooses the later volume number when two books share a release date', () => {
    const same = [book('v1', { startDate: '2026-01-01', volumes: 1 }), book('v2', { startDate: '2026-01-01', volumes: 2 })];
    expect(defaultSelection({ entries: same }).book?.id).toBe('ndl:v2');
  });
});
