// 設定値。localStorage の 'triptych.pref.<name>' に JSON で残し、読めない値は既定値に戻す（engine.ts も読むので svelte に依存しない）。

export type AutoSyncMode = 'launch' | 'hourly' | 'manual';
export type SyncSourceName = 'anilist' | 'syoboi' | 'ndl' | 'narou' | 'kakuyomu';
export type StartScreen = 'home' | 'library' | 'calendar' | 'catalog';
/** 深夜の時刻: 放送日のまま 25:30（既定）／実際の時計で翌 1:30 */
export type TimeNotation = 'late-night' | 'clock';
export type WeekStart = 'sun' | 'mon';
export type SyncSourceToggles = Record<SyncSourceName, boolean>;

export interface Preferences {
  /** 自動同期: 起動時（既定）／1時間ごと（開いている間）／手動のみ。 */
  autoSync: AutoSyncMode;
  /** 同期で照会するソースの ON/OFF（既定は全部 ON）。 */
  syncSources: SyncSourceToggles;
  /** アニメ化検知を同期のあとに走らせるか。 */
  detection: boolean;
  /** URL にハッシュが無い起動の時に開く画面。 */
  startScreen: StartScreen;
  /** 深夜の時刻の書き方。 */
  timeNotation: TimeNotation;
  /** カレンダーの月の升目の始まり。 */
  weekStart: WeekStart;
}

export type PreferenceName = keyof Preferences;

export const AUTO_SYNC_MODES: readonly AutoSyncMode[] = ['launch', 'hourly', 'manual'];
export const SYNC_SOURCES: readonly SyncSourceName[] = ['anilist', 'syoboi', 'ndl', 'narou', 'kakuyomu'];
export const START_SCREENS: readonly StartScreen[] = ['home', 'library', 'calendar', 'catalog'];
export const TIME_NOTATIONS: readonly TimeNotation[] = ['late-night', 'clock'];
export const WEEK_STARTS: readonly WeekStart[] = ['sun', 'mon'];

export const PREF_KEY_PREFIX = 'triptych.pref.';

export const PREF_DEFAULTS: Readonly<Preferences> = Object.freeze({
  autoSync: 'launch',
  syncSources: Object.freeze({ anilist: true, syoboi: true, ndl: true, narou: true, kakuyomu: true }),
  detection: true,
  startScreen: 'home',
  timeNotation: 'late-night',
  weekStart: 'sun',
});

export function prefKey(name: PreferenceName): string {
  return `${PREF_KEY_PREFIX}${name}`;
}

// ---------------------------------------------------------------------------
// 値の検証（保存された値は信用しない）
// ---------------------------------------------------------------------------

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

const PARSERS: { [K in PreferenceName]: (raw: unknown) => Preferences[K] | undefined } = {
  autoSync: (raw) => (AUTO_SYNC_MODES as readonly unknown[]).includes(raw) ? (raw as AutoSyncMode) : undefined,
  syncSources: (raw) => {
    if (!isPlainObject(raw)) return undefined;
    const toggles = { ...PREF_DEFAULTS.syncSources };
    for (const source of SYNC_SOURCES) {
      if (typeof raw[source] === 'boolean') toggles[source] = raw[source];
    }
    return toggles;
  },
  detection: (raw) => (typeof raw === 'boolean' ? raw : undefined),
  startScreen: (raw) => (START_SCREENS as readonly unknown[]).includes(raw) ? (raw as StartScreen) : undefined,
  timeNotation: (raw) => (TIME_NOTATIONS as readonly unknown[]).includes(raw) ? (raw as TimeNotation) : undefined,
  weekStart: (raw) => (WEEK_STARTS as readonly unknown[]).includes(raw) ? (raw as WeekStart) : undefined,
};

const NAMES = Object.keys(PARSERS) as PreferenceName[];

/** 返した値を書き換えられても、保存した値や既定値が変わらないように（値はどれも浅い）。 */
function copy<K extends PreferenceName>(value: Preferences[K]): Preferences[K] {
  const raw: unknown = value;
  return (isPlainObject(raw) ? { ...raw } : raw) as Preferences[K];
}

function same(a: unknown, b: unknown): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

// ---------------------------------------------------------------------------
// ストア
// ---------------------------------------------------------------------------

type PrefStorage = Pick<Storage, 'getItem' | 'setItem'>;

/** localStorage そのものに触れるだけで例外になる環境があるので、取り出しも包む。 */
function browserStorage(): PrefStorage | null {
  try {
    return typeof localStorage === 'undefined' ? null : localStorage;
  } catch {
    return null;
  }
}

export interface PreferenceStore {
  get<K extends PreferenceName>(name: K): Preferences[K];
  all(): Preferences;
  /** 不正な値は保存しない。保存できない環境でも、このストアの中では効く。 */
  set<K extends PreferenceName>(name: K, value: Preferences[K]): void;
  /** 同期するソースを1つだけ切り替える。 */
  setSyncSource(source: SyncSourceName, enabled: boolean): void;
  /** 今の値がすぐ届き、以後は変わるたびに全体が届く（Svelte のストアの約束どおり）。戻り値で解除。 */
  subscribe(listener: (prefs: Preferences) => void): () => void;
}

export function createPreferenceStore(storage: PrefStorage | null = browserStorage()): PreferenceStore {
  // 保存に失敗した値（プライベートモード・容量超過）。保存できた値は毎回 storage から読む
  const memory: Partial<Preferences> = {};
  const listeners = new Set<(prefs: Preferences) => void>();

  function get<K extends PreferenceName>(name: K): Preferences[K] {
    if (name in memory) return copy(memory[name] as Preferences[K]);
    try {
      const text = storage?.getItem(prefKey(name));
      if (text != null) {
        const parsed = PARSERS[name](JSON.parse(text));
        if (parsed !== undefined) return parsed;
      }
    } catch {
      // 読めない・壊れた JSON は既定値
    }
    return copy(PREF_DEFAULTS[name] as Preferences[K]);
  }

  function all(): Preferences {
    const prefs = {} as Record<PreferenceName, unknown>;
    for (const name of NAMES) prefs[name] = get(name);
    return prefs as unknown as Preferences;
  }

  function notify(): void {
    const prefs = all();
    for (const listener of [...listeners]) {
      try {
        listener(prefs);
      } catch {
        // 1つの購読者の失敗で、ほかの購読者への通知を止めない
      }
    }
  }

  function set<K extends PreferenceName>(name: K, value: Preferences[K]): void {
    const parsed = PARSERS[name](value);
    if (parsed === undefined) return;
    if (same(get(name), parsed)) return;
    try {
      if (!storage) throw new Error('no storage');
      storage.setItem(prefKey(name), JSON.stringify(parsed));
      delete memory[name];
    } catch {
      memory[name] = parsed;
    }
    notify();
  }

  return {
    get,
    all,
    set,
    setSyncSource(source, enabled) {
      set('syncSources', { ...get('syncSources'), [source]: enabled });
    },
    subscribe(listener) {
      listeners.add(listener);
      listener(all());
      return () => {
        listeners.delete(listener);
      };
    },
  };
}

/** アプリ全体で使うストア（設定画面・App・同期エンジン）。 */
export const preferences: PreferenceStore = createPreferenceStore();

/** 保存された設定をまとめて読む（テストでは storage を差し替える）。 */
export function loadPreferences(storage: PrefStorage | null = browserStorage()): Preferences {
  return createPreferenceStore(storage).all();
}

/** ON のソース（並びは SYNC_SOURCES のまま）。 */
export function enabledSyncSources(toggles: SyncSourceToggles): SyncSourceName[] {
  return SYNC_SOURCES.filter((source) => toggles[source]);
}

// ---------------------------------------------------------------------------
// 自動同期
// ---------------------------------------------------------------------------

export const AUTO_SYNC_INTERVAL_MS = 60 * 60 * 1000;

/** 自動同期の仕方。onLaunch: 起動時に同期する／repeatMs: 開いている間、この間隔で同期する。 */
export function autoSyncPlan(mode: AutoSyncMode): { onLaunch: boolean; repeatMs: number | null } {
  switch (mode) {
    case 'hourly':
      return { onLaunch: true, repeatMs: AUTO_SYNC_INTERVAL_MS };
    case 'manual':
      return { onLaunch: false, repeatMs: null };
    default:
      return { onLaunch: true, repeatMs: null };
  }
}

/** 前回の同期から intervalMs 以上たったか。未同期・読めない時刻なら true。 */
export function isAutoSyncDue(
  lastSyncAt: string | null | undefined,
  now: number = Date.now(),
  intervalMs: number = AUTO_SYNC_INTERVAL_MS,
): boolean {
  if (!lastSyncAt) return true;
  const t = Date.parse(lastSyncAt);
  if (Number.isNaN(t)) return true;
  return now - t >= intervalMs;
}

// ---------------------------------------------------------------------------
// 起動時に開く画面
// ---------------------------------------------------------------------------

const START_HASHES: Record<StartScreen, string> = {
  home: '#/',
  library: '#/library',
  calendar: '#/calendar',
  catalog: '#/catalog',
};

export function startHash(screen: StartScreen): string {
  return START_HASHES[screen];
}

/**
 * 起動時に移すハッシュ。URL にハッシュが無い起動（ホーム画面のアイコンなど）の時だけ設定に従い、
 * 共有されたリンクやリロード（ハッシュがある）では今の画面のまま（null）。Home なら移す必要がない。
 */
export function startRedirect(currentHash: string, screen: StartScreen): string | null {
  if (currentHash !== '' && currentHash !== '#') return null;
  return screen === 'home' ? null : startHash(screen);
}
