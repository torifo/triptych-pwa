import { watchWide } from './viewport';

const state = $state({ wide: false });
state.wide = watchWide(
  typeof window !== 'undefined' && typeof window.matchMedia === 'function' ? window.matchMedia.bind(window) : undefined,
  (wide) => (state.wide = wide),
);

/** PC 幅（1024px 以上）か。画面の幅が変わると追従する。 */
export const viewport: { readonly wide: boolean } = state;
