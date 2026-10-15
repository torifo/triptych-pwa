// 設定画面用。最終エクスポートの古さ、ストレージ、設定の選択肢の名前、カタログの件数、この app の出典と版。

import type { CatalogSourceCount } from '../catalog/types';
import { formatJstDateTime } from '../catalog/view';
import type { AutoSyncMode, StartScreen, SyncSourceName } from '../preferences';

// vite.config.ts の define がビルド時に埋める（テストでは未定義）
declare const __APP_VERSION__: string | undefined;
declare const __APP_BUILT_AT__: string | undefined;

export const EXPORT_STALE_DAYS = 30;

const DAY_MS = 24 * 60 * 60 * 1000;

/** 最終エクスポートが無い・読めない・staleDays 日を超えて古いなら true。 */
export function isExportStale(
  lastExportAt: string | undefined,
  now: number = Date.now(),
  staleDays: number = EXPORT_STALE_DAYS,
): boolean {
  if (!lastExportAt) return true;
  const t = new Date(lastExportAt).getTime();
  if (Number.isNaN(t)) return true;
  return now - t > staleDays * DAY_MS;
}

const BYTE_UNITS = ['B', 'KB', 'MB', 'GB', 'TB'] as const;

function digitsFor(value: number): number {
  if (value >= 100) return 0;
  if (value >= 10) return 1;
  return 2;
}

/** バイト数を `12.3 MB` のように。B は小数なし、他は有効 3 桁ほど。 */
export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return '—';
  if (bytes < 1024) return `${Math.round(bytes)} B`;

  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < BYTE_UNITS.length - 1) {
    value /= 1024;
    unit++;
  }
  let text = value.toFixed(digitsFor(value));
  // 1023.99 KB のように丸めて 1024 になる時は、次の単位で書き直す
  if (Number(text) >= 1024 && unit < BYTE_UNITS.length - 1) {
    value /= 1024;
    unit++;
    text = value.toFixed(digitsFor(value));
  }
  return `${text} ${BYTE_UNITS[unit]}`;
}

/** `使用量 / 上限` の表示。使用量が分からなければ —、上限が分からなければ使用量だけ。 */
export function formatUsage({ usage, quota }: { usage: number | null; quota: number | null }): string {
  if (usage === null) return '—';
  const used = formatBytes(usage);
  return quota === null ? used : `${used} / ${formatBytes(quota)}`;
}

/** navigator.storage のうち使う部分。どれも無いブラウザがある。 */
export type StorageLike = Partial<{
  persisted: () => Promise<boolean>;
  persist: () => Promise<boolean>;
  estimate: () => Promise<{ usage?: number; quota?: number }>;
}>;

export interface StorageInfo {
  /** 永続化済みか。API が無い・失敗した時は null（不明）。 */
  persisted: boolean | null;
  usage: number | null;
  quota: number | null;
  /** 「永続化を頼む」を出せるか（persist がある）。 */
  canPersist: boolean;
}

/** ストレージの節を出せるか。persisted も estimate も無ければ節ごと出さない。 */
export function hasStorageApi(storage: StorageLike | null | undefined): boolean {
  return typeof storage?.persisted === 'function' || typeof storage?.estimate === 'function';
}

/** 永続化の状態と使用量。API が無ければ null。個々の呼び出しが失敗したら、その項目だけ null。 */
export async function readStorageInfo(storage: StorageLike | null | undefined): Promise<StorageInfo | null> {
  if (!storage || !hasStorageApi(storage)) return null;

  let persisted: boolean | null = null;
  if (typeof storage.persisted === 'function') {
    try {
      persisted = await storage.persisted();
    } catch {
      persisted = null;
    }
  }

  let usage: number | null = null;
  let quota: number | null = null;
  if (typeof storage.estimate === 'function') {
    try {
      const estimate = await storage.estimate();
      usage = typeof estimate.usage === 'number' ? estimate.usage : null;
      quota = typeof estimate.quota === 'number' ? estimate.quota : null;
    } catch {
      usage = null;
      quota = null;
    }
  }

  return { persisted, usage, quota, canPersist: typeof storage.persist === 'function' };
}

/** 永続化をブラウザに頼む。許可なら true、断られたら false、頼めない・失敗は null。 */
export async function requestPersistent(storage: StorageLike | null | undefined): Promise<boolean | null> {
  if (typeof storage?.persist !== 'function') return null;
  try {
    return await storage.persist();
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// 設定の選択肢の名前
// ---------------------------------------------------------------------------

export const AUTO_SYNC_LABELS: Record<AutoSyncMode, string> = {
  launch: '起動時',
  hourly: '1時間ごと',
  manual: '手動のみ',
};

export const AUTO_SYNC_NOTES: Record<AutoSyncMode, string> = {
  launch: 'アプリを開いた時に同期します（前回から1時間以内なら飛ばします）。',
  hourly: '開いた時と、開いている間は1時間ごとに同期します。',
  manual: '自動では同期しません。「今すぐ同期」で取りに行きます。',
};

/** 同期するソース。what は何を取りに行くか。 */
export const SYNC_SOURCE_LABELS: Record<SyncSourceName, { name: string; what: string }> = {
  anilist: { name: 'AniList', what: 'アニメの次話・新期・表紙' },
  syoboi: { name: 'しょぼかる', what: '放送局・配信開始' },
  ndl: { name: 'NDL Search', what: '文庫・漫画の新刊' },
  narou: { name: 'なろう', what: 'Web 小説の更新' },
  kakuyomu: { name: 'カクヨム', what: 'Web 小説の更新' },
};

export const START_SCREEN_LABELS: Record<StartScreen, string> = {
  home: 'Home',
  library: 'Library',
  calendar: 'カレンダー',
  catalog: 'カタログ',
};

// ---------------------------------------------------------------------------
// Library を空にする
// ---------------------------------------------------------------------------

/** 確かめの入力が作品数そのものか（全角数字・前後の空白は許す）。作品が無ければ消せない。 */
export function canClearLibrary(typed: string, works: number): boolean {
  if (works <= 0) return false;
  const half = typed.trim().replace(/[０-９]/g, (c) => String.fromCharCode(c.charCodeAt(0) - 0xfee0));
  return half === String(works);
}

// ---------------------------------------------------------------------------
// カタログ
// ---------------------------------------------------------------------------

const CATALOG_SOURCE_LABELS: Record<string, string> = {
  anilist: 'AniList',
  syoboi: 'しょぼかる',
  narou: 'なろう',
  kakuyomu: 'カクヨム',
  ndl: 'NDL Search',
  jpro: 'うち JPRO 近刊',
};

export function catalogSourceLabel(source: string): string {
  return CATALOG_SOURCE_LABELS[source] ?? source;
}

export function formatCount(n: number): string {
  return n.toLocaleString('ja-JP');
}

export interface CatalogSourceRow {
  source: string;
  label: string;
  entries: string;
  /** JST の `2026-10-14 21:30`。収集したことが無ければ null。 */
  harvestedAt: string | null;
  errorSlices: number;
}

/** ソースごとの件数・最終収集の行（並びは collector のまま）。 */
export function catalogSourceRows(sources: readonly CatalogSourceCount[]): CatalogSourceRow[] {
  return sources.map((s) => ({
    source: s.source,
    label: catalogSourceLabel(s.source),
    entries: formatCount(s.entries),
    harvestedAt: s.lastHarvestAt ? formatJstDateTime(s.lastHarvestAt) : null,
    errorSlices: s.errorSlices,
  }));
}

// ---------------------------------------------------------------------------
// この app について
// ---------------------------------------------------------------------------

export interface DataSource {
  name: string;
  url: string;
  /** 何に使っているか。 */
  note: string;
}

export const DATA_SOURCES: readonly DataSource[] = [
  { name: 'AniList', url: 'https://anilist.co/', note: 'アニメ・原作の作品情報、放送予定、アニメ化検知' },
  { name: 'しょぼいカレンダー', url: 'https://cal.syoboi.jp/', note: '放送局・配信の予定' },
  {
    name: '国立国会図書館サーチ（NDL Search）',
    url: 'https://ndlsearch.ndl.go.jp/',
    note: '文庫・漫画の刊行情報（出版情報登録センター JPRO の近刊情報を含む）',
  },
  { name: '小説家になろう', url: 'https://syosetu.com/', note: 'Web 小説の更新' },
  { name: 'カクヨム', url: 'https://kakuyomu.jp/', note: 'Web 小説の更新' },
  { name: 'openBD', url: 'https://openbd.jp/', note: '書誌情報' },
];

/** ビルド時に埋めた版（git の短いハッシュ。git が無い・埋めていなければ dev）とビルドした時刻。 */
export function buildInfo(): { version: string; builtAt: string | null } {
  const version = typeof __APP_VERSION__ === 'string' && __APP_VERSION__ ? __APP_VERSION__ : 'dev';
  const builtAt = typeof __APP_BUILT_AT__ === 'string' && __APP_BUILT_AT__ ? __APP_BUILT_AT__ : null;
  return { version, builtAt };
}
