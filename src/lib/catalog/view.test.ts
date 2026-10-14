import { describe, expect, it } from 'vitest';
import { makeEntry } from './fixtures.test-util';
import {
  animeStatusLabel,
  entryCounts,
  formatEntryPeriod,
  formatJstDateTime,
  formatLabel,
  formatNext,
  latestHarvestAt,
  linkBadge,
  namesLine,
  yearRange,
} from './view';

describe('formatNext', () => {
  it('shows nothing without a date', () => {
    expect(formatNext({}, '2026-10-14')).toBeNull();
    expect(formatNext({ nextDate: null }, '2026-10-14')).toBeNull();
  });

  it('follows the precision', () => {
    expect(formatNext({ nextDate: '2026-10-16', nextPrecision: 'day' }, '2026-10-14')).toEqual({ date: '10/16(金)', label: null });
    expect(formatNext({ nextDate: '2026-12-01', nextPrecision: 'month' }, '2026-10-14')).toEqual({ date: '2026年12月', label: null });
    expect(formatNext({ nextDate: '2027-01-01', nextPrecision: 'year' }, '2026-10-14')).toEqual({ date: '2027年', label: null });
  });

  it('treats a missing precision as day and adds the year for another year', () => {
    expect(formatNext({ nextDate: '2027-01-05' }, '2026-10-14')?.date).toBe('2027/1/5(火)');
  });

  it('keeps the label', () => {
    expect(formatNext({ nextDate: '2026-10-16', nextPrecision: 'day', nextLabel: ' 第2期 ' }, '2026-10-14')?.label).toBe('第2期');
  });
});

describe('yearRange', () => {
  it('formats the range', () => {
    expect(yearRange(2019, 2024)).toBe('2019–2024');
    expect(yearRange(2019, 2019)).toBe('2019');
    expect(yearRange(2019, null)).toBe('2019');
    expect(yearRange(null, 2024)).toBe('2024');
    expect(yearRange(undefined, undefined)).toBe('');
  });
});

describe('animeStatusLabel', () => {
  it('translates known statuses and passes others through', () => {
    expect(animeStatusLabel('RELEASING')).toBe('放送中');
    expect(animeStatusLabel('NOT_YET_RELEASED')).toBe('放送予定');
    expect(animeStatusLabel('FINISHED')).toBe('放送済');
    expect(animeStatusLabel('???')).toBe('???');
    expect(animeStatusLabel(null)).toBeNull();
  });
});

describe('formatEntryPeriod', () => {
  const base = { kind: 'anime', status: 'FINISHED' } as const;
  it('uses the start precision', () => {
    expect(formatEntryPeriod({ ...base, startDate: '2023-10-22', startPrecision: 'day', endDate: '2024-03-30' })).toBe('2023-10-22〜2024-03-30');
    expect(formatEntryPeriod({ ...base, startDate: '2023-10-01', startPrecision: 'month', endDate: '2024-03-01' })).toBe('2023-10〜2024-03');
    expect(formatEntryPeriod({ ...base, startDate: '2023-01-01', startPrecision: 'year' })).toBe('2023');
  });
  it('shows an open range while it is still running', () => {
    expect(formatEntryPeriod({ kind: 'manga', status: 'RELEASING', startDate: '2020-05-01', startPrecision: 'month' })).toBe('2020-05〜');
  });
  it('shows only the release date for a book', () => {
    expect(formatEntryPeriod({ kind: 'book', status: null, startDate: '2026-11-10', startPrecision: 'day', endDate: '2026-11-10' })).toBe('2026-11-10');
  });
  it('is null without a start date', () => {
    expect(formatEntryPeriod({ ...base, startDate: null })).toBeNull();
  });
});

describe('entryCounts / formatLabel / namesLine', () => {
  it('lists episodes, chapters and volumes', () => {
    expect(entryCounts({ kind: 'anime', episodes: 24, chapters: null, volumes: null })).toEqual(['24話']);
    expect(entryCounts({ kind: 'manga', episodes: null, chapters: 80, volumes: 11 })).toEqual(['80話', '11巻']);
    expect(entryCounts({ kind: 'web-novel', episodes: 120, chapters: null, volumes: null })).toEqual(['120部分']);
    expect(entryCounts({ kind: 'anime', episodes: 0, chapters: null, volumes: undefined })).toEqual([]);
  });
  it('labels formats', () => {
    expect(formatLabel(makeEntry({ source: 'anilist', externalId: '1', kind: 'anime', format: 'MOVIE' }))).toBe('映画');
    expect(formatLabel(makeEntry({ source: 'anilist', externalId: '1', kind: 'manga', format: 'ONE_SHOT' }))).toBe('読切');
    expect(formatLabel(makeEntry({ source: 'ndl', externalId: '1', kind: 'book', format: '文庫' }))).toBe('文庫');
    expect(formatLabel(makeEntry({ source: 'ndl', externalId: '1', kind: 'book' }))).toBeNull();
  });
  it('shortens long name lists', () => {
    expect(namesLine(['A', 'B'])).toBe('A / B');
    expect(namesLine(['A', 'B', 'C', 'D', 'E'])).toBe('A / B / C ほか2');
    expect(namesLine([])).toBe('');
  });
});

describe('linkBadge', () => {
  it('shows method and confidence for non-AniList rows', () => {
    expect(linkBadge({ source: 'narou', linkMethod: 'title-exact', linkConfidence: 0.85 })).toBe('題名一致 85%');
    expect(linkBadge({ source: 'syoboi', linkMethod: 'title-year', linkConfidence: 0.95 })).toBe('題名・年一致 95%');
    expect(linkBadge({ source: 'ndl', linkMethod: 'series', linkConfidence: 0.85 })).toBe('シリーズ名一致 85%');
    expect(linkBadge({ source: 'kakuyomu', linkMethod: 'title-prefix', linkConfidence: 0.6 })).toBe('題名前方一致 60%');
    expect(linkBadge({ source: 'narou', linkMethod: 'new-method', linkConfidence: 0.9 })).toBe('new-method 90%');
  });
  it('shows nothing for AniList rows or rows without link data', () => {
    expect(linkBadge({ source: 'anilist', linkMethod: null, linkConfidence: null })).toBeNull();
    expect(linkBadge({ source: 'anilist', linkMethod: 'title-exact', linkConfidence: 0.9 })).toBeNull();
    expect(linkBadge({ source: 'narou', linkMethod: null, linkConfidence: null })).toBeNull();
  });
});

describe('formatJstDateTime / latestHarvestAt', () => {
  it('converts UTC to JST', () => {
    expect(formatJstDateTime('2026-10-14T03:30:00+00:00')).toBe('2026-10-14 12:30');
    expect(formatJstDateTime('2026-10-14T15:30:00Z')).toBe('2026-10-15 00:30');
    expect(formatJstDateTime('2026-10-14T03:30:00.123456+00:00')).toBe('2026-10-14 12:30');
    expect(formatJstDateTime('2026-10-14T03:30:00')).toBe('2026-10-14 12:30');
  });
  it('returns the original text when unreadable', () => {
    expect(formatJstDateTime('yesterday')).toBe('yesterday');
  });
  it('picks the newest harvest time', () => {
    expect(
      latestHarvestAt({
        sources: [
          { source: 'anilist', entries: 1, lastHarvestAt: '2026-10-13T00:00:00+00:00', errorSlices: 0 },
          { source: 'narou', entries: 1, lastHarvestAt: '2026-10-14T00:00:00+00:00', errorSlices: 0 },
          { source: 'ndl', entries: 1, lastHarvestAt: null, errorSlices: 0 },
        ],
      }),
    ).toBe('2026-10-14T00:00:00+00:00');
    expect(latestHarvestAt({ sources: [] })).toBeNull();
  });
});
