import { describe, expect, it } from 'vitest';
import type { Release } from '../db/schema';
import {
  buildEditionReleaseView,
  startedStreamServices,
  streamEntries,
  streamServiceName,
} from './release-history';
import { groupEditionReleases } from './release-view';

const TODAY = '2026-10-13';

function release(over: Partial<Release> & Pick<Release, 'id' | 'date'>): Release {
  return {
    editionId: 'e1',
    kind: 'volume',
    label: over.id,
    precision: 'day',
    dateHistory: [],
    source: 'ndl',
    createdAt: '2026-10-01T00:00:00.000Z',
    updatedAt: '2026-10-01T00:00:00.000Z',
    ...over,
  };
}

const ids = (list: readonly { id: string }[]) => list.map((r) => r.id);

describe('streamServiceName', () => {
  it('collector の「<サービス> 配信」からサービス名を取り出す', () => {
    expect(streamServiceName('Netflix 配信')).toBe('Netflix');
    expect(streamServiceName('ABEMA 配信')).toBe('ABEMA');
    expect(streamServiceName('dアニメストア配信')).toBe('dアニメストア');
  });

  it('接尾辞のない手動ラベルはそのまま、空になる場合は元のラベル', () => {
    expect(streamServiceName('Netflix')).toBe('Netflix');
    expect(streamServiceName('  U-NEXT  ')).toBe('U-NEXT');
    expect(streamServiceName('配信')).toBe('配信');
    expect(streamServiceName('')).toBe('');
  });
});

describe('streamEntries / startedStreamServices', () => {
  const releases = [
    release({ id: 'abema', editionId: 'e1', kind: 'stream-start', label: 'ABEMA 配信', date: '2026-10-17' }),
    release({ id: 'netflix', editionId: 'e1', kind: 'stream-start', label: 'Netflix 配信', date: '2026-10-02' }),
    release({ id: 'prime', editionId: 'e1', kind: 'stream-start', label: 'Prime Video 配信', date: '2026-09-01' }),
    release({ id: 'dup', editionId: 'e1', kind: 'stream-start', label: 'Netflix 配信', date: '2026-10-05' }),
    release({ id: 'other', editionId: 'e2', kind: 'stream-start', label: 'U-NEXT 配信', date: '2026-10-01' }),
    release({ id: 'ep', editionId: 'e1', kind: 'episode', label: '第3話', date: '2026-10-20' }),
  ];

  it('配信開始だけを日付順に並べ、始まっているかを判定する（今日始まるものは予定）', () => {
    const entries = streamEntries(
      [...releases, release({ id: 'today', kind: 'stream-start', label: 'dアニメ 配信', date: TODAY })],
      TODAY,
    );
    expect(entries.map((e) => [e.release.id, e.started])).toEqual([
      ['prime', true],
      ['other', true],
      ['netflix', true],
      ['dup', true],
      ['today', false],
      ['abema', false],
    ]);
  });

  it('始まっているサービスを Edition ごとに重複なしで返す', () => {
    const map = startedStreamServices(releases, TODAY);
    expect(map.get('e1')).toEqual(['Prime Video', 'Netflix']);
    expect(map.get('e2')).toEqual(['U-NEXT']);
    expect(map.has('e3')).toBe(false);
  });
});

describe('buildEditionReleaseView', () => {
  const volumes = [
    release({ id: 'v-new', date: '2026-10-05' }),
    release({ id: 'v-may', date: '2026-05-10' }), // 本好き文庫の最新巻のように 30 日より前
    release({ id: 'v-old', date: '2025-01-10' }),
    release({ id: 'v-oldest', date: '2020-01-10' }),
    release({ id: 'v-next', date: '2026-11-10' }),
    release({ id: 'v-month-past', date: '2025-03-01', precision: 'month' }),
    release({ id: 'v-year-past', date: '2019-01-01', precision: 'year' }),
  ];

  it('30 日より前の過去分を新しい順で older に返す', () => {
    const view = buildEditionReleaseView(volumes, 'light-novel', TODAY);
    expect(ids(view.upcoming)).toEqual(['v-next']);
    expect(ids(view.recent)).toEqual(['v-new']);
    expect(ids(view.older)).toEqual(['v-may', 'v-month-past', 'v-old', 'v-oldest', 'v-year-past']);
    expect(view.nextEpisodes).toEqual([]);
    expect(view.streams).toEqual([]);
  });

  it('older の件数は groupEditionReleases の hiddenCount と一致し、他の一覧と重ならない', () => {
    const view = buildEditionReleaseView(volumes, 'manga', TODAY);
    const groups = groupEditionReleases(volumes, 'manga', TODAY);
    expect(view.older).toHaveLength(groups.hiddenCount);
    const all = [...view.upcoming, ...view.recent, ...view.older];
    expect(new Set(ids(all)).size).toBe(volumes.length);
  });

  it('最新巻が 30 日より前でも、older に残って開ける', () => {
    const only = [release({ id: 'v-may', date: '2026-05-10' })];
    const view = buildEditionReleaseView(only, 'light-novel', TODAY);
    expect([view.upcoming, view.recent]).toEqual([[], []]);
    expect(ids(view.older)).toEqual(['v-may']);
  });

  it('pastDays を変えると畳む境界が動く', () => {
    const view = buildEditionReleaseView(volumes, 'light-novel', TODAY, 200);
    expect(ids(view.recent)).toEqual(['v-new', 'v-may']);
    expect(ids(view.older)).toEqual(['v-month-past', 'v-old', 'v-oldest', 'v-year-past']);
  });

  describe('アニメ面', () => {
    const anime = [
      release({ id: 'ep-next', kind: 'episode', label: '第3話', date: '2026-10-16', time: '25:30' }),
      release({ id: 'stream-new', kind: 'stream-start', label: 'ABEMA 配信', date: '2026-10-17' }),
      release({ id: 'stream-started', kind: 'stream-start', label: 'Netflix 配信', date: '2026-10-02' }),
      release({ id: 'stream-ancient', kind: 'stream-start', label: 'Prime Video 配信', date: '2026-07-01' }),
      release({ id: 'ep-past', kind: 'episode', label: '第2話', date: '2026-10-09' }),
      release({ id: 'ep-old', kind: 'episode', label: '第1話', date: '2026-08-01' }),
      release({ id: 'season', kind: 'season-start', label: '第2期', date: '2026-07-04' }),
    ];

    it('次話を先頭に、配信開始は 30 日より前の分も含めてひとまとめに出す', () => {
      const view = buildEditionReleaseView(anime, 'anime', TODAY);
      expect(ids(view.nextEpisodes)).toEqual(['ep-next']);
      expect(view.streams.map((s) => [s.release.id, s.service, s.started])).toEqual([
        ['stream-ancient', 'Prime Video', true],
        ['stream-started', 'Netflix', true],
        ['stream-new', 'ABEMA', false],
      ]);
    });

    it('配信開始を recent / older / upcoming に二重に出さない', () => {
      const view = buildEditionReleaseView(anime, 'anime', TODAY);
      expect(ids(view.recent)).toEqual(['ep-past']);
      expect(ids(view.older)).toEqual(['ep-old', 'season']);
      expect(ids(view.upcoming)).toEqual([]);
      const listed = [
        ...view.nextEpisodes,
        ...view.streams.map((s) => s.release),
        ...view.upcoming,
        ...view.recent,
        ...view.older,
      ];
      expect(ids(listed).sort()).toEqual(ids(anime).sort());
    });

    it('配信開始が無ければ streams は空で、次話だけが残る', () => {
      const view = buildEditionReleaseView(anime.filter((r) => r.kind !== 'stream-start'), 'anime', TODAY);
      expect(ids(view.nextEpisodes)).toEqual(['ep-next']);
      expect(view.streams).toEqual([]);
    });

    it('日付が壊れた配信開始も消せるよう streams に残す', () => {
      const broken = release({ id: 'broken', kind: 'stream-start', label: 'Netflix 配信', date: 'not-a-date' });
      const view = buildEditionReleaseView([broken], 'anime', TODAY);
      expect(view.streams.map((s) => [s.release.id, s.started])).toEqual([['broken', false]]);
      expect(view.older).toEqual([]);
    });
  });

  it('アニメ以外では stream-start を特別扱いしない', () => {
    const odd = [release({ id: 'stream', kind: 'stream-start', label: 'Netflix 配信', date: '2026-01-01' })];
    const view = buildEditionReleaseView(odd, 'manga', TODAY);
    expect(view.streams).toEqual([]);
    expect(ids(view.older)).toEqual(['stream']);
  });
});
