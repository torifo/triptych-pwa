import { describe, expect, it } from 'vitest';
import { WIDE_QUERY, watchWide } from './viewport';

function fakeMatchMedia(initial: boolean) {
  let listener: ((e: { matches: boolean }) => void) | null = null;
  const queries: string[] = [];
  const matchMedia = (query: string) => {
    queries.push(query);
    return {
      matches: initial,
      addEventListener: (_type: 'change', fn: (e: { matches: boolean }) => void) => (listener = fn),
    };
  };
  return { matchMedia, queries, change: (matches: boolean) => listener?.({ matches }) };
}

describe('watchWide', () => {
  it('CSS の PC 幅（64rem）と同じ問い合わせで、今の幅と変化を拾う', () => {
    const media = fakeMatchMedia(true);
    const seen: boolean[] = [];
    expect(watchWide(media.matchMedia, (wide) => seen.push(wide))).toBe(true);
    expect(media.queries).toEqual([WIDE_QUERY]);
    expect(WIDE_QUERY).toBe('(min-width: 64rem)');
    media.change(false);
    expect(seen).toEqual([false]);
  });

  it('matchMedia が無い環境ではスマホ幅として扱う', () => {
    expect(watchWide(undefined, () => {})).toBe(false);
  });
});
