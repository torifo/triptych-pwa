// テーマ（ライト／ダーク／端末に合わせる）。選択を端末に残し、<html> の dark クラスで画面に反映する。
// 起動時に main.ts から initTheme() を呼ぶので、設定画面を開かなくても効く。

export type ThemePref = 'light' | 'dark' | 'system';

export const THEME_PREFS: readonly ThemePref[] = ['light', 'dark', 'system'];

export const THEME_LABELS: Record<ThemePref, string> = {
  light: 'ライト',
  dark: 'ダーク',
  system: '端末に合わせる',
};

export const THEME_KEY = 'triptych.theme';
/** 旧版のダークモード（'1' がダーク）。新キーが無い時だけ引き継ぐ。 */
export const LEGACY_DARK_MODE_KEY = 'triptych.darkMode';

const DARK_QUERY = '(prefers-color-scheme: dark)';

function isThemePref(value: unknown): value is ThemePref {
  return value === 'light' || value === 'dark' || value === 'system';
}

/** localStorage そのものに触れるだけで例外になる環境があるので、取り出しも包む。 */
function browserStorage(): Storage | null {
  try {
    return typeof localStorage === 'undefined' ? null : localStorage;
  } catch {
    return null;
  }
}

/**
 * 端末に保存したテーマ。無い・読めない・知らない値なら旧キーを見て（'1' ならダーク）、
 * それも無ければ端末に合わせる。
 */
export function loadThemePref(storage: Pick<Storage, 'getItem'> | null = browserStorage()): ThemePref {
  try {
    const stored = storage?.getItem(THEME_KEY);
    if (isThemePref(stored)) return stored;
    return storage?.getItem(LEGACY_DARK_MODE_KEY) === '1' ? 'dark' : 'system';
  } catch {
    return 'system';
  }
}

/** テーマの選択を端末に残す。保存できない環境では何もしない（切り替え自体はできる）。 */
export function saveThemePref(pref: ThemePref, storage: Pick<Storage, 'setItem'> | null = browserStorage()): void {
  try {
    storage?.setItem(THEME_KEY, pref);
  } catch {
    // 容量超過・プライベートモードなど。表示は切り替わるので黙って諦める
  }
}

/** applyTheme が触る環境。テストでは差し替える。 */
export interface ThemeEnv {
  root: Pick<HTMLElement, 'classList' | 'style'> | null;
  matchMedia?: (query: string) => {
    matches: boolean;
    addEventListener(type: 'change', listener: (event: { matches: boolean }) => void): void;
    removeEventListener(type: 'change', listener: (event: { matches: boolean }) => void): void;
  };
}

function browserEnv(): ThemeEnv {
  if (typeof document === 'undefined') return { root: null };
  return {
    root: document.documentElement,
    matchMedia: typeof window !== 'undefined' && window.matchMedia ? window.matchMedia.bind(window) : undefined,
  };
}

/** 端末の設定の変化を追っている購読。次に適用する時に必ず外す。 */
let stopWatching: (() => void) | null = null;

function paint(root: ThemeEnv['root'], dark: boolean): void {
  if (!root) return;
  root.classList.toggle('dark', dark);
  // スクロールバーやフォーム部品、読み込み中の地の色もテーマに合わせる
  root.style.colorScheme = dark ? 'dark' : 'light';
}

/**
 * テーマを画面に反映する。'system' は端末の prefers-color-scheme で決め、
 * 端末側の切り替えも追う（前の購読は外してから張り直す）。
 */
export function applyTheme(pref: ThemePref, env: ThemeEnv = browserEnv()): void {
  stopWatching?.();
  stopWatching = null;

  if (pref !== 'system') {
    paint(env.root, pref === 'dark');
    return;
  }

  const query = env.matchMedia?.(DARK_QUERY);
  paint(env.root, query?.matches ?? false);
  if (!query) return;
  const onChange = (event: { matches: boolean }) => paint(env.root, event.matches);
  query.addEventListener('change', onChange);
  stopWatching = () => query.removeEventListener('change', onChange);
}

/** 起動時に一度、保存した設定を読んで反映する。マウント前に呼べば白い画面のちらつきが減る。 */
export function initTheme(
  storage: Pick<Storage, 'getItem'> | null = browserStorage(),
  env: ThemeEnv = browserEnv(),
): ThemePref {
  const pref = loadThemePref(storage);
  applyTheme(pref, env);
  return pref;
}
