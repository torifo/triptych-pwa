import { describe, expect, it } from 'vitest';
import type { Edition, Release } from '../db/schema';
import {
  buildReleaseDate,
  compareReleases,
  dedupeResults,
  directSourceRef,
  groupByDate,
  groupByMonth,
  groupEditionReleases,
  isSafeHttpUrl,
  monthlyUndecided,
  parseTimeInput,
  registeredSourceKeys,
  releaseKindOptions,
  splitByPrecision,
  splitDateFields,
  timeMinutes,
  trackedReleases,
  unseenReleases,
  upcomingReleases,
  yearlyUndecided,
} from './release-view';

const TODAY = '2026-10-13';

function release(over: Partial<Release> & Pick<Release, 'id' | 'date'>): Release {
  return {
    editionId: 'e1',
    kind: 'volume',
    label: over.id,
    precision: 'day',
    dateHistory: [],
    source: 'anilist',
    createdAt: '2026-10-01T00:00:00.000Z',
    updatedAt: '2026-10-01T00:00:00.000Z',
    ...over,
  };
}

function edition(id: string, status: Edition['status'], mediaType: Edition['mediaType'] = 'anime'): Edition {
  return {
    id,
    workId: 'w1',
    mediaType,
    title: id,
    sourceRefs: [],
    status,
    linkSource: 'manual',
    createdAt: '2026-10-01T00:00:00.000Z',
    updatedAt: '2026-10-01T00:00:00.000Z',
  };
}

const editionMap = new Map(
  [
    edition('e1', 'active'),
    edition('plan', 'plan'),
    edition('paused', 'paused'),
    edition('done', 'done'),
    edition('dropped', 'dropped'),
  ].map((e) => [e.id, e]),
);

describe('compareReleases', () => {
  it('同じ放送日では 25:30 が 23:00 より後ろ、時刻なしが最後', () => {
    const noTime = release({ id: 'a', date: '2026-10-16' });
    const late = release({ id: 'b', date: '2026-10-16', time: '25:30' });
    const evening = release({ id: 'c', date: '2026-10-16', time: '23:00' });
    expect([noTime, late, evening].sort(compareReleases).map((r) => r.id)).toEqual(['c', 'b', 'a']);
  });

  it('日付が違えば日付が優先される', () => {
    const a = release({ id: 'a', date: '2026-10-17', time: '01:00' });
    const b = release({ id: 'b', date: '2026-10-16', time: '29:00' });
    expect([a, b].sort(compareReleases).map((r) => r.id)).toEqual(['b', 'a']);
  });

  it('timeMinutes は不正値を末尾扱いにする', () => {
    expect(timeMinutes('25:30')).toBe(25 * 60 + 30);
    expect(timeMinutes(undefined)).toBe(Number.POSITIVE_INFINITY);
    expect(timeMinutes('abc')).toBe(Number.POSITIVE_INFINITY);
  });
});

describe('Home: 直近7日', () => {
  it('追跡中・day 精度・今日〜7日後だけを日付順に返す', () => {
    const releases = [
      release({ id: 'today', date: TODAY }),
      release({ id: 'plus7', date: '2026-10-20' }),
      release({ id: 'plus8', date: '2026-10-21' }),
      release({ id: 'yesterday', date: '2026-10-12' }),
      release({ id: 'month', date: '2026-10-01', precision: 'month' }),
      release({ id: 'year', date: '2026-01-01', precision: 'year' }),
      release({ id: 'done', date: '2026-10-15', editionId: 'done' }),
      release({ id: 'dropped', date: '2026-10-15', editionId: 'dropped' }),
      release({ id: 'plan', date: '2026-10-15', editionId: 'plan' }),
      release({ id: 'paused', date: '2026-10-14', editionId: 'paused' }),
      release({ id: 'orphan', date: '2026-10-14', editionId: 'missing' }),
    ];
    expect(upcomingReleases(releases, editionMap, TODAY, 7).map((r) => r.id)).toEqual([
      'today',
      'paused',
      'plan',
      'plus7',
    ]);
  });

  it('precision 未設定は day とみなす', () => {
    const legacy = { ...release({ id: 'legacy', date: '2026-10-14' }), precision: undefined };
    expect(upcomingReleases([legacy], editionMap, TODAY, 7)).toHaveLength(1);
  });

  it('JST の朝5時区切りの放送（25:30）は放送日のまま並ぶ', () => {
    const releases = [
      release({ id: 'late', date: '2026-10-16', time: '25:30' }),
      release({ id: 'early', date: '2026-10-16', time: '09:00' }),
    ];
    expect(upcomingReleases(releases, editionMap, TODAY, 7).map((r) => r.id)).toEqual(['early', 'late']);
  });
});

describe('Home: 新着', () => {
  it('追跡中 Edition の seenAt 未設定だけ（過去日でも seenAt があれば除く）', () => {
    const releases = [
      release({ id: 'new', date: '2026-10-20' }),
      release({ id: 'seen', date: '2026-10-20', seenAt: '2026-10-13T00:00:00.000Z' }),
      release({ id: 'done', date: '2026-10-20', editionId: 'done' }),
      release({ id: 'plan', date: '2026-10-18', editionId: 'plan' }),
    ];
    expect(unseenReleases(releases, editionMap).map((r) => r.id)).toEqual(['plan', 'new']);
  });
});

describe('Calendar', () => {
  const releases = [
    release({ id: 'd1', date: '2026-10-16', time: '25:30' }),
    release({ id: 'd2', date: '2026-10-16', time: '22:00' }),
    release({ id: 'm1', date: '2026-10-01', precision: 'month' }),
    release({ id: 'm2', date: '2026-11-01', precision: 'month' }),
    release({ id: 'y1', date: '2027-01-01', precision: 'year' }),
    release({ id: 'y0', date: '2025-01-01', precision: 'year' }),
    release({ id: 'x', date: '2026-10-16', editionId: 'done' }),
  ];
  const split = splitByPrecision(trackedReleases(releases, editionMap));

  it('精度ごとに分け、追跡外を除く', () => {
    expect(split.days.map((r) => r.id)).toEqual(['d2', 'd1']);
    expect(split.months.map((r) => r.id)).toEqual(['m1', 'm2']);
    expect(split.years.map((r) => r.id)).toEqual(['y0', 'y1']);
  });

  it('day 精度は日付セルに入り、同日は時刻順', () => {
    expect(groupByDate(split.days).get('2026-10-16')?.map((r) => r.id)).toEqual(['d2', 'd1']);
  });

  it('month 精度はその月の「日付未定」だけ', () => {
    expect(monthlyUndecided(split.months, 2026, 10).map((r) => r.id)).toEqual(['m1']);
    expect(monthlyUndecided(split.months, 2026, 11).map((r) => r.id)).toEqual(['m2']);
    expect(monthlyUndecided(split.months, 2026, 9)).toEqual([]);
  });

  it('year 精度は過ぎた年を除いて時期未定に出す', () => {
    expect(yearlyUndecided(split.years, TODAY).map((r) => r.id)).toEqual(['y1']);
  });

  it('month 精度を月ごとに束ねる', () => {
    expect(groupByMonth(split.months).map((g) => [g.key, g.year, g.month, g.releases.length])).toEqual([
      ['2026-10', 2026, 10, 1],
      ['2026-11', 2026, 11, 1],
    ]);
  });
});

describe('WorkDetail: Edition の Release 一覧', () => {
  const releases = [
    release({ id: 'ep-future', date: '2026-10-20', kind: 'episode' }),
    release({ id: 'stream-future', date: '2026-10-14', kind: 'stream-start' }),
    release({ id: 'season', date: '2026-10-13', kind: 'season-start' }),
    release({ id: 'ep-past', date: '2026-10-06', kind: 'episode' }),
    release({ id: 'edge', date: '2026-09-13', kind: 'episode' }), // ちょうど 30 日前
    release({ id: 'old', date: '2026-09-12', kind: 'episode' }), // 31 日前
    release({ id: 'older', date: '2026-01-01', kind: 'episode' }),
    release({ id: 'month-now', date: '2026-10-01', precision: 'month', kind: 'volume' }),
    release({ id: 'month-past', date: '2026-09-01', precision: 'month', kind: 'volume' }),
    release({ id: 'year-now', date: '2026-01-01', precision: 'year', kind: 'volume' }),
    release({ id: 'year-past', date: '2025-01-01', precision: 'year', kind: 'volume' }),
  ];

  it('アニメ面は放送・配信の今後分を上に分け、過去分は直近30日だけ新しい順に出す', () => {
    const groups = groupEditionReleases(releases, 'anime', TODAY);
    expect(groups.broadcast.map((r) => r.id)).toEqual(['stream-future', 'ep-future']);
    expect(groups.upcoming.map((r) => r.id)).toEqual(['year-now', 'month-now', 'season']);
    expect(groups.recent.map((r) => r.id)).toEqual(['ep-past', 'edge', 'month-past']);
    expect(groups.hiddenCount).toBe(3); // old / older / year-past
  });

  it('アニメ以外は broadcast を作らず、予定にまとめる', () => {
    const groups = groupEditionReleases(releases, 'manga', TODAY);
    expect(groups.broadcast).toEqual([]);
    expect(groups.upcoming.map((r) => r.id)).toEqual([
      'year-now',
      'month-now',
      'season',
      'stream-future',
      'ep-future',
    ]);
  });

  it('日付が壊れた Release は消せるよう予定側に残す', () => {
    const broken = release({ id: 'broken', date: 'not-a-date' });
    expect(groupEditionReleases([broken], 'manga', TODAY).upcoming.map((r) => r.id)).toEqual(['broken']);
  });

  it('編集中の種類が範囲外でも選択肢に残す', () => {
    expect(releaseKindOptions(['volume'])).toEqual(['volume']);
    expect(releaseKindOptions(['volume'], 'episode')).toEqual(['volume', 'episode']);
    expect(releaseKindOptions(['volume'], 'volume')).toEqual(['volume']);
  });
});

describe('手動 Release フォームの入力', () => {
  it('精度に応じて日付を組み立てる（month/year は月初・年初）', () => {
    expect(buildReleaseDate('day', { day: '2026-10-16', year: '', month: '1' })).toBe('2026-10-16');
    expect(buildReleaseDate('month', { day: '', year: '2026', month: '8' })).toBe('2026-08-01');
    expect(buildReleaseDate('year', { day: '', year: '2027', month: '8' })).toBe('2027-01-01');
  });

  it('不正な日付は null', () => {
    expect(buildReleaseDate('day', { day: '', year: '2026', month: '1' })).toBeNull();
    expect(buildReleaseDate('day', { day: '2026-02-30', year: '2026', month: '1' })).toBeNull();
    expect(buildReleaseDate('month', { day: '', year: '26', month: '8' })).toBeNull();
    expect(buildReleaseDate('month', { day: '', year: '2026', month: '13' })).toBeNull();
    expect(buildReleaseDate('year', { day: '', year: '', month: '1' })).toBeNull();
  });

  it('日付を入力欄の値に分解する', () => {
    expect(splitDateFields('2026-08-01')).toEqual({ day: '2026-08-01', year: '2026', month: '8' });
    expect(splitDateFields('bad')).toBeNull();
  });

  it('時刻は 25:30 形式（0〜29 時）。空欄は時刻なし、全角も受ける', () => {
    expect(parseTimeInput('')).toEqual({ ok: true, value: undefined });
    expect(parseTimeInput('  ')).toEqual({ ok: true, value: undefined });
    expect(parseTimeInput('25:30')).toEqual({ ok: true, value: '25:30' });
    expect(parseTimeInput('5:05')).toEqual({ ok: true, value: '05:05' });
    expect(parseTimeInput('２５：３０')).toEqual({ ok: true, value: '25:30' });
    expect(parseTimeInput('30:00')).toEqual({ ok: false });
    expect(parseTimeInput('12:60')).toEqual({ ok: false });
    expect(parseTimeInput('1230')).toEqual({ ok: false });
  });
});

describe('ソース', () => {
  it('どれかの Edition にある (source, externalId) を登録済みとして集める', () => {
    const a = {
      ...edition('a', 'plan'),
      sourceRefs: [
        { source: 'narou' as const, externalId: 'n4830bu', url: 'https://ncode.syosetu.com/n4830bu/' },
      ],
    };
    const b = {
      ...edition('b', 'plan'),
      sourceRefs: [{ source: 'anilist' as const, externalId: '1', url: 'https://anilist.co/anime/1' }],
    };
    const keys = registeredSourceKeys([a, b]);
    expect(keys.has('narou:n4830bu')).toBe(true);
    expect(keys.has('anilist:1')).toBe(true);
    expect(keys.has('anilist:n4830bu')).toBe(false);
  });

  it('貼り付けの解決は先頭が direct の時だけ sourceRef にする', () => {
    const direct = { source: 'narou' as const, externalId: 'n1', url: 'https://x/n1', direct: true };
    const other = { source: 'anilist' as const, externalId: '2', url: 'https://x/2' };
    expect(directSourceRef([direct, other])).toEqual({
      source: 'narou',
      externalId: 'n1',
      url: 'https://x/n1',
    });
    expect(directSourceRef([other, direct])).toBeNull();
    expect(directSourceRef([{ ...other, direct: false }])).toBeNull();
    expect(directSourceRef([])).toBeNull();
  });

  it('外部リンクは http/https だけ', () => {
    expect(isSafeHttpUrl('https://anilist.co/anime/1')).toBe(true);
    expect(isSafeHttpUrl('http://localhost:8000/x')).toBe(true);
    expect(isSafeHttpUrl('javascript:alert(1)')).toBe(false);
    expect(isSafeHttpUrl('')).toBe(false);
  });

  it('検索結果の重複は先頭を残す', () => {
    const rows = [
      { source: 'ndl', externalId: '1', mediaType: 'manga', title: 'a' },
      { source: 'ndl', externalId: '1', mediaType: 'manga', title: 'b' },
      { source: 'ndl', externalId: '1', mediaType: 'light-novel', title: 'c' },
    ];
    expect(dedupeResults(rows).map((r) => r.title)).toEqual(['a', 'c']);
  });
});
