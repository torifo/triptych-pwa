import { describe, expect, it } from 'vitest';
import type { Edition, Release } from '../db/schema';
import {
  editionDisambiguators,
  editionSubtitle,
  LIBRARY_LAYOUT_KEY,
  libraryCover,
  loadLibraryLayout,
  progressRatio,
  progressText,
  saveLibraryLayout,
  seasonLabel,
  seasonStartByEdition,
  sortLibraryEditions,
} from './library-view';

function edition(
  id: string,
  over: Partial<Pick<Edition, 'workId' | 'mediaType' | 'title' | 'createdAt'>> = {},
): Pick<Edition, 'id' | 'workId' | 'mediaType' | 'title' | 'createdAt'> {
  return {
    id,
    workId: 'w1',
    mediaType: 'anime',
    title: '本好きの下剋上',
    createdAt: '2026-10-01T00:00:00.000Z',
    ...over,
  };
}

function release(over: Partial<Release> & Pick<Release, 'id' | 'editionId' | 'date'>): Release {
  return {
    kind: 'season-start',
    label: '新期',
    precision: 'day',
    dateHistory: [],
    source: 'anilist',
    createdAt: '2026-10-01T00:00:00.000Z',
    updatedAt: '2026-10-01T00:00:00.000Z',
    ...over,
  };
}

const titles: Record<string, string> = { w1: '本好きの下剋上', w2: 'あの作品' };
const workTitleOf = (id: string) => titles[id] ?? '';

describe('editionSubtitle', () => {
  it('Work 題名と同じ（表記ゆれ含む）なら副題なし', () => {
    expect(editionSubtitle('本好きの下剋上', '本好きの下剋上')).toBe('');
    expect(editionSubtitle('Re:ゼロ', 'Ｒｅ：ゼロ')).toBe('');
    expect(editionSubtitle('作品', '  ')).toBe('');
  });

  it('Work 題名で始まる題名は、その後ろだけを副題にする', () => {
    expect(editionSubtitle('本好きの下剋上', '本好きの下剋上 第3部')).toBe('第3部');
    expect(editionSubtitle('本好きの下剋上', '本好きの下剋上：司書になるためには')).toBe('司書になるためには');
    expect(editionSubtitle('本好きの下剋上', '本好きの下剋上（コミック版）')).toBe('（コミック版）');
  });

  it('頭が違う題名はそのまま使う', () => {
    expect(editionSubtitle('本好きの下剋上', 'Ascendance of a Bookworm')).toBe('Ascendance of a Bookworm');
  });
});

describe('seasonStartByEdition / seasonLabel', () => {
  it('Edition ごとに最も早い新期開始を選び、壊れた日付と別種別は無視する', () => {
    const map = seasonStartByEdition([
      release({ id: 'a', editionId: 'e1', date: '2026-10-04' }),
      release({ id: 'b', editionId: 'e1', date: '2024-04-06' }),
      release({ id: 'c', editionId: 'e1', date: 'broken' }),
      release({ id: 'd', editionId: 'e1', kind: 'episode', date: '2020-01-01' }),
      release({ id: 'e', editionId: 'e2', date: '2026-01-01', precision: 'year' }),
    ]);
    expect(map.get('e1')?.date).toBe('2024-04-06');
    expect(map.get('e2')).toEqual({ date: '2026-01-01', precision: 'year' });
    expect(map.has('e3')).toBe(false);
  });

  it('日・月精度は年月、年精度は年だけを出す', () => {
    expect(seasonLabel({ date: '2026-10-04', precision: 'day' })).toBe('2026年10月〜');
    expect(seasonLabel({ date: '2026-10-01', precision: 'month' })).toBe('2026年10月〜');
    expect(seasonLabel({ date: '2026-01-01', precision: 'year' })).toBe('2026年〜');
    expect(seasonLabel({ date: '2026-10-04' })).toBe('2026年10月〜');
  });
});

describe('editionDisambiguators', () => {
  it('同じ Work の同じ媒体が 2 つある時だけ、開始年月と副題を返す', () => {
    const editions = [
      edition('e1', { title: '本好きの下剋上 第2部' }),
      edition('e2', { title: '本好きの下剋上 第3部' }),
      edition('solo', { workId: 'w2', title: 'あの作品' }),
      edition('manga', { mediaType: 'manga', title: '本好きの下剋上' }),
    ];
    const starts = new Map([
      ['e1', { date: '2020-10-04', precision: 'day' as const }],
      ['e2', { date: '2026-10-04', precision: 'day' as const }],
    ]);
    const labels = editionDisambiguators(editions, workTitleOf, starts);
    expect(labels.get('e1')).toBe('2020年10月〜 ・ 第2部');
    expect(labels.get('e2')).toBe('2026年10月〜 ・ 第3部');
    expect(labels.has('solo')).toBe(false);
    expect(labels.has('manga')).toBe(false);
  });

  it('題名が同じでも開始年月で見分ける', () => {
    const editions = [edition('e1'), edition('e2')];
    const starts = new Map([
      ['e1', { date: '2019-10-03', precision: 'day' as const }],
      ['e2', { date: '2021-04-05', precision: 'day' as const }],
    ]);
    const labels = editionDisambiguators(editions, workTitleOf, starts);
    expect(labels.get('e1')).toBe('2019年10月〜');
    expect(labels.get('e2')).toBe('2021年4月〜');
  });

  it('副題でも見分けがつかない時だけ登録日（JST）を足す', () => {
    const editions = [
      edition('e1', { createdAt: '2026-10-01T16:00:00.000Z' }), // JST で 10/2 01:00
      edition('e2', { createdAt: '2026-10-05T00:00:00.000Z' }),
      edition('e3', { workId: 'w2', title: 'あの作品' }),
    ];
    const labels = editionDisambiguators(editions, workTitleOf, new Map());
    expect(labels.get('e1')).toBe('登録 2026/10/02');
    expect(labels.get('e2')).toBe('登録 2026/10/05');
    expect(labels.has('e3')).toBe(false);
  });

  it('片方だけ開始年月がある時は、見分けがついているので登録日を足さない', () => {
    const editions = [edition('e1'), edition('e2')];
    const starts = new Map([['e1', { date: '2026-10-04', precision: 'day' as const }]]);
    const labels = editionDisambiguators(editions, workTitleOf, starts);
    expect(labels.get('e1')).toBe('2026年10月〜');
    expect(labels.has('e2')).toBe(false);
  });
});

describe('sortLibraryEditions', () => {
  it('Work 題名順を保ち、同じ Work は媒体 → 開始日 → 登録順に並べる', () => {
    const editions = [
      edition('z-late', { workId: 'w1' }),
      edition('a-solo', { workId: 'w2', title: 'あの作品' }),
      edition('manga', { workId: 'w1', mediaType: 'manga' }),
      edition('z-early', { workId: 'w1' }),
      edition('z-nodate', { workId: 'w1', createdAt: '2026-09-01T00:00:00.000Z' }),
    ];
    const starts = new Map([
      ['z-late', { date: '2026-10-04', precision: 'day' as const }],
      ['z-early', { date: '2020-10-04', precision: 'day' as const }],
    ]);
    const sorted = sortLibraryEditions(editions, workTitleOf, starts);
    expect(sorted.map((e) => e.id)).toEqual(['a-solo', 'manga', 'z-early', 'z-late', 'z-nodate']);
  });

  it('入力を書き換えない', () => {
    const editions = [edition('b'), edition('a')];
    sortLibraryEditions(editions, workTitleOf, new Map());
    expect(editions.map((e) => e.id)).toEqual(['b', 'a']);
  });
});

describe('loadLibraryLayout / saveLibraryLayout', () => {
  function memoryStorage(initial: Record<string, string> = {}) {
    const data = new Map(Object.entries(initial));
    return {
      getItem: (key: string) => data.get(key) ?? null,
      setItem: (key: string, value: string) => void data.set(key, value),
      data,
    };
  }
  const throwing = {
    getItem: () => {
      throw new Error('SecurityError');
    },
    setItem: () => {
      throw new Error('QuotaExceededError');
    },
  };

  it('既定は棚。保存した一覧は一覧で開く', () => {
    expect(loadLibraryLayout(memoryStorage())).toBe('shelf');
    expect(loadLibraryLayout(memoryStorage({ [LIBRARY_LAYOUT_KEY]: 'list' }))).toBe('list');
    expect(loadLibraryLayout(memoryStorage({ [LIBRARY_LAYOUT_KEY]: 'grid' }))).toBe('shelf');
  });

  it('保存した値を読み戻せる', () => {
    const storage = memoryStorage();
    saveLibraryLayout('list', storage);
    expect(storage.data.get(LIBRARY_LAYOUT_KEY)).toBe('list');
    expect(loadLibraryLayout(storage)).toBe('list');
  });

  it('localStorage が使えない時は棚にして、保存の失敗は投げない', () => {
    expect(loadLibraryLayout(null)).toBe('shelf');
    expect(loadLibraryLayout(throwing)).toBe('shelf');
    expect(() => saveLibraryLayout('list', throwing)).not.toThrow();
    expect(() => saveLibraryLayout('list', null)).not.toThrow();
  });
});

describe('libraryCover', () => {
  const work = { title: '薬屋のひとりごと', coverEditionId: undefined };
  const anime = { id: 'a', coverUrl: 'https://x/anime.jpg', mediaType: 'anime' as const, title: '薬屋 第2期' };
  const manga = { id: 'm', coverUrl: undefined, mediaType: 'manga' as const, title: '薬屋のひとりごと' };

  it('Edition 自身の表紙を使い、題名は Work 名にする', () => {
    expect(libraryCover(anime, work, [anime, manga])).toEqual({
      url: 'https://x/anime.jpg',
      title: '薬屋のひとりごと',
      mediaType: 'anime',
    });
  });

  it('表紙の無い Edition は同じ Work の代表表紙を借り、媒体は自分のまま', () => {
    expect(libraryCover(manga, work, [anime, manga])).toEqual({
      url: 'https://x/anime.jpg',
      title: '薬屋のひとりごと',
      mediaType: 'manga',
    });
  });

  it('どこにも表紙が無ければ文字の表紙（url なし）', () => {
    expect(libraryCover(manga, work, [manga]).url).toBeUndefined();
    expect(libraryCover(manga, undefined, [anime]).url).toBeUndefined();
    expect(libraryCover(manga, undefined, [anime]).title).toBe('薬屋のひとりごと');
  });
});

describe('progressRatio / progressText', () => {
  it('全体数がある時だけ割合を出し、0〜1 に収める', () => {
    expect(progressRatio({ unit: 'episode', current: 3, total: 12 })).toBe(0.25);
    expect(progressRatio({ unit: 'episode', current: 15, total: 12 })).toBe(1);
    expect(progressRatio({ unit: 'episode', current: -1, total: 12 })).toBe(0);
    expect(progressRatio({ unit: 'volume', current: 3 })).toBeUndefined();
    expect(progressRatio({ unit: 'volume', current: 3, total: 0 })).toBeUndefined();
    expect(progressRatio(undefined)).toBeUndefined();
  });

  it('進捗を「3/12話」の形にする', () => {
    expect(progressText({ unit: 'episode', current: 3, total: 12 })).toBe('3/12話');
    expect(progressText({ unit: 'volume', current: 5 })).toBe('5巻');
    expect(progressText({ unit: 'part', current: 0, total: 0 })).toBe('0部分');
    expect(progressText(undefined)).toBe('');
  });
});
