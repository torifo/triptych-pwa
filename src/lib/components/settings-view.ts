// 設定画面用。最終エクスポートの古さ、ブラウザのストレージ（永続化と使用量）の読み出しと表示。

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
