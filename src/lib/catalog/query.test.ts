import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  catalogListHash,
  createDebouncer,
  hasActiveFilter,
  parseCatalogQuery,
  recalledListHash,
  rememberListHash,
  serializeCatalogQuery,
} from './query';

describe('parseCatalogQuery', () => {
  it('reads every field', () => {
    expect(
      parseCatalogQuery({
        q: ' 薬屋 ',
        kind: 'web-novel',
        has: 'anime,novel',
        status: 'RELEASING',
        year: '2026',
        season: 'FALL',
        sort: 'upcoming',
        page: '3',
      }),
    ).toEqual({
      q: '薬屋',
      kind: 'web-novel',
      has: ['anime', 'novel'],
      status: 'RELEASING',
      year: 2026,
      season: 'FALL',
      sort: 'upcoming',
      page: 3,
    });
  });

  it('drops invalid values', () => {
    expect(
      parseCatalogQuery({ kind: 'movie', has: 'x,y', status: 'DONE', year: '26', season: 'AUTUMN', sort: 'random', page: '0' }),
    ).toEqual({});
    expect(parseCatalogQuery({ has: 'anime,bogus,anime' })).toEqual({ has: ['anime'] });
    expect(parseCatalogQuery({ page: 'abc', year: '2026abc' })).toEqual({});
  });
});

describe('serializeCatalogQuery / catalogListHash', () => {
  it('omits defaults and the page size', () => {
    expect(serializeCatalogQuery({ sort: 'popularity', page: 1, per: 30 })).toEqual({});
    expect(serializeCatalogQuery({ q: '転スラ', sort: 'recent', page: 2, per: 30 })).toEqual({
      q: '転スラ',
      sort: 'recent',
      page: '2',
    });
  });

  it('round-trips through the hash query', () => {
    const q = { q: '本好き', kind: 'novel' as const, has: ['anime' as const], status: 'FINISHED' as const, year: 2022, season: 'SPRING' as const, sort: 'title' as const, page: 4 };
    const hash = catalogListHash(q);
    expect(hash.startsWith('#/catalog?')).toBe(true);
    const search = new URLSearchParams(hash.slice(hash.indexOf('?') + 1));
    expect(parseCatalogQuery(Object.fromEntries(search))).toEqual(q);
  });

  it('is the bare path with no conditions', () => {
    expect(catalogListHash({})).toBe('#/catalog');
    expect(catalogListHash({ sort: 'popularity', page: 1 })).toBe('#/catalog');
  });
});

describe('hasActiveFilter', () => {
  it('ignores sort and page', () => {
    expect(hasActiveFilter({ sort: 'title', page: 3 })).toBe(false);
    expect(hasActiveFilter({ has: [] })).toBe(false);
    expect(hasActiveFilter({ q: 'a' })).toBe(true);
    expect(hasActiveFilter({ year: 2020 })).toBe(true);
    expect(hasActiveFilter({ has: ['anime'] })).toBe(true);
  });
});

describe('list hash memory', () => {
  it('remembers the last list URL', () => {
    rememberListHash('#/catalog?kind=anime');
    expect(recalledListHash()).toBe('#/catalog?kind=anime');
    rememberListHash('#/catalog');
  });
});

describe('createDebouncer', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('runs only the last call, 300ms after input stops', () => {
    const d = createDebouncer(300);
    const calls: string[] = [];
    d.run(() => calls.push('a'));
    vi.advanceTimersByTime(200);
    d.run(() => calls.push('b'));
    vi.advanceTimersByTime(299);
    expect(calls).toEqual([]);
    vi.advanceTimersByTime(1);
    expect(calls).toEqual(['b']);
  });

  it('can flush or cancel what is waiting', () => {
    const d = createDebouncer(300);
    const calls: string[] = [];
    d.run(() => calls.push('a'));
    d.flush();
    expect(calls).toEqual(['a']);
    d.run(() => calls.push('b'));
    d.cancel();
    vi.advanceTimersByTime(1000);
    expect(calls).toEqual(['a']);
    d.flush();
    expect(calls).toEqual(['a']);
  });
});
