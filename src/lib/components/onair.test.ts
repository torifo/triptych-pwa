import { describe, expect, it } from 'vitest';
import type { Edition, Release } from '../db/schema';
import { airingInstant, broadcastDayJst, countdownLabel, onAirEditionIds } from './onair';

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

describe('broadcastDayJst', () => {
  it('朝5時より前は前日の放送日', () => {
    // 2026-10-16 01:30 JST = 2026-10-15T16:30Z
    expect(broadcastDayJst(new Date('2026-10-15T16:30:00Z'))).toBe('2026-10-15');
    // 2026-10-16 05:00 JST = 2026-10-15T20:00Z
    expect(broadcastDayJst(new Date('2026-10-15T20:00:00Z'))).toBe('2026-10-16');
  });
});

describe('airingInstant', () => {
  it('24 時超えの時刻を翌日の実時刻にする', () => {
    // 10/15 25:30 = 10/16 01:30 JST = 10/15 16:30Z
    expect(airingInstant({ date: '2026-10-15', time: '25:30' })?.toISOString()).toBe('2026-10-15T16:30:00.000Z');
    expect(airingInstant({ date: '2026-10-15', time: '9:05' })?.toISOString()).toBe('2026-10-15T00:05:00.000Z');
  });

  it('時刻が無い・不正なら undefined', () => {
    expect(airingInstant({ date: '2026-10-15' })).toBeUndefined();
    expect(airingInstant({ date: '2026-10-15', time: '24:61' })).toBeUndefined();
    expect(airingInstant({ date: '2026-10', time: '24:00' })).toBeUndefined();
  });
});

describe('onAirEditionIds', () => {
  const editions = new Map<string, Pick<Edition, 'status'>>([
    ['e1', { status: 'active' }],
    ['e2', { status: 'plan' }],
    ['e3', { status: 'done' }],
    ['e4', { status: 'active' }],
  ]);

  it('今日の放送・配信（rolling を含む）がある追跡中 Edition だけ', () => {
    const ids = onAirEditionIds(
      [
        release({ id: 'r1', editionId: 'e1', date: '2026-10-15', time: '24:00' }),
        release({ id: 'r2', editionId: 'e2', date: '2026-10-15', kind: 'stream-start', rolling: true }),
        release({ id: 'r3', editionId: 'e3', date: '2026-10-15' }), // 完了は数えない
        release({ id: 'r4', editionId: 'e4', date: '2026-10-16' }), // 明日
        release({ id: 'r5', editionId: 'e4', date: '2026-10-15', kind: 'volume' }), // 本は放送ではない
        release({ id: 'r6', editionId: 'e4', date: '2026-10-01', precision: 'month' }),
        release({ id: 'r7', editionId: 'missing', date: '2026-10-15' }),
      ],
      editions,
      '2026-10-15',
    );
    expect([...ids].sort()).toEqual(['e1', 'e2']);
  });
});

describe('countdownLabel', () => {
  const now = new Date('2026-10-15T05:00:00Z'); // 10/15 14:00 JST

  it('24 時間以内の未来だけ「あと h:mm」', () => {
    expect(countdownLabel({ date: '2026-10-15', time: '24:00' }, now)).toBe('あと 10:00');
    expect(countdownLabel({ date: '2026-10-15', time: '14:00' }, new Date('2026-10-15T04:59:30Z'))).toBe('あと 0:01');
  });

  it('過ぎたもの・遠いもの・時刻の無いものは出さない', () => {
    expect(countdownLabel({ date: '2026-10-15', time: '13:00' }, now)).toBeUndefined();
    expect(countdownLabel({ date: '2026-10-17', time: '24:00' }, now)).toBeUndefined();
    expect(countdownLabel({ date: '2026-10-15' }, now)).toBeUndefined();
  });
});
