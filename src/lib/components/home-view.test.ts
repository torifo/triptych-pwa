import { describe, expect, it } from 'vitest';
import type { Edition, Release, Work } from '../db/schema';
import {
  buildCoverIndex,
  currentSeason,
  datelineLabel,
  episodeNumberOf,
  isAiringNow,
  onAirCount,
  todayItems,
  todaySubLabel,
  withoutToday,
} from './home-view';

function release(over: Partial<Release> & Pick<Release, 'id' | 'editionId' | 'date'>): Release {
  return {
    kind: 'episode',
    label: '第1話',
    dateHistory: [],
    source: 'anilist',
    createdAt: '2026-10-01T00:00:00.000Z',
    updatedAt: '2026-10-01T00:00:00.000Z',
    ...over,
  };
}

function edition(over: Partial<Edition> & Pick<Edition, 'id' | 'workId'>): Edition {
  return {
    mediaType: 'anime',
    title: '',
    sourceRefs: [],
    status: 'active',
    linkSource: 'manual',
    createdAt: '2026-10-01T00:00:00.000Z',
    updatedAt: '2026-10-01T00:00:00.000Z',
    ...over,
  };
}

const statuses = new Map<string, Pick<Edition, 'status'>>([
  ['anime', { status: 'active' }],
  ['anime2', { status: 'plan' }],
  ['manga', { status: 'active' }],
  ['done', { status: 'done' }],
]);

// 2026-10-15 14:00 JST
const NOON = new Date('2026-10-15T05:00:00Z');

describe('episodeNumberOf', () => {
  it('externalId の話数を優先し、無ければラベルの最後の「n話」', () => {
    expect(episodeNumberOf({ kind: 'episode', label: '第3話', externalId: '123:14' })).toBe(14);
    expect(episodeNumberOf({ kind: 'episode', label: '第2期 第5話' })).toBe(5);
    expect(episodeNumberOf({ kind: 'episode', label: '最終回' })).toBeUndefined();
    expect(episodeNumberOf({ kind: 'volume', label: '第12巻', externalId: '978:1' })).toBeUndefined();
  });
});

describe('todayItems', () => {
  it('放送は放送日、本は暦日で今日のものを、追跡中の Edition だけ時刻順に並べる', () => {
    const items = todayItems(
      [
        release({ id: 'late', editionId: 'anime', date: '2026-10-15', time: '25:30', externalId: '1:14' }),
        release({ id: 'early', editionId: 'anime2', date: '2026-10-15', time: '9:00' }),
        release({ id: 'vol', editionId: 'manga', date: '2026-10-15', kind: 'volume', label: '第12巻' }),
        release({ id: 'done', editionId: 'done', date: '2026-10-15', time: '20:00' }),
        release({ id: 'tomorrow', editionId: 'manga', date: '2026-10-16', kind: 'volume' }),
        release({ id: 'month', editionId: 'manga', date: '2026-10-01', kind: 'volume', precision: 'month' }),
      ],
      statuses,
      NOON,
    );
    expect(items.map((i) => i.release.id)).toEqual(['early', 'late', 'vol']);
    expect(items[1]).toMatchObject({ badge: '25:30', episode: 14 });
    expect(items[2]).toMatchObject({ badge: '発売', episode: undefined });
  });

  it('朝5時より前は、前日の放送日の番組を今日に数える（本は暦日のまま）', () => {
    // 2026-10-16 01:00 JST
    const night = new Date('2026-10-15T16:00:00Z');
    const items = todayItems(
      [
        release({ id: 'midnight', editionId: 'anime', date: '2026-10-15', time: '25:30' }),
        release({ id: 'next-day', editionId: 'anime2', date: '2026-10-16', time: '22:00' }),
        release({ id: 'vol', editionId: 'manga', date: '2026-10-16', kind: 'volume' }),
      ],
      statuses,
      night,
    );
    expect(items.map((i) => i.release.id)).toEqual(['midnight', 'vol']);
  });

  it('同じ Edition の放送・放送開始・配信は1つにまとめ、話数の分かる放送を表に出す', () => {
    const items = todayItems(
      [
        release({ id: 'netflix', editionId: 'anime', date: '2026-10-15', kind: 'stream-start', label: 'Netflix 配信', rolling: true }),
        release({ id: 'ep', editionId: 'anime', date: '2026-10-15', time: '24:00', label: '第1話' }),
        release({ id: 'start', editionId: 'anime', date: '2026-10-15', kind: 'season-start', label: '第2期' }),
        release({ id: 'prime', editionId: 'anime', date: '2026-10-15', kind: 'stream-start', label: 'Prime Video 配信' }),
        release({ id: 'netflix2', editionId: 'anime', date: '2026-10-15', kind: 'stream-start', label: 'Netflix 配信' }),
      ],
      statuses,
      NOON,
    );
    expect(items).toHaveLength(1);
    expect(items[0].release.id).toBe('ep');
    expect(items[0].releaseIds.sort()).toEqual(['ep', 'netflix', 'netflix2', 'prime', 'start']);
    expect(items[0].services).toEqual(['Netflix', 'Prime Video']);
    expect(items[0].episode).toBe(1);
  });

  it('次回の配信（rolling）だけの日は配信として出す', () => {
    const items = todayItems(
      [release({ id: 'r', editionId: 'anime', date: '2026-10-15', kind: 'stream-start', label: 'Netflix 配信', rolling: true })],
      statuses,
      NOON,
    );
    expect(items[0]).toMatchObject({ badge: '配信', services: ['Netflix'] });
  });
});

describe('todaySubLabel', () => {
  const base = { editionId: 'anime', releaseIds: [], badge: '', services: [] as string[] };

  it('これからの放送はカウントダウン、過ぎたら話数', () => {
    const r = release({ id: 'r', editionId: 'anime', date: '2026-10-15', time: '17:12' });
    expect(todaySubLabel({ ...base, release: r, episode: 14 }, NOON)).toEqual({ text: 'あと 3:12', upcoming: true });
    const after = new Date('2026-10-15T09:00:00Z');
    expect(todaySubLabel({ ...base, release: r, episode: 14 }, after)).toEqual({ text: '#14', upcoming: false });
  });

  it('話数が無ければ配信サービス、それも無ければラベル', () => {
    const stream = release({ id: 's', editionId: 'anime', date: '2026-10-15', kind: 'stream-start', label: 'Netflix 配信' });
    expect(todaySubLabel({ ...base, release: stream, services: ['Netflix', 'Hulu'] }, NOON).text).toBe('Netflix・Hulu');
    const vol = release({ id: 'v', editionId: 'manga', date: '2026-10-15', kind: 'volume', label: '第12巻' });
    expect(todaySubLabel({ ...base, release: vol }, NOON).text).toBe('第12巻');
  });
});

describe('isAiringNow', () => {
  it('放送の時刻から 30 分の間だけ', () => {
    const r = release({ id: 'r', editionId: 'anime', date: '2026-10-15', time: '14:00' });
    expect(isAiringNow(r, NOON)).toBe(true);
    expect(isAiringNow(r, new Date('2026-10-15T05:29:00Z'))).toBe(true);
    expect(isAiringNow(r, new Date('2026-10-15T05:30:00Z'))).toBe(false);
    expect(isAiringNow(r, new Date('2026-10-15T04:59:00Z'))).toBe(false);
    expect(isAiringNow({ ...r, time: undefined }, NOON)).toBe(false);
    expect(isAiringNow({ ...r, kind: 'stream-start' }, NOON)).toBe(false);
  });
});

describe('withoutToday', () => {
  it('今日の横並びにまとめた Release を除く', () => {
    const a = release({ id: 'a', editionId: 'anime', date: '2026-10-15' });
    const b = release({ id: 'b', editionId: 'anime', date: '2026-10-16' });
    const items = todayItems([a], statuses, NOON);
    expect(withoutToday([a, b], items).map((r) => r.id)).toEqual(['b']);
  });
});

describe('onAirCount', () => {
  it('放送日（朝5時区切り）で今日の放送・配信がある追跡中 Edition を数える', () => {
    const releases = [
      release({ id: 'a', editionId: 'anime', date: '2026-10-15', time: '25:30' }),
      release({ id: 'b', editionId: 'anime', date: '2026-10-15', kind: 'stream-start', rolling: true }),
      release({ id: 'c', editionId: 'manga', date: '2026-10-15', kind: 'volume' }),
    ];
    expect(onAirCount(releases, statuses, NOON)).toBe(1);
    // 2026-10-16 01:00 JST はまだ 10/15 の放送日
    expect(onAirCount(releases, statuses, new Date('2026-10-15T16:00:00Z'))).toBe(1);
    // 2026-10-16 05:00 JST から 10/16 の放送日
    expect(onAirCount(releases, statuses, new Date('2026-10-15T20:00:00Z'))).toBe(0);
  });
});

describe('buildCoverIndex', () => {
  const works: Pick<Work, 'id' | 'title' | 'coverEditionId'>[] = [{ id: 'w', title: '薬屋のひとりごと' }];

  it('Edition 自身の表紙を優先し、無ければ同じ作品の代表表紙を借りる', () => {
    const editions = [
      edition({ id: 'a', workId: 'w', mediaType: 'anime', coverUrl: 'https://x/anime.jpg' }),
      edition({ id: 'b', workId: 'w', mediaType: 'light-novel' }),
      edition({ id: 'c', workId: 'w', mediaType: 'manga', coverUrl: 'https://x/manga.jpg' }),
    ];
    const index = buildCoverIndex(editions, works);
    expect(index.get('a')).toEqual({ url: 'https://x/anime.jpg', title: '薬屋のひとりごと', mediaType: 'anime' });
    expect(index.get('b')).toEqual({ url: 'https://x/anime.jpg', title: '薬屋のひとりごと', mediaType: 'anime' });
    expect(index.get('c')?.url).toBe('https://x/manga.jpg');
  });

  it('作品のどこにも表紙が無ければ、その Edition の文字の表紙', () => {
    const index = buildCoverIndex([edition({ id: 'b', workId: 'w', mediaType: 'light-novel', title: '文庫版' })], works);
    expect(index.get('b')).toEqual({ url: undefined, title: '薬屋のひとりごと', mediaType: 'light-novel' });
  });

  it('Work が見つからなければ Edition の題名で組む', () => {
    const index = buildCoverIndex([edition({ id: 'x', workId: 'missing', title: '孤立した版' })], works);
    expect(index.get('x')?.title).toBe('孤立した版');
  });
});

describe('datelineLabel', () => {
  it('YYYY.MM.DD と英字の曜日', () => {
    expect(datelineLabel('2026-10-15')).toBe('2026.10.15 THU');
    expect(datelineLabel('2026-10')).toBe('2026-10');
  });
});

describe('currentSeason', () => {
  it('1-3 冬・4-6 春・7-9 夏・10-12 秋', () => {
    expect(currentSeason('2026-01-01')).toEqual({ year: 2026, season: 'WINTER' });
    expect(currentSeason('2026-03-31')).toEqual({ year: 2026, season: 'WINTER' });
    expect(currentSeason('2026-04-01')).toEqual({ year: 2026, season: 'SPRING' });
    expect(currentSeason('2026-09-30')).toEqual({ year: 2026, season: 'SUMMER' });
    expect(currentSeason('2026-10-15')).toEqual({ year: 2026, season: 'FALL' });
    expect(currentSeason('2026-12-31')).toEqual({ year: 2026, season: 'FALL' });
  });
});
