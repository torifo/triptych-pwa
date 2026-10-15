import { describe, expect, it } from 'vitest';
import {
  CALENDAR_OVERLAY_KEY,
  CALENDAR_VIEW_KEY,
  catalogKindsFor,
  dayHeading,
  filterByMedia,
  loadCalendarView,
  loadCatalogOverlay,
  matchesMediaFilter,
  monthRange,
  rangeLabel,
  saveCalendarView,
  saveCatalogOverlay,
  weekDates,
  weekStart,
} from './view';

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
    throw new Error('denied');
  },
  setItem: () => {
    throw new Error('quota');
  },
};

describe('表示の保存', () => {
  it('既定は週。保存した月・リストはそのまま、知らない値は週', () => {
    expect(loadCalendarView(memoryStorage())).toBe('week');
    expect(loadCalendarView(memoryStorage({ [CALENDAR_VIEW_KEY]: 'month' }))).toBe('month');
    expect(loadCalendarView(memoryStorage({ [CALENDAR_VIEW_KEY]: 'list' }))).toBe('list');
    expect(loadCalendarView(memoryStorage({ [CALENDAR_VIEW_KEY]: 'year' }))).toBe('week');
    expect(loadCalendarView(null)).toBe('week');
    expect(loadCalendarView(throwing)).toBe('week');
  });

  it('選んだ表示を残す。保存できない環境でも例外にしない', () => {
    const storage = memoryStorage();
    saveCalendarView('list', storage);
    expect(storage.data.get(CALENDAR_VIEW_KEY)).toBe('list');
    expect(() => saveCalendarView('month', throwing)).not.toThrow();
    expect(() => saveCalendarView('month', null)).not.toThrow();
  });

  it('カタログの重ね表示は既定で ON。OFF にした時だけ OFF', () => {
    expect(loadCatalogOverlay(memoryStorage())).toBe(true);
    expect(loadCatalogOverlay(null)).toBe(true);
    expect(loadCatalogOverlay(throwing)).toBe(true);
    expect(loadCatalogOverlay(memoryStorage({ [CALENDAR_OVERLAY_KEY]: 'off' }))).toBe(false);
    expect(loadCatalogOverlay(memoryStorage({ [CALENDAR_OVERLAY_KEY]: 'on' }))).toBe(true);

    const storage = memoryStorage();
    saveCatalogOverlay(false, storage);
    expect(storage.data.get(CALENDAR_OVERLAY_KEY)).toBe('off');
    saveCatalogOverlay(true, storage);
    expect(storage.data.get(CALENDAR_OVERLAY_KEY)).toBe('on');
    expect(() => saveCatalogOverlay(true, throwing)).not.toThrow();
  });
});

describe('媒体の絞り込み', () => {
  it('本は文庫と漫画、Web は Web 小説', () => {
    expect(matchesMediaFilter('anime', 'all')).toBe(true);
    expect(matchesMediaFilter(undefined, 'all')).toBe(true);
    expect(matchesMediaFilter('anime', 'anime')).toBe(true);
    expect(matchesMediaFilter('manga', 'anime')).toBe(false);
    expect(matchesMediaFilter('light-novel', 'book')).toBe(true);
    expect(matchesMediaFilter('manga', 'book')).toBe(true);
    expect(matchesMediaFilter('web-novel', 'book')).toBe(false);
    expect(matchesMediaFilter('web-novel', 'web')).toBe(true);
    expect(matchesMediaFilter(undefined, 'web')).toBe(false);
  });

  it('Library の Release を Edition の媒体で絞る', () => {
    const editions = new Map([
      ['a', { mediaType: 'anime' as const }],
      ['l', { mediaType: 'light-novel' as const }],
      ['m', { mediaType: 'manga' as const }],
      ['w', { mediaType: 'web-novel' as const }],
    ]);
    const list = ['a', 'l', 'm', 'w', 'gone'].map((editionId) => ({ editionId }));
    expect(filterByMedia(list, editions, 'all').map((r) => r.editionId)).toEqual(['a', 'l', 'm', 'w', 'gone']);
    expect(filterByMedia(list, editions, 'book').map((r) => r.editionId)).toEqual(['l', 'm']);
    expect(filterByMedia(list, editions, 'web').map((r) => r.editionId)).toEqual(['w']);
  });

  it('カタログに問い合わせる種類（Web はカタログに無いので空）', () => {
    expect(catalogKindsFor('all')).toEqual(['anime', 'book']);
    expect(catalogKindsFor('anime')).toEqual(['anime']);
    expect(catalogKindsFor('book')).toEqual(['book']);
    expect(catalogKindsFor('web')).toEqual([]);
  });
});

describe('週', () => {
  it('今日から7日。前後の週へ7日ずつ送る', () => {
    expect(weekStart('2026-10-15', 0)).toBe('2026-10-15');
    expect(weekStart('2026-10-15', 1)).toBe('2026-10-22');
    expect(weekStart('2026-10-15', -1)).toBe('2026-10-08');
    expect(weekDates('2026-12-29')).toEqual([
      '2026-12-29',
      '2026-12-30',
      '2026-12-31',
      '2027-01-01',
      '2027-01-02',
      '2027-01-03',
      '2027-01-04',
    ]);
  });

  it('日の見出しは等幅の月日と英字の曜日', () => {
    expect(dayHeading('2026-10-15')).toEqual({ date: '10.15', weekday: 'THU', label: '10月15日（木）' });
    expect(dayHeading('2026-11-01')).toEqual({ date: '11.01', weekday: 'SUN', label: '11月1日（日）' });
  });

  it('期間の表記（年をまたぐ時は年も）', () => {
    expect(rangeLabel('2026-10-15', '2026-10-21')).toBe('10.15 – 10.21');
    expect(rangeLabel('2026-12-29', '2027-01-04')).toBe('2026.12.29 – 2027.01.04');
  });
});

describe('monthRange', () => {
  it('月初から月末（うるう年の2月も）', () => {
    expect(monthRange(2026, 10)).toEqual({ from: '2026-10-01', to: '2026-10-31' });
    expect(monthRange(2028, 2)).toEqual({ from: '2028-02-01', to: '2028-02-29' });
    expect(monthRange(2026, 2)).toEqual({ from: '2026-02-01', to: '2026-02-28' });
  });
});
