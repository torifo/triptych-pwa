import { describe, expect, it } from 'vitest';
import type { Edition, Release } from '../db/schema';
import {
  editionTabs,
  firstAiringTimeToday,
  initialEditionId,
  orderWorkEditions,
  suggestedMediaType,
  workMediaTypes,
} from './work-detail-view';

const TODAY = '2026-10-15';

function edition(
  id: string,
  over: Partial<Pick<Edition, 'mediaType' | 'title' | 'createdAt' | 'status'>> = {},
): Pick<Edition, 'id' | 'workId' | 'mediaType' | 'title' | 'createdAt' | 'status'> {
  return {
    id,
    workId: 'w1',
    mediaType: 'anime',
    title: '本好きの下剋上',
    createdAt: '2026-10-01T00:00:00.000Z',
    status: 'active',
    ...over,
  };
}

function release(over: Partial<Release> & Pick<Release, 'id' | 'editionId' | 'date'>): Release {
  return {
    kind: 'episode',
    label: over.id,
    precision: 'day',
    dateHistory: [],
    source: 'anilist',
    createdAt: '2026-10-01T00:00:00.000Z',
    updatedAt: '2026-10-01T00:00:00.000Z',
    ...over,
  };
}

describe('orderWorkEditions', () => {
  it('媒体（ANIME → 漫画 → 文庫 → WEB）→ 新しい期から → 登録順に並べる', () => {
    const editions = [
      edition('anime-late'),
      edition('manga', { mediaType: 'manga' }),
      edition('anime-early'),
      edition('web', { mediaType: 'web-novel' }),
    ];
    const starts = new Map([
      ['anime-late', { date: '2026-10-04', precision: 'day' as const }],
      ['anime-early', { date: '2020-10-03', precision: 'day' as const }],
    ]);
    expect(orderWorkEditions(editions, starts).map((e) => e.id)).toEqual([
      'anime-late',
      'anime-early',
      'manga',
      'web',
    ]);
  });

  it('開始日の無い期は同じ媒体の後ろ、同じ開始日は登録順', () => {
    const editions = [
      edition('b', { createdAt: '2026-10-02T00:00:00.000Z' }),
      edition('unknown'),
      edition('a', { createdAt: '2026-10-01T00:00:00.000Z' }),
    ];
    const starts = new Map([
      ['a', { date: '2026-10-04', precision: 'day' as const }],
      ['b', { date: '2026-10-04', precision: 'day' as const }],
    ]);
    expect(orderWorkEditions(editions, starts).map((e) => e.id)).toEqual(['a', 'b', 'unknown']);
  });
});

describe('editionTabs', () => {
  it('媒体名と状態を出し、同じ媒体が複数ある時だけ見分け用の副題を付ける', () => {
    const ordered = [
      edition('manga', { mediaType: 'manga', status: 'plan' }),
      edition('a1', { title: '本好きの下剋上 第2部' }),
      edition('a2', { title: '本好きの下剋上 第3部', status: 'done' }),
    ];
    const starts = new Map([['a1', { date: '2020-10-03', precision: 'day' as const }]]);
    expect(editionTabs(ordered, '本好きの下剋上', starts)).toEqual([
      { id: 'manga', media: '漫画', detail: '', status: '積み' },
      { id: 'a1', media: 'ANIME', detail: '2020年10月〜 ・ 第2部', status: '進行中' },
      { id: 'a2', media: 'ANIME', detail: '第3部', status: '完了' },
    ]);
  });
});

describe('initialEditionId', () => {
  const ordered = [{ id: 'web' }, { id: 'manga' }, { id: 'anime' }];

  it('URL で指定された Edition を最優先する', () => {
    expect(initialEditionId(ordered, 'manga', new Set(['anime']))).toBe('manga');
  });

  it('指定が無い・消えた時は今日放送のある Edition、それも無ければ先頭', () => {
    expect(initialEditionId(ordered, 'deleted', new Set(['anime']))).toBe('anime');
    expect(initialEditionId(ordered, undefined, new Set())).toBe('web');
    expect(initialEditionId([], undefined, new Set())).toBeUndefined();
  });
});

describe('workMediaTypes / suggestedMediaType', () => {
  it('作品にある媒体を決まった順に重複なしで返す', () => {
    const editions = [{ mediaType: 'anime' as const }, { mediaType: 'web-novel' as const }, { mediaType: 'anime' as const }];
    expect(workMediaTypes(editions)).toEqual(['anime', 'web-novel']);
    expect(workMediaTypes([])).toEqual([]);
  });

  it('追加の既定はまだ無い媒体の先頭、全部あれば WEB', () => {
    expect(suggestedMediaType([{ mediaType: 'web-novel' }])).toBe('light-novel');
    expect(suggestedMediaType([])).toBe('web-novel');
    expect(
      suggestedMediaType([
        { mediaType: 'web-novel' },
        { mediaType: 'light-novel' },
        { mediaType: 'manga' },
        { mediaType: 'anime' },
      ]),
    ).toBe('web-novel');
  });
});

describe('firstAiringTimeToday', () => {
  it('今日放送のある Edition の放送・配信のうち最初の時刻を返す', () => {
    const releases = [
      release({ id: 'late', editionId: 'e1', date: TODAY, time: '25:30' }),
      release({ id: 'early', editionId: 'e1', date: TODAY, time: '23:00' }),
      release({ id: 'stream', editionId: 'e1', kind: 'stream-start', date: TODAY, time: '22:00' }),
      release({ id: 'other', editionId: 'e2', date: TODAY, time: '9:00' }),
      release({ id: 'tomorrow', editionId: 'e1', date: '2026-10-16', time: '1:00' }),
      release({ id: 'volume', editionId: 'e1', kind: 'volume', date: TODAY, time: '0:00' }),
    ];
    expect(firstAiringTimeToday(releases, new Set(['e1']), TODAY)).toBe('22:00');
  });

  it('時刻の無い回しか無ければ undefined', () => {
    const releases = [release({ id: 'a', editionId: 'e1', date: TODAY })];
    expect(firstAiringTimeToday(releases, new Set(['e1']), TODAY)).toBeUndefined();
  });
});
