import { describe, expect, it } from 'vitest';
import { calendarDay, cellCovers, cellThumbs, dayCellLabel, dayOnAirMark } from './calendar-view';

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

describe('cellCovers', () => {
  const ev = (clusterId: string, popularity: number | null = null) => ({ clusterId, popularity });

  it('Library が空なら、カタログの出来事を人気順で枠いっぱいまで出す', () => {
    const events = [ev('a', 10), ev('b', 300), ev('c', null), ev('d', 50)];
    const { library, catalog, rest } = cellCovers([], events, 2);
    expect(library).toEqual([]);
    expect(catalog.map((e) => e.clusterId)).toEqual(['b', 'd']);
    expect(rest).toBe(2);
  });

  it('Library の表紙を先に置き、空いた枠だけカタログで埋める', () => {
    const releases = [{ id: '1', editionId: 'x' }];
    const { library, catalog, rest } = cellCovers(releases, [ev('a', 5), ev('b', 9)], 2);
    expect(library.map((r) => r.id)).toEqual(['1']);
    expect(catalog.map((e) => e.clusterId)).toEqual(['b']);
    expect(rest).toBe(1);
  });

  it('同じ作品の出来事は1つに数え、Library で埋まればカタログは残りの数だけ', () => {
    const releases = [
      { id: '1', editionId: 'x' },
      { id: '2', editionId: 'y' },
      { id: '3', editionId: 'z' },
    ];
    const { library, catalog, rest } = cellCovers(releases, [ev('a', 1), ev('a', 1), ev('b', 2)], 2);
    expect(library.map((r) => r.id)).toEqual(['1', '2']);
    expect(catalog).toEqual([]);
    expect(rest).toBe(3); // Library の z ＋ カタログの a・b
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
  it('日付・分類ごとの件数・放送の有無', () => {
    expect(dayCellLabel('2026-10-05', undefined, null)).toBe('10月5日');
    expect(dayCellLabel('2026-10-05', { program: 0, release: 0, web: 0 }, null)).toBe('10月5日');
    expect(dayCellLabel('2026-10-15', { program: 3, release: 0, web: 0 }, 'today')).toBe('10月15日 番組 3件 放送・配信あり');
  });

  it('0 の分類は読まず、番組 → 発売 → Web の順', () => {
    expect(dayCellLabel('2026-10-15', { program: 17, release: 9, web: 1 }, 'upcoming')).toBe(
      '10月15日 番組 17件 発売 9件 Web 1件 放送・配信あり',
    );
    expect(dayCellLabel('2026-10-15', { program: 0, release: 12, web: 0 }, null)).toBe('10月15日 発売 12件');
  });
});
