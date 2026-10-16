import { describe, expect, it } from 'vitest';
import type { CatalogEventKind } from '../catalog/types';
import type { MediaType } from '../db/schema';
import {
  CATEGORY_LABELS,
  categoryCounts,
  categoryOfFilter,
  countsByDate,
  daySections,
  emptyCounts,
  eventCategory,
  filterCounts,
  nonZeroCounts,
  releaseCategory,
} from './category';
import { MEDIA_FILTERS, catalogKindsFor, matchesMediaFilter, type MediaFilter } from './view';

const MEDIA: MediaType[] = ['anime', 'light-novel', 'manga', 'web-novel'];
const FILTERS: Exclude<MediaFilter, 'all'>[] = ['anime', 'book', 'web'];
const KINDS: CatalogEventKind[] = ['anime', 'book'];

const editions = new Map([
  ['a', { mediaType: 'anime' as const }],
  ['l', { mediaType: 'light-novel' as const }],
  ['m', { mediaType: 'manga' as const }],
  ['w', { mediaType: 'web-novel' as const }],
]);

const rel = (id: string, editionId: string, date = '2026-10-16') => ({ id, editionId, date });
const ev = (
  clusterId: string,
  kind: CatalogEventKind,
  extra: { time?: string | null; popularity?: number | null; date?: string } = {},
) => ({ clusterId, kind, time: extra.time ?? null, popularity: extra.popularity ?? null, date: extra.date ?? '2026-10-16' });

describe('分類', () => {
  it('Library: アニメは番組、Web 小説は Web、文庫・漫画（と媒体不明）は発売', () => {
    expect(releaseCategory('anime')).toBe('program');
    expect(releaseCategory('light-novel')).toBe('release');
    expect(releaseCategory('manga')).toBe('release');
    expect(releaseCategory('web-novel')).toBe('web');
    expect(releaseCategory(undefined)).toBe('release');
  });

  it('カタログ: アニメは番組、本は発売', () => {
    expect(eventCategory({ kind: 'anime' })).toBe('program');
    expect(eventCategory({ kind: 'book' })).toBe('release');
  });

  it('タブの絞り込みと段の分類が食い違わない（Library もカタログも）', () => {
    for (const filter of FILTERS) {
      const category = categoryOfFilter(filter);
      for (const media of MEDIA) {
        expect(matchesMediaFilter(media, filter)).toBe(releaseCategory(media) === category);
      }
      for (const kind of KINDS) {
        expect(catalogKindsFor(filter).includes(kind)).toBe(eventCategory({ kind }) === category);
      }
    }
    expect(categoryOfFilter('all')).toBeNull();
  });

  it('タブのラベルは段の見出しと同じ言葉', () => {
    expect(MEDIA_FILTERS.map((item) => item.label)).toEqual(['すべて', '番組', '発売', 'Web']);
    for (const item of MEDIA_FILTERS) {
      if (item.category) expect(item.label).toBe(CATEGORY_LABELS[item.category]);
    }
  });
});

describe('daySections', () => {
  it('番組 → 発売 → Web の順に分け、空の段は出さない。件数は Library とカタログの合計', () => {
    const releases = [rel('1', 'w'), rel('2', 'm'), rel('3', 'a'), rel('4', 'l')];
    const events = [ev('b1', 'book'), ev('a1', 'anime', { time: '21:00' })];
    const sections = daySections(releases, events, editions);
    expect(sections.map((s) => s.category)).toEqual(['program', 'release', 'web']);
    expect(sections.map((s) => s.count)).toEqual([2, 3, 1]);
    expect(sections[1].releases.map((r) => r.id)).toEqual(['2', '4']);
    expect(sections[1].events.map((e) => e.clusterId)).toEqual(['b1']);

    const onlyBooks = daySections([rel('2', 'm')], [ev('b1', 'book')], editions);
    expect(onlyBooks.map((s) => s.category)).toEqual(['release']);
    expect(daySections([], [], editions)).toEqual([]);
  });

  it('Library の並びは保つ（呼ぶ側の日付・時刻順）', () => {
    const releases = [rel('x', 'a', '2026-10-01'), rel('y', 'a', '2026-10-03'), rel('z', 'a', '2026-10-02')];
    expect(daySections(releases, [], editions)[0].releases.map((r) => r.id)).toEqual(['x', 'y', 'z']);
  });

  it('番組のカタログは時刻順（時刻の無いものは入力の順で後ろ）、発売のカタログは人気順', () => {
    const events = [
      ev('late', 'anime', { time: '25:30' }),
      ev('none1', 'anime'),
      ev('early', 'anime', { time: '18:00' }),
      ev('none2', 'anime'),
      ev('pop10', 'book', { popularity: 10 }),
      ev('popNull', 'book'),
      ev('pop90', 'book', { popularity: 90 }),
    ];
    const [program, release] = daySections([], events, editions);
    expect(program.events.map((e) => e.clusterId)).toEqual(['early', 'late', 'none1', 'none2']);
    expect(release.events.map((e) => e.clusterId)).toEqual(['pop90', 'pop10', 'popNull']);
  });
});

describe('件数', () => {
  it('分類ごとに数える', () => {
    const counts = categoryCounts([rel('1', 'a'), rel('2', 'm'), rel('3', 'w'), rel('4', 'l')], [ev('b', 'book'), ev('c', 'anime')], editions);
    expect(counts).toEqual({ program: 2, release: 3, web: 1 });
    expect(categoryCounts([], [], editions)).toEqual(emptyCounts());
  });

  it('日付ごとに数える（月の升目）', () => {
    const map = countsByDate(
      [rel('1', 'a', '2026-10-01'), rel('2', 'm', '2026-10-01'), rel('3', 'w', '2026-10-02')],
      [ev('b', 'book', { date: '2026-10-02' }), ev('c', 'anime', { date: '2026-10-01' })],
      editions,
    );
    expect(map.get('2026-10-01')).toEqual({ program: 2, release: 1, web: 0 });
    expect(map.get('2026-10-02')).toEqual({ program: 0, release: 1, web: 1 });
    expect(map.get('2026-10-03')).toBeUndefined();
  });

  it('升目には 0 の分類を出さない', () => {
    expect(nonZeroCounts({ program: 17, release: 0, web: 2 })).toEqual([
      { category: 'program', count: 17 },
      { category: 'web', count: 2 },
    ]);
    expect(nonZeroCounts(undefined)).toEqual([]);
    expect(nonZeroCounts(emptyCounts())).toEqual([]);
  });

  it('タブの件数（すべては合計）', () => {
    expect(filterCounts({ program: 17, release: 9, web: 2 })).toEqual({ all: 28, anime: 17, book: 9, web: 2 });
  });
});
