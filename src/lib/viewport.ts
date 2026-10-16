// PC 幅（1024px 以上）かどうか。CSS の @media (min-width: 64rem) と同じ値で、構造が変わる所だけが読む

export const WIDE_QUERY = '(min-width: 64rem)';

export type MatchMedia = (query: string) => {
  matches: boolean;
  addEventListener(type: 'change', listener: (e: { matches: boolean }) => void): void;
};

/** 今が PC 幅かを返し、変わったら onChange を呼ぶ。matchMedia が無ければスマホ幅。 */
export function watchWide(matchMedia: MatchMedia | undefined, onChange: (wide: boolean) => void): boolean {
  if (!matchMedia) return false;
  const query = matchMedia(WIDE_QUERY);
  query.addEventListener('change', (e) => onChange(e.matches));
  return query.matches;
}
