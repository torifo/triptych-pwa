import { describe, expect, it } from 'vitest';
import {
  addDaysYmd,
  compareYmd,
  diffDaysYmd,
  formatReleaseDate,
  isPastRelease,
  isValidYmd,
  isWithinDays,
  precisionEnd,
  precisionStart,
  todayJst,
} from './dates';

describe('todayJst', () => {
  it('JST 00:30 は UTC では前日だが、JST の暦日を返す', () => {
    expect(todayJst(new Date('2026-10-14T00:30:00+09:00'))).toBe('2026-10-14');
    expect(new Date('2026-10-14T00:30:00+09:00').toISOString().slice(0, 10)).toBe('2026-10-13');
  });

  it('JST 08:59 も同じ暦日（UTC ではまだ前日）', () => {
    expect(todayJst(new Date('2026-10-14T08:59:59+09:00'))).toBe('2026-10-14');
  });

  it('JST 09:00 ちょうど（UTC が日付を跨ぐ瞬間）も同じ暦日', () => {
    expect(todayJst(new Date('2026-10-14T09:00:00+09:00'))).toBe('2026-10-14');
  });

  it('JST 23:59 までは同じ日で、0:00 で翌日になる', () => {
    expect(todayJst(new Date('2026-10-13T23:59:59+09:00'))).toBe('2026-10-13');
    expect(todayJst(new Date('2026-10-14T00:00:00+09:00'))).toBe('2026-10-14');
  });

  it('月・年の境界を跨ぐ', () => {
    expect(todayJst(new Date('2026-12-31T15:00:00Z'))).toBe('2027-01-01');
    expect(todayJst(new Date('2026-02-28T15:00:00Z'))).toBe('2026-03-01');
  });
});

describe('addDaysYmd / compareYmd / diffDaysYmd', () => {
  it('月・年・うるう日を跨いで加減算する', () => {
    expect(addDaysYmd('2026-10-31', 1)).toBe('2026-11-01');
    expect(addDaysYmd('2026-12-31', 1)).toBe('2027-01-01');
    expect(addDaysYmd('2026-02-28', 1)).toBe('2026-03-01');
    expect(addDaysYmd('2028-02-28', 1)).toBe('2028-02-29');
    expect(addDaysYmd('2026-01-01', -1)).toBe('2025-12-31');
    expect(addDaysYmd('2026-10-13', 7)).toBe('2026-10-20');
    expect(addDaysYmd('2026-10-13', 0)).toBe('2026-10-13');
  });

  it('compareYmd は前・同・後を -1/0/1 で返す', () => {
    expect(compareYmd('2026-10-12', '2026-10-13')).toBe(-1);
    expect(compareYmd('2026-10-13', '2026-10-13')).toBe(0);
    expect(compareYmd('2027-01-01', '2026-12-31')).toBe(1);
  });

  it('diffDaysYmd は a - b の日数', () => {
    expect(diffDaysYmd('2026-10-16', '2026-10-13')).toBe(3);
    expect(diffDaysYmd('2026-10-13', '2026-10-16')).toBe(-3);
    expect(diffDaysYmd('2028-03-01', '2028-02-28')).toBe(2);
    expect(diffDaysYmd('2027-01-01', '2026-01-01')).toBe(365);
  });

  it('不正な日付は RangeError', () => {
    expect(() => addDaysYmd('2026-13-01', 1)).toThrow(RangeError);
    expect(() => diffDaysYmd('2026-02-30', '2026-03-01')).toThrow(RangeError);
    expect(() => compareYmd('2026/10/13', '2026-10-13')).toThrow(RangeError);
  });

  it('isValidYmd は形式と実在を確かめる', () => {
    expect(isValidYmd('2026-10-13')).toBe(true);
    expect(isValidYmd('2028-02-29')).toBe(true);
    expect(isValidYmd('2026-02-29')).toBe(false);
    expect(isValidYmd('2026-8-1')).toBe(false);
    expect(isValidYmd('2026-08')).toBe(false);
    expect(isValidYmd('')).toBe(false);
  });
});

describe('precisionStart / precisionEnd', () => {
  it('day はそのまま', () => {
    expect(precisionStart('2026-10-13', 'day')).toBe('2026-10-13');
    expect(precisionEnd('2026-10-13', 'day')).toBe('2026-10-13');
  });

  it('month は月初・月末（30/31 日・2 月・うるう年）', () => {
    expect(precisionStart('2026-10-13', 'month')).toBe('2026-10-01');
    expect(precisionEnd('2026-01-01', 'month')).toBe('2026-01-31');
    expect(precisionEnd('2026-04-01', 'month')).toBe('2026-04-30');
    expect(precisionEnd('2026-02-01', 'month')).toBe('2026-02-28');
    expect(precisionEnd('2028-02-01', 'month')).toBe('2028-02-29');
    expect(precisionEnd('2026-12-01', 'month')).toBe('2026-12-31');
  });

  it('year は 1/1 と 12/31', () => {
    expect(precisionStart('2026-08-25', 'year')).toBe('2026-01-01');
    expect(precisionEnd('2026-01-01', 'year')).toBe('2026-12-31');
  });
});

describe('isPastRelease', () => {
  const today = '2026-10-13';

  it('day: 昨日は過去、今日・明日は過去でない', () => {
    expect(isPastRelease({ date: '2026-10-12', precision: 'day' }, today)).toBe(true);
    expect(isPastRelease({ date: '2026-10-13', precision: 'day' }, today)).toBe(false);
    expect(isPastRelease({ date: '2026-10-14', precision: 'day' }, today)).toBe(false);
  });

  it('precision 未設定は day として扱う', () => {
    expect(isPastRelease({ date: '2026-10-12' }, today)).toBe(true);
    expect(isPastRelease({ date: '2026-10-13' }, today)).toBe(false);
  });

  it('month: 月末を過ぎるまでは過去でない', () => {
    expect(isPastRelease({ date: '2026-09-01', precision: 'month' }, today)).toBe(true);
    expect(isPastRelease({ date: '2026-10-01', precision: 'month' }, today)).toBe(false);
    expect(isPastRelease({ date: '2026-10-01', precision: 'month' }, '2026-10-31')).toBe(false);
    expect(isPastRelease({ date: '2026-10-01', precision: 'month' }, '2026-11-01')).toBe(true);
  });

  it('year: 年末を過ぎるまでは過去でない', () => {
    expect(isPastRelease({ date: '2026-01-01', precision: 'year' }, '2026-12-31')).toBe(false);
    expect(isPastRelease({ date: '2026-01-01', precision: 'year' }, '2027-01-01')).toBe(true);
  });

  it('不正な日付は過去扱いにしない', () => {
    expect(isPastRelease({ date: 'oops' }, today)).toBe(false);
  });
});

describe('isWithinDays', () => {
  const today = '2026-10-13';

  it('今日〜今日+n を含む', () => {
    expect(isWithinDays({ date: '2026-10-13', precision: 'day' }, today, 7)).toBe(true);
    expect(isWithinDays({ date: '2026-10-20', precision: 'day' }, today, 7)).toBe(true);
    expect(isWithinDays({ date: '2026-10-21', precision: 'day' }, today, 7)).toBe(false);
    expect(isWithinDays({ date: '2026-10-12', precision: 'day' }, today, 7)).toBe(false);
    expect(isWithinDays({ date: '2026-10-13' }, today, 0)).toBe(true);
  });

  it('day 精度のみ。月・年精度は範囲内の日付に見えても false', () => {
    expect(isWithinDays({ date: '2026-10-13', precision: 'month' }, today, 7)).toBe(false);
    expect(isWithinDays({ date: '2026-10-13', precision: 'year' }, today, 7)).toBe(false);
  });
});

describe('formatReleaseDate', () => {
  it('day は M/D(曜)。先頭のゼロは付けない', () => {
    expect(formatReleaseDate({ date: '2026-10-16', precision: 'day' })).toBe('10/16(金)');
    expect(formatReleaseDate({ date: '2026-08-05', precision: 'day' })).toBe('8/5(水)');
    expect(formatReleaseDate({ date: '2026-10-18' })).toBe('10/18(日)');
    expect(formatReleaseDate({ date: '2026-10-17', precision: 'day' })).toBe('10/17(土)');
  });

  it('time があれば併記し、曜日は放送日（date）のもの', () => {
    // 25:30 は実時刻では土曜の 1:30 だが、放送日は金曜
    expect(formatReleaseDate({ date: '2026-10-16', precision: 'day', time: '25:30' })).toBe(
      '10/16(金) 25:30',
    );
  });

  it('month は 2026年8月、year は 2026年（日付セルに見せない）', () => {
    expect(formatReleaseDate({ date: '2026-08-01', precision: 'month' })).toBe('2026年8月');
    expect(formatReleaseDate({ date: '2026-12-01', precision: 'month' })).toBe('2026年12月');
    expect(formatReleaseDate({ date: '2026-01-01', precision: 'year' })).toBe('2026年');
  });

  it('month / year では time を無視する', () => {
    expect(formatReleaseDate({ date: '2026-08-01', precision: 'month', time: '25:30' })).toBe(
      '2026年8月',
    );
  });

  it('不正な日付は壊さずそのまま返す', () => {
    expect(formatReleaseDate({ date: 'TBD' })).toBe('TBD');
  });
});
