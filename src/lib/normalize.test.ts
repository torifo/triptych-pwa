import { describe, expect, it } from 'vitest';
import { normalizeTitle, titleMatchScore } from './normalize';

describe('normalizeTitle', () => {
  it('lowercases ASCII characters', () => {
    expect(normalizeTitle('ABC')).toBe('abc');
  });

  it('strips whitespace and punctuation', () => {
    expect(normalizeTitle('転生したら スライムだった件！')).toBe('転生したらスライムだった件');
  });

  it('applies NFKC normalization (full-width -> half-width)', () => {
    expect(normalizeTitle('ＡＢＣ')).toBe('abc');
  });

  it('is stable across equivalent inputs', () => {
    expect(normalizeTitle('Re:ゼロから始める異世界生活')).toBe(
      normalizeTitle('Re：ゼロから始める異世界生活'),
    );
  });
});

describe('titleMatchScore', () => {
  it('returns 1.0 for exact string match', () => {
    expect(titleMatchScore('転生したらスライムだった件', '転生したらスライムだった件')).toBe(1.0);
  });

  it('returns a high score for normalized match', () => {
    expect(titleMatchScore('転生したら スライムだった件！', '転生したらスライムだった件')).toBe(0.9);
  });

  it('returns a partial score for substring match', () => {
    const score = titleMatchScore('転生したらスライムだった件', '転生したらスライムだった件 第2期');
    expect(score).toBeGreaterThan(0);
    expect(score).toBeLessThan(0.9);
  });

  it('returns 0 for unrelated titles', () => {
    expect(titleMatchScore('無職転生', 'オーバーロード')).toBe(0);
  });

  it('returns 0 when either title normalizes to empty', () => {
    expect(titleMatchScore('！！！', '無職転生')).toBe(0);
  });
});
