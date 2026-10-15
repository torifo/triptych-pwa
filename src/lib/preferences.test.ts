import { describe, expect, it, vi } from 'vitest';
import {
  AUTO_SYNC_INTERVAL_MS,
  PREF_DEFAULTS,
  SYNC_SOURCES,
  START_SCREENS,
  autoSyncPlan,
  createPreferenceStore,
  enabledSyncSources,
  isAutoSyncDue,
  loadPreferences,
  prefKey,
  startHash,
  startRedirect,
} from './preferences';

/** localStorage の代わり。getItem / setItem だけ。 */
function memoryStorage(initial: Record<string, string> = {}) {
  const data = new Map(Object.entries(initial));
  return {
    data,
    getItem: (key: string) => data.get(key) ?? null,
    setItem: (key: string, value: string) => {
      data.set(key, value);
    },
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

describe('既定値と保存キー', () => {
  it('保存キーは triptych.pref.<name>', () => {
    expect(prefKey('autoSync')).toBe('triptych.pref.autoSync');
    expect(prefKey('startScreen')).toBe('triptych.pref.startScreen');
  });

  it('既定は起動時に同期・全ソース ON・検知 ON・Home から', () => {
    expect(PREF_DEFAULTS).toEqual({
      autoSync: 'launch',
      syncSources: { anilist: true, syoboi: true, ndl: true, narou: true, kakuyomu: true },
      detection: true,
      startScreen: 'home',
    });
    expect(SYNC_SOURCES).toEqual(['anilist', 'syoboi', 'ndl', 'narou', 'kakuyomu']);
    expect(START_SCREENS).toEqual(['home', 'library', 'calendar', 'catalog']);
  });

  it('何も保存されていなければ既定値', () => {
    expect(loadPreferences(memoryStorage())).toEqual(PREF_DEFAULTS);
    expect(loadPreferences(null)).toEqual(PREF_DEFAULTS);
  });
});

describe('createPreferenceStore', () => {
  it('保存した値を JSON で残し、読み直せる', () => {
    const storage = memoryStorage();
    const prefs = createPreferenceStore(storage);
    prefs.set('autoSync', 'hourly');
    prefs.set('detection', false);
    expect(storage.data.get('triptych.pref.autoSync')).toBe('"hourly"');
    expect(storage.data.get('triptych.pref.detection')).toBe('false');
    expect(prefs.get('autoSync')).toBe('hourly');
    // 別のストア（再起動後）からも読める
    expect(createPreferenceStore(storage).get('detection')).toBe(false);
  });

  it('知らない値・壊れた JSON は既定値に戻す', () => {
    const storage = memoryStorage({
      'triptych.pref.autoSync': '"weekly"',
      'triptych.pref.detection': 'yes',
      'triptych.pref.startScreen': '{',
      'triptych.pref.syncSources': '"all"',
    });
    expect(loadPreferences(storage)).toEqual(PREF_DEFAULTS);
  });

  it('ソースの ON/OFF は、真偽でない・知らないソースを捨て、欠けたソースは ON', () => {
    const storage = memoryStorage({
      'triptych.pref.syncSources': JSON.stringify({ ndl: false, narou: 'no', openbd: false }),
    });
    expect(loadPreferences(storage).syncSources).toEqual({
      anilist: true,
      syoboi: true,
      ndl: false,
      narou: true,
      kakuyomu: true,
    });
  });

  it('ソースの ON/OFF は、ほかのソースを変えずに1つだけ切り替えられる', () => {
    const prefs = createPreferenceStore(memoryStorage());
    prefs.setSyncSource('syoboi', false);
    prefs.setSyncSource('kakuyomu', false);
    prefs.setSyncSource('syoboi', true);
    expect(prefs.get('syncSources')).toEqual({ ...PREF_DEFAULTS.syncSources, kakuyomu: false });
  });

  it('返した値を書き換えても、保存した値は変わらない', () => {
    const prefs = createPreferenceStore(memoryStorage());
    prefs.get('syncSources').anilist = false;
    prefs.all().syncSources.ndl = false;
    expect(prefs.get('syncSources')).toEqual(PREF_DEFAULTS.syncSources);
  });

  it('localStorage が使えない環境でも、その場では切り替えが効く（投げない）', () => {
    const prefs = createPreferenceStore(throwing);
    expect(prefs.get('autoSync')).toBe('launch');
    expect(() => prefs.set('autoSync', 'manual')).not.toThrow();
    expect(prefs.get('autoSync')).toBe('manual');
  });

  it('不正な値は保存しない', () => {
    const storage = memoryStorage();
    const prefs = createPreferenceStore(storage);
    prefs.set('startScreen', 'settings' as never);
    expect(storage.data.size).toBe(0);
    expect(prefs.get('startScreen')).toBe('home');
  });

  it('購読すると今の値がすぐ届き、変更のたびに全体が届く。解除後は届かない', () => {
    const prefs = createPreferenceStore(memoryStorage());
    const listener = vi.fn();
    const stop = prefs.subscribe(listener);
    expect(listener).toHaveBeenCalledTimes(1);
    expect(listener).toHaveBeenLastCalledWith(PREF_DEFAULTS);

    prefs.set('autoSync', 'manual');
    expect(listener).toHaveBeenCalledTimes(2);
    expect(listener.mock.lastCall?.[0]).toMatchObject({ autoSync: 'manual' });

    stop();
    prefs.set('autoSync', 'hourly');
    expect(listener).toHaveBeenCalledTimes(2);
  });

  it('同じ値を入れ直しても通知しない', () => {
    const prefs = createPreferenceStore(memoryStorage());
    const listener = vi.fn();
    prefs.subscribe(listener);
    prefs.set('autoSync', 'launch');
    prefs.setSyncSource('anilist', true);
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it('購読者が投げても、ほかの購読者には届く', () => {
    const prefs = createPreferenceStore(memoryStorage());
    const second = vi.fn();
    prefs.subscribe((p) => {
      if (p.autoSync === 'manual') throw new Error('boom');
    });
    prefs.subscribe(second);
    expect(() => prefs.set('autoSync', 'manual')).not.toThrow();
    expect(second).toHaveBeenCalledTimes(2);
  });
});

describe('enabledSyncSources', () => {
  it('ON のソースだけを並び順どおりに返す', () => {
    expect(enabledSyncSources(PREF_DEFAULTS.syncSources)).toEqual(SYNC_SOURCES);
    expect(enabledSyncSources({ ...PREF_DEFAULTS.syncSources, syoboi: false, narou: false })).toEqual([
      'anilist',
      'ndl',
      'kakuyomu',
    ]);
  });
});

describe('自動同期', () => {
  it('起動時: 起動の時だけ。1時間ごと: 起動時と、開いている間 1 時間ごと。手動: どちらもしない', () => {
    expect(autoSyncPlan('launch')).toEqual({ onLaunch: true, repeatMs: null });
    expect(autoSyncPlan('hourly')).toEqual({ onLaunch: true, repeatMs: AUTO_SYNC_INTERVAL_MS });
    expect(autoSyncPlan('manual')).toEqual({ onLaunch: false, repeatMs: null });
    expect(AUTO_SYNC_INTERVAL_MS).toBe(60 * 60 * 1000);
  });

  it('前回の同期から間隔が過ぎていれば同期する。未同期・読めない時刻も同期する', () => {
    const now = Date.parse('2026-10-15T12:00:00Z');
    expect(isAutoSyncDue(null, now)).toBe(true);
    expect(isAutoSyncDue('not-a-date', now)).toBe(true);
    expect(isAutoSyncDue('2026-10-15T11:30:00Z', now)).toBe(false);
    expect(isAutoSyncDue('2026-10-15T11:00:00Z', now)).toBe(true);
    expect(isAutoSyncDue('2026-10-15T11:50:00Z', now, 5 * 60 * 1000)).toBe(true);
  });
});

describe('起動時に開く画面', () => {
  it('画面ごとのハッシュ', () => {
    expect(startHash('home')).toBe('#/');
    expect(startHash('library')).toBe('#/library');
    expect(startHash('calendar')).toBe('#/calendar');
    expect(startHash('catalog')).toBe('#/catalog');
  });

  it('URL にハッシュが無い起動の時だけ、設定の画面へ移す', () => {
    expect(startRedirect('', 'library')).toBe('#/library');
    expect(startRedirect('#', 'calendar')).toBe('#/calendar');
  });

  it('共有されたリンクやリロード（ハッシュがある）では移さない。Home の時も移さない', () => {
    expect(startRedirect('#/', 'library')).toBeNull();
    expect(startRedirect('#/work/abc', 'library')).toBeNull();
    expect(startRedirect('#/settings', 'catalog')).toBeNull();
    expect(startRedirect('', 'home')).toBeNull();
  });
});
