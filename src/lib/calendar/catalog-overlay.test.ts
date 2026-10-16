import { describe, expect, it, vi } from 'vitest';
import { ApiError } from '../api';
import { DETAIL_NOT_BUILT } from '../catalog/api';
import type { CatalogEvent, CatalogEventPage, CatalogEventQuery } from '../catalog/types';
import {
  EventsShapeError,
  dayEmptyNote,
  describeOverlayError,
  eventCover,
  eventHref,
  eventKindWord,
  eventLabel,
  fetchEventsRange,
  foldEvents,
  groupEventsByDate,
  libraryKeys,
  loadOverlayEvents,
  normalizeEventPage,
  overlayEvents,
  overlayNote,
} from './catalog-overlay';

function ev(over: Partial<CatalogEvent> & Pick<CatalogEvent, 'clusterId' | 'date'>): CatalogEvent {
  return {
    entryId: `anilist:${over.clusterId}`,
    title: `作品${over.clusterId}`,
    kind: 'anime',
    event: 'season-start',
    label: '放送開始',
    time: null,
    cover: null,
    popularity: 0,
    ...over,
  };
}

describe('libraryKeys / overlayEvents', () => {
  const keys = libraryKeys(
    [{ title: '薬屋のひとりごと' }, { title: '  ' }],
    [
      {
        sourceRefs: [
          { source: 'anilist', externalId: '161645', url: '' },
          { source: 'syoboi', externalId: '7000', url: '' },
        ],
      },
    ],
  );

  it('同じ題名の Work（表記ゆれは正規化）と、AniList id が一致する Edition は重ねない', () => {
    const events = [
      ev({ clusterId: 'a', date: '2026-10-15', title: '薬屋の ひとりごと' }),
      ev({ clusterId: 'b', date: '2026-10-15', entryId: 'anilist:161645' }),
      ev({ clusterId: 'c', date: '2026-10-15', entryId: 'syoboi:7000' }),
      ev({ clusterId: 'd', date: '2026-10-15' }),
    ];
    expect(overlayEvents(events, keys, ['anime', 'book']).map((e) => e.clusterId)).toEqual(['c', 'd']);
  });

  it('絞り込みの種類だけ残す', () => {
    const events = [
      ev({ clusterId: 'a', date: '2026-10-15', kind: 'anime' }),
      ev({ clusterId: 'b', date: '2026-10-15', kind: 'book', event: 'release', label: '2巻' }),
    ];
    expect(overlayEvents(events, keys, ['book']).map((e) => e.clusterId)).toEqual(['b']);
    expect(overlayEvents(events, keys, [])).toEqual([]);
  });

  it('同じ作品の同じ日の同じ出来事は1つにする', () => {
    const events = [
      ev({ clusterId: 'a', date: '2026-10-15', entryId: 'anilist:1' }),
      ev({ clusterId: 'a', date: '2026-10-15', entryId: 'anilist:2' }),
      ev({ clusterId: 'a', date: '2026-10-16', entryId: 'anilist:1' }),
      ev({ clusterId: 'a', date: '2026-10-15', event: 'release', kind: 'book', label: '2巻' }),
    ];
    expect(overlayEvents(events, keys, ['anime', 'book'])).toHaveLength(3);
  });
});

describe('groupEventsByDate', () => {
  it('日ごとに、時刻のあるもの（時刻順）→ 時刻の無いもの（届いた順＝人気順）', () => {
    const events = [
      ev({ clusterId: 'pop', date: '2026-10-15', event: 'release', kind: 'book', label: '5巻' }),
      ev({ clusterId: 'late', date: '2026-10-15', event: 'episode', label: '第3話', time: '23:30' }),
      ev({ clusterId: 'less', date: '2026-10-15', event: 'season-start' }),
      ev({ clusterId: 'early', date: '2026-10-15', event: 'episode', label: '第8話', time: '01:30' }),
      ev({ clusterId: 'next', date: '2026-10-16' }),
    ];
    const map = groupEventsByDate(events);
    expect(map.get('2026-10-15')?.map((e) => e.clusterId)).toEqual(['early', 'late', 'pop', 'less']);
    expect(map.get('2026-10-16')?.map((e) => e.clusterId)).toEqual(['next']);
  });
});

describe('foldEvents', () => {
  const list = Array.from({ length: 8 }, (_, i) => i);

  it('上位5件と、残りの数。開いたら全部', () => {
    expect(foldEvents(list, false)).toEqual({ shown: [0, 1, 2, 3, 4], rest: 3 });
    expect(foldEvents(list, true)).toEqual({ shown: list, rest: 0 });
    expect(foldEvents(list.slice(0, 5), false)).toEqual({ shown: [0, 1, 2, 3, 4], rest: 0 });
  });
});

describe('行の文言', () => {
  it('右の種別（放送／放送開始／公開／発売）', () => {
    expect(eventKindWord('episode')).toBe('放送');
    expect(eventKindWord('season-start')).toBe('放送開始');
    expect(eventKindWord('movie')).toBe('公開');
    expect(eventKindWord('release')).toBe('発売');
  });

  it('label が空なら種別の言葉', () => {
    expect(eventLabel(ev({ clusterId: 'a', date: '2026-10-15', label: '第3話', event: 'episode' }))).toBe('第3話');
    expect(eventLabel(ev({ clusterId: 'a', date: '2026-10-15', label: '  ', event: 'movie' }))).toBe('公開');
  });

  it('表紙は http(s) だけ。媒体はアニメだけ分かる', () => {
    expect(eventCover(ev({ clusterId: 'a', date: '2026-10-15', cover: 'https://img/x.jpg' }))).toEqual({
      url: 'https://img/x.jpg',
      title: '作品a',
      mediaType: 'anime',
    });
    expect(eventCover(ev({ clusterId: 'b', date: '2026-10-15', cover: 'javascript:alert(1)', kind: 'book' }))).toEqual({
      url: undefined,
      title: '作品b',
      mediaType: undefined,
    });
  });

  it('カタログ詳細への道', () => {
    expect(eventHref({ clusterId: 'anilist:1' })).toBe('#/catalog/anilist%3A1');
  });
});

describe('normalizeEventPage', () => {
  it('形の崩れた行は落とし、time は HH:MM だけ残す', () => {
    const page = normalizeEventPage({
      items: [
        ev({ clusterId: 'a', date: '2026-10-15', time: '25:30' }),
        ev({ clusterId: 'b', date: '2026-10-15', time: 'soon' }),
        { clusterId: 'c', date: 'x' },
        null,
        ev({ clusterId: 'd', date: '2026-10-16', kind: 'web' as never }),
      ],
      truncated: true,
    });
    expect(page.items.map((e) => [e.clusterId, e.time])).toEqual([
      ['a', '25:30'],
      ['b', null],
    ]);
    expect(page.truncated).toBe(true);
  });

  it('items の無い応答は形の誤り', () => {
    expect(() => normalizeEventPage({ detail: 'x' })).toThrow(EventsShapeError);
    expect(() => normalizeEventPage(null)).toThrow(EventsShapeError);
  });
});

describe('fetchEventsRange', () => {
  function pageOf(items: CatalogEvent[], truncated = false): CatalogEventPage {
    return { items, truncated };
  }

  it('切れていなければ1回で終わる', async () => {
    const fetcher = vi.fn(async () => pageOf([ev({ clusterId: 'a', date: '2026-10-15' })]));
    const result = await fetchEventsRange(fetcher, { from: '2026-10-15', to: '2026-10-21', kinds: ['anime'] });
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(result).toEqual({ items: [ev({ clusterId: 'a', date: '2026-10-15' })], truncated: false });
  });

  it('上限で切れたら、最後の日から取り直して重ねずにつなぐ', async () => {
    const calls: CatalogEventQuery[] = [];
    const fetcher = vi.fn(async (q: CatalogEventQuery) => {
      calls.push(q);
      if (q.from === '2026-10-01') {
        return pageOf(
          [
            ev({ clusterId: 'a', date: '2026-10-01' }),
            ev({ clusterId: 'b', date: '2026-10-09' }),
          ],
          true,
        );
      }
      return pageOf([
        ev({ clusterId: 'b', date: '2026-10-09' }),
        ev({ clusterId: 'c', date: '2026-10-09' }),
        ev({ clusterId: 'd', date: '2026-10-30' }),
      ]);
    });
    const result = await fetchEventsRange(fetcher, { from: '2026-10-01', to: '2026-10-31', kinds: ['book'] });
    expect(calls.map((q) => [q.from, q.to, q.kinds])).toEqual([
      ['2026-10-01', '2026-10-31', ['book']],
      ['2026-10-09', '2026-10-31', ['book']],
    ]);
    expect(result.items.map((e) => e.clusterId)).toEqual(['a', 'b', 'c', 'd']);
    expect(result.truncated).toBe(false);
  });

  it('1日だけで上限を超えた時は、その日は切れたまま次の日へ進む', async () => {
    const fetcher = vi.fn(async (q: CatalogEventQuery) =>
      q.from === '2026-10-15'
        ? pageOf([ev({ clusterId: 'a', date: '2026-10-15' }), ev({ clusterId: 'b', date: '2026-10-15' })], true)
        : pageOf([ev({ clusterId: 'c', date: '2026-10-16' })]),
    );
    const result = await fetchEventsRange(fetcher, { from: '2026-10-15', to: '2026-10-21' });
    expect(fetcher.mock.calls.map(([q]) => q.from)).toEqual(['2026-10-15', '2026-10-16']);
    expect(result.items.map((e) => e.clusterId)).toEqual(['a', 'b', 'c']);
    expect(result.truncated).toBe(true);
  });

  it('取り直しの回数には上限がある', async () => {
    let day = 1;
    const fetcher = vi.fn(async () => {
      day += 1;
      return pageOf([ev({ clusterId: String(day), date: `2026-10-${String(day).padStart(2, '0')}` })], true);
    });
    const result = await fetchEventsRange(fetcher, { from: '2026-10-01', to: '2026-10-31' }, 3);
    expect(fetcher).toHaveBeenCalledTimes(3);
    expect(result.truncated).toBe(true);
  });

  it('失敗はそのまま投げる', async () => {
    const fetcher = vi.fn(async () => {
      throw new ApiError(404, 'x', DETAIL_NOT_BUILT);
    });
    await expect(fetchEventsRange(fetcher, { from: '2026-10-15', to: '2026-10-21' })).rejects.toBeInstanceOf(ApiError);
  });
});

describe('loadOverlayEvents', () => {
  it('種類が無ければ取りに行かない', async () => {
    const fetcher = vi.fn();
    expect(await loadOverlayEvents(fetcher, { from: '2026-10-01', to: '2026-10-31' }, [], true)).toEqual({
      items: [],
      truncated: false,
    });
    expect(fetcher).not.toHaveBeenCalled();
  });

  it('週は1回で取る', async () => {
    const fetcher = vi.fn(async (_q: CatalogEventQuery) => ({ items: [], truncated: false }));
    await loadOverlayEvents(fetcher, { from: '2026-10-15', to: '2026-10-21' }, ['anime', 'book'], false);
    expect(fetcher.mock.calls.map(([q]) => q.kinds)).toEqual([['anime', 'book']]);
  });

  it('月はアニメと本を別々に取り、日付→人気の順に合わせる', async () => {
    const b1 = ev({ clusterId: 'b1', date: '2026-10-02', kind: 'book', event: 'release', popularity: 80 });
    const b2 = ev({ clusterId: 'b2', date: '2026-10-03', kind: 'book', event: 'release', popularity: null });
    const fetcher = vi.fn(async (q: CatalogEventQuery) => {
      if (q.kinds?.[0] === 'anime') {
        return {
          items: [
            ev({ clusterId: 'a1', date: '2026-10-02', popularity: 50 }),
            ev({ clusterId: 'a2', date: '2026-10-05', popularity: 10 }),
          ],
          truncated: false,
        };
      }
      // 本: 10/03 は1日だけで上限に掛かる
      if (q.from === '2026-10-01') return { items: [b1, b2], truncated: true };
      if (q.from === '2026-10-03') return { items: [b2], truncated: true };
      return { items: [], truncated: false };
    });
    const result = await loadOverlayEvents(fetcher, { from: '2026-10-01', to: '2026-10-31' }, ['anime', 'book'], true);
    expect(fetcher.mock.calls.map(([q]) => [q.kinds, q.from])).toEqual([
      [['anime'], '2026-10-01'],
      [['book'], '2026-10-01'],
      [['book'], '2026-10-03'],
      [['book'], '2026-10-04'],
    ]);
    expect(result.items.map((e) => e.clusterId)).toEqual(['b1', 'a1', 'b2', 'a2']);
    expect(result.truncated).toBe(true);
  });
});

describe('describeOverlayError', () => {
  it('理由を短く', () => {
    expect(describeOverlayError(new ApiError(404, 'x', DETAIL_NOT_BUILT))).toBe('カタログ未作成');
    expect(describeOverlayError(new ApiError(404, 'x', 'Not Found'))).toBe('collector が未対応');
    expect(describeOverlayError(new ApiError(404, 'x'))).toBe('collector が未対応');
    expect(describeOverlayError(new ApiError(502, 'x'))).toBe('取得できませんでした（502）');
    expect(describeOverlayError(new EventsShapeError())).toBe('応答を読めませんでした');
    expect(describeOverlayError(new SyntaxError('bad json'))).toBe('応答を読めませんでした');
    expect(describeOverlayError(new TypeError('Failed to fetch'))).toBe('collector に接続できません');
  });
});

describe('overlayNote', () => {
  it('状態ごとの一言', () => {
    expect(overlayNote({ status: 'off' })).toBeNull();
    expect(overlayNote({ status: 'no-kinds' })).toBe('Web はカタログの対象外');
    expect(overlayNote({ status: 'loading' })).toBe('読み込み中…');
    expect(overlayNote({ status: 'error', reason: 'カタログ未作成' })).toBe('カタログ未作成');
    expect(overlayNote({ status: 'ready', count: 42, truncated: false })).toBe('42 件');
    expect(overlayNote({ status: 'ready', count: 0, truncated: false })).toBe('この期間は無し');
    expect(overlayNote({ status: 'ready', count: 600, truncated: true })).toBe('600 件（多いため一部）');
  });
});

describe('dayEmptyNote', () => {
  it('カタログを読めない時は「予定なし」と言わず、理由を出す', () => {
    expect(dayEmptyNote({ status: 'error', reason: 'collector に接続できません' })).toBe(
      'カタログの予定を出せません（collector に接続できません）',
    );
    expect(dayEmptyNote({ status: 'loading' })).toBe('読み込み中…');
  });

  it('読めた・重ねていない時は、ふつうに予定なし', () => {
    expect(dayEmptyNote({ status: 'ready', count: 0, truncated: false })).toBe('この日の予定はありません');
    expect(dayEmptyNote({ status: 'off' })).toBe('この日の予定はありません');
    expect(dayEmptyNote({ status: 'no-kinds' })).toBe('この日の予定はありません');
  });
});
