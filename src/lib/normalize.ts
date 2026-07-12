// タイトル正規化・照合スコア計算

// 空白・記号（Unicode の Punctuation / Symbol / Space カテゴリ）を除去する。
// eslint-disable-next-line no-control-regex
const STRIP_PATTERN = /[\s\p{P}\p{S}]/gu;

/**
 * NFKC 正規化 → 小文字化 → 空白・記号除去 の順でタイトルを正規化する。
 * 検知照合（Work.titleNormalized）や DetectionEvent の照合に使う。
 */
export function normalizeTitle(title: string): string {
  return title.normalize('NFKC').toLowerCase().replace(STRIP_PATTERN, '');
}

/**
 * 2つのタイトルの照合スコアを 0-1 で返す。
 * - 完全一致（正規化前の文字列がそのまま一致）: 1.0
 * - 正規化後一致: 0.9
 * - 部分一致（どちらかがもう一方を包含）: 包含割合に応じた簡易スコア
 * - 不一致: 0
 */
export function titleMatchScore(a: string, b: string): number {
  if (a === b) return 1.0;

  const na = normalizeTitle(a);
  const nb = normalizeTitle(b);

  if (na.length === 0 || nb.length === 0) return 0;
  if (na === nb) return 0.9;

  const [shorter, longer] = na.length <= nb.length ? [na, nb] : [nb, na];
  if (longer.includes(shorter)) {
    // 部分一致: 短い方が長い方に占める割合を簡易スコアとする（上限 0.8）
    const ratio = shorter.length / longer.length;
    return Math.min(0.8, 0.5 + ratio * 0.3);
  }

  return 0;
}
