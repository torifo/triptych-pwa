import { describe, expect, it } from 'vitest';
import { calendarDay, cellThumbs, dayCellLabel, dayOnAirMark } from './calendar-view';

describe('dayOnAirMark', () => {
  it('放送・配信のある今日と先の日にだけ印を付ける', () => {
    const ep = [{ kind: 'episode' as const }];
    expect(dayOnAirMark(ep, '2026-10-15', '2026-10-15')).toBe('today');
    expect(dayOnAirMark([{ kind: 'stream-start' as const }], '2026-10-20', '2026-10-15')).toBe('upcoming');
    expect(dayOnAirMark(ep, '2026-10-14', '2026-10-15')).toBeNull();
    expect(dayOnAirMark([{ kind: 'volume' as const }], '2026-10-20', '2026-10-15')).toBeNull();
    expect(dayOnAirMark([], '2026-10-20', '2026-10-15')).toBeNull();
  });
});

describe('cellThumbs', () => {
  it('同じ Edition は1つにし、出しきれない Edition の数を返す', () => {
    const list = [
      { id: '1', editionId: 'a' },
      { id: '2', editionId: 'a' },
      { id: '3', editionId: 'b' },
      { id: '4', editionId: 'c' },
      { id: '5', editionId: 'd' },
    ];
    const { shown, rest } = cellThumbs(list, 2);
    expect(shown.map((r) => r.id)).toEqual(['1', '3']);
    expect(rest).toBe(2);
    expect(cellThumbs(list.slice(0, 2), 2)).toEqual({ shown: [list[0]], rest: 0 });
  });
});

describe('calendarDay', () => {
  it('選んだ日がこの月ならその日、無ければこの月の今日', () => {
    expect(calendarDay('2026-10-20', '2026-10-15', 2026, 10)).toBe('2026-10-20');
    expect(calendarDay(null, '2026-10-15', 2026, 10)).toBe('2026-10-15');
    expect(calendarDay('2026-09-30', '2026-10-15', 2026, 10)).toBe('2026-10-15');
    expect(calendarDay(null, '2026-10-15', 2026, 11)).toBeNull();
    expect(calendarDay('2026-11-03', '2026-10-15', 2026, 11)).toBe('2026-11-03');
  });
});

describe('dayCellLabel', () => {
  it('日付・件数・放送の有無', () => {
    expect(dayCellLabel('2026-10-05', 0, null)).toBe('10月5日');
    expect(dayCellLabel('2026-10-15', 3, 'today')).toBe('10月15日 3件 放送・配信あり');
  });
});
