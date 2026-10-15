import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  LEGACY_DARK_MODE_KEY,
  THEME_KEY,
  applyTheme,
  initTheme,
  loadThemePref,
  saveThemePref,
  type ThemeEnv,
} from './theme';

function memoryStorage(initial: Record<string, string> = {}) {
  const data = new Map(Object.entries(initial));
  return {
    getItem: (key: string) => data.get(key) ?? null,
    setItem: (key: string, value: string) => void data.set(key, value),
    data,
  };
}

const throwing = {
  getItem: () => {
    throw new Error('SecurityError');
  },
  setItem: () => {
    throw new Error('QuotaExceededError');
  },
};

describe('loadThemePref', () => {
  it('何も保存が無ければ端末に合わせる', () => {
    expect(loadThemePref(memoryStorage())).toBe('system');
  });

  it('保存した3択はそのまま返す', () => {
    expect(loadThemePref(memoryStorage({ [THEME_KEY]: 'light' }))).toBe('light');
    expect(loadThemePref(memoryStorage({ [THEME_KEY]: 'dark' }))).toBe('dark');
    expect(loadThemePref(memoryStorage({ [THEME_KEY]: 'system' }))).toBe('system');
  });

  it('旧キーが 1 ならダーク、それ以外は端末に合わせる', () => {
    expect(loadThemePref(memoryStorage({ [LEGACY_DARK_MODE_KEY]: '1' }))).toBe('dark');
    expect(loadThemePref(memoryStorage({ [LEGACY_DARK_MODE_KEY]: '0' }))).toBe('system');
    expect(loadThemePref(memoryStorage({ [LEGACY_DARK_MODE_KEY]: 'yes' }))).toBe('system');
  });

  it('新キーがあれば旧キーより優先する', () => {
    expect(loadThemePref(memoryStorage({ [THEME_KEY]: 'light', [LEGACY_DARK_MODE_KEY]: '1' }))).toBe('light');
  });

  it('新キーが知らない値なら無かったものとして旧キーを見る', () => {
    expect(loadThemePref(memoryStorage({ [THEME_KEY]: 'sepia' }))).toBe('system');
    expect(loadThemePref(memoryStorage({ [THEME_KEY]: 'sepia', [LEGACY_DARK_MODE_KEY]: '1' }))).toBe('dark');
  });

  it('localStorage が使えなくても端末に合わせるに落ちる', () => {
    expect(loadThemePref(null)).toBe('system');
    expect(loadThemePref(throwing)).toBe('system');
  });
});

describe('saveThemePref', () => {
  it('保存した値を読み戻せる', () => {
    const storage = memoryStorage();
    saveThemePref('dark', storage);
    expect(storage.data.get(THEME_KEY)).toBe('dark');
    expect(loadThemePref(storage)).toBe('dark');
  });

  it('保存の失敗は投げない', () => {
    expect(() => saveThemePref('light', throwing)).not.toThrow();
    expect(() => saveThemePref('light', null)).not.toThrow();
  });
});

/** matchMedia の代役。setDark で端末の設定が切り替わったことにできる。 */
function fakeSystem(initialDark: boolean) {
  const listeners = new Set<(event: { matches: boolean }) => void>();
  const query = {
    matches: initialDark,
    addEventListener: (_type: 'change', listener: (event: { matches: boolean }) => void) => void listeners.add(listener),
    removeEventListener: (_type: 'change', listener: (event: { matches: boolean }) => void) =>
      void listeners.delete(listener),
  };
  const matchMedia = vi.fn((_query: string) => query);
  return {
    matchMedia,
    listeners,
    setDark(dark: boolean) {
      query.matches = dark;
      for (const listener of [...listeners]) listener({ matches: dark });
    },
  };
}

function fakeEnv(system: ReturnType<typeof fakeSystem> | null): ThemeEnv {
  return { root: document.documentElement, matchMedia: system?.matchMedia };
}

const root = () => document.documentElement;

afterEach(() => {
  // 端末の変化を追う購読を次のテストに持ち越さない
  applyTheme('light', fakeEnv(null));
  root().classList.remove('dark');
  root().style.colorScheme = '';
});

describe('applyTheme', () => {
  it('ダークは dark クラスと color-scheme を付ける', () => {
    applyTheme('dark', fakeEnv(null));
    expect(root().classList.contains('dark')).toBe(true);
    expect(root().style.colorScheme).toBe('dark');
  });

  it('ライトは dark クラスを外す（端末がダークでも）', () => {
    root().classList.add('dark');
    applyTheme('light', fakeEnv(fakeSystem(true)));
    expect(root().classList.contains('dark')).toBe(false);
    expect(root().style.colorScheme).toBe('light');
  });

  it('端末に合わせるは prefers-color-scheme: dark で決める', () => {
    const system = fakeSystem(true);
    applyTheme('system', fakeEnv(system));
    expect(system.matchMedia).toHaveBeenCalledWith('(prefers-color-scheme: dark)');
    expect(root().classList.contains('dark')).toBe(true);

    applyTheme('system', fakeEnv(fakeSystem(false)));
    expect(root().classList.contains('dark')).toBe(false);
    expect(root().style.colorScheme).toBe('light');
  });

  it('端末に合わせるは端末の切り替えを追う', () => {
    const system = fakeSystem(false);
    applyTheme('system', fakeEnv(system));
    expect(root().classList.contains('dark')).toBe(false);

    system.setDark(true);
    expect(root().classList.contains('dark')).toBe(true);
    expect(root().style.colorScheme).toBe('dark');

    system.setDark(false);
    expect(root().classList.contains('dark')).toBe(false);
  });

  it('端末に合わせるを繰り返しても購読は1つだけ', () => {
    const system = fakeSystem(false);
    applyTheme('system', fakeEnv(system));
    applyTheme('system', fakeEnv(system));
    applyTheme('system', fakeEnv(system));
    expect(system.listeners.size).toBe(1);
  });

  it('ライト・ダークに切り替えたら端末の変化を追うのをやめる', () => {
    const system = fakeSystem(false);
    applyTheme('system', fakeEnv(system));
    applyTheme('light', fakeEnv(system));
    expect(system.listeners.size).toBe(0);

    system.setDark(true);
    expect(root().classList.contains('dark')).toBe(false);

    applyTheme('system', fakeEnv(system));
    applyTheme('dark', fakeEnv(system));
    expect(system.listeners.size).toBe(0);
    system.setDark(false);
    expect(root().classList.contains('dark')).toBe(true);
  });

  it('matchMedia が無い環境の端末に合わせるはライト', () => {
    root().classList.add('dark');
    applyTheme('system', fakeEnv(null));
    expect(root().classList.contains('dark')).toBe(false);
  });

  it('描画先が無くても投げない', () => {
    const system = fakeSystem(true);
    expect(() => applyTheme('system', { root: null, matchMedia: system.matchMedia })).not.toThrow();
    expect(() => applyTheme('dark', { root: null })).not.toThrow();
  });
});

describe('initTheme', () => {
  it('保存した設定を読んで適用し、その設定を返す', () => {
    const storage = memoryStorage({ [THEME_KEY]: 'dark' });
    expect(initTheme(storage, fakeEnv(null))).toBe('dark');
    expect(root().classList.contains('dark')).toBe(true);
  });

  it('旧キーのダークは引き継ぐ', () => {
    const storage = memoryStorage({ [LEGACY_DARK_MODE_KEY]: '1' });
    expect(initTheme(storage, fakeEnv(null))).toBe('dark');
    expect(root().classList.contains('dark')).toBe(true);
  });
});
