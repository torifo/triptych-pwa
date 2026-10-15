import { describe, expect, it } from 'vitest';
import { displayReleaseLabel } from './labels';

describe('displayReleaseLabel', () => {
  it('数字だけの巻は「◯巻」、末尾が数字の巻は数字に「巻」を付ける', () => {
    expect(displayReleaseLabel({ kind: 'volume', label: '5' })).toBe('5巻');
    expect(displayReleaseLabel({ kind: 'volume', label: '外伝小蘭回想録 1' })).toBe('外伝小蘭回想録 1巻');
    expect(displayReleaseLabel({ kind: 'volume', label: '第3部 10' })).toBe('第3部 10巻');
  });

  it('すでに巻と書いてあるもの・数字で終わらないもの・巻以外はそのまま', () => {
    expect(displayReleaseLabel({ kind: 'volume', label: '第12巻' })).toBe('第12巻');
    expect(displayReleaseLabel({ kind: 'volume', label: '第3部[10]' })).toBe('第3部[10]');
    expect(displayReleaseLabel({ kind: 'volume', label: '短編集' })).toBe('短編集');
    expect(displayReleaseLabel({ kind: 'episode', label: '3' })).toBe('3');
  });
});
