import { describe, expect, it } from 'vitest';
import {
  EXPORT_STALE_DAYS,
  formatBytes,
  formatUsage,
  hasStorageApi,
  isExportStale,
  readStorageInfo,
  requestPersistent,
} from './settings-view';

const DAY_MS = 24 * 60 * 60 * 1000;
const NOW = Date.parse('2026-10-15T00:00:00Z');

describe('isExportStale', () => {
  it('一度も書き出していなければ要エクスポート', () => {
    expect(isExportStale(undefined, NOW)).toBe(true);
  });

  it('30 日を超えたら要エクスポート、ちょうど 30 日まではまだ', () => {
    expect(EXPORT_STALE_DAYS).toBe(30);
    expect(isExportStale(new Date(NOW - 29 * DAY_MS).toISOString(), NOW)).toBe(false);
    expect(isExportStale(new Date(NOW - 30 * DAY_MS).toISOString(), NOW)).toBe(false);
    expect(isExportStale(new Date(NOW - 30 * DAY_MS - 1).toISOString(), NOW)).toBe(true);
  });

  it('日付が読めなければ要エクスポート', () => {
    expect(isExportStale('not-a-date', NOW)).toBe(true);
  });

  it('日数は指定できる', () => {
    expect(isExportStale(new Date(NOW - 8 * DAY_MS).toISOString(), NOW, 7)).toBe(true);
  });
});

describe('formatBytes', () => {
  it('1KB 未満は B のまま', () => {
    expect(formatBytes(0)).toBe('0 B');
    expect(formatBytes(1023)).toBe('1023 B');
  });

  it('桁に応じて小数を絞る', () => {
    expect(formatBytes(1024)).toBe('1.00 KB');
    expect(formatBytes(1536)).toBe('1.50 KB');
    expect(formatBytes(10 * 1024 * 1024)).toBe('10.0 MB');
    expect(formatBytes(123 * 1024 * 1024)).toBe('123 MB');
    expect(formatBytes(1.5 * 1024 ** 3)).toBe('1.50 GB');
  });

  it('丸めて 1024 になる時は次の単位へ繰り上げる', () => {
    expect(formatBytes(1024 * 1024 - 1)).toBe('1.00 MB');
  });

  it('TB より上は TB のまま', () => {
    expect(formatBytes(2048 * 1024 ** 4)).toBe('2048 TB');
  });

  it('数でない・負の値は ダッシュ', () => {
    expect(formatBytes(Number.NaN)).toBe('—');
    expect(formatBytes(-1)).toBe('—');
    expect(formatBytes(Number.POSITIVE_INFINITY)).toBe('—');
  });
});

describe('formatUsage', () => {
  it('使用量と上限を並べる', () => {
    expect(formatUsage({ usage: 1536, quota: 2 * 1024 ** 3 })).toBe('1.50 KB / 2.00 GB');
  });

  it('上限が分からなければ使用量だけ', () => {
    expect(formatUsage({ usage: 1536, quota: null })).toBe('1.50 KB');
  });

  it('使用量が分からなければダッシュ', () => {
    expect(formatUsage({ usage: null, quota: 100 })).toBe('—');
  });
});

describe('hasStorageApi', () => {
  it('persisted か estimate のどちらかがあれば使える', () => {
    expect(hasStorageApi({ persisted: async () => true })).toBe(true);
    expect(hasStorageApi({ estimate: async () => ({}) })).toBe(true);
  });

  it('API が無い・空なら使えない', () => {
    expect(hasStorageApi(undefined)).toBe(false);
    expect(hasStorageApi(null)).toBe(false);
    expect(hasStorageApi({})).toBe(false);
  });
});

describe('readStorageInfo', () => {
  it('永続化の状態と使用量・上限を返す', async () => {
    const info = await readStorageInfo({
      persisted: async () => true,
      persist: async () => true,
      estimate: async () => ({ usage: 1234, quota: 5678 }),
    });
    expect(info).toEqual({ persisted: true, usage: 1234, quota: 5678, canPersist: true });
  });

  it('未永続化で persist があれば頼める', async () => {
    const info = await readStorageInfo({
      persisted: async () => false,
      persist: async () => false,
      estimate: async () => ({ usage: 1, quota: 2 }),
    });
    expect(info?.persisted).toBe(false);
    expect(info?.canPersist).toBe(true);
  });

  it('persist が無ければ頼めない', async () => {
    const info = await readStorageInfo({ persisted: async () => false, estimate: async () => ({}) });
    expect(info).toEqual({ persisted: false, usage: null, quota: null, canPersist: false });
  });

  it('片方の API が無くても、あるほうは返す', async () => {
    expect(await readStorageInfo({ estimate: async () => ({ usage: 10, quota: 20 }) })).toEqual({
      persisted: null,
      usage: 10,
      quota: 20,
      canPersist: false,
    });
    expect(await readStorageInfo({ persisted: async () => true })).toEqual({
      persisted: true,
      usage: null,
      quota: null,
      canPersist: false,
    });
  });

  it('呼び出しが失敗した項目は null にして、投げない', async () => {
    const info = await readStorageInfo({
      persisted: async () => {
        throw new Error('SecurityError');
      },
      estimate: async () => {
        throw new Error('SecurityError');
      },
    });
    expect(info).toEqual({ persisted: null, usage: null, quota: null, canPersist: false });
  });

  it('API が無ければ null（節ごと出さない）', async () => {
    expect(await readStorageInfo(undefined)).toBeNull();
    expect(await readStorageInfo(null)).toBeNull();
    expect(await readStorageInfo({})).toBeNull();
  });
});

describe('requestPersistent', () => {
  it('persist の結果をそのまま返す', async () => {
    expect(await requestPersistent({ persist: async () => true })).toBe(true);
    expect(await requestPersistent({ persist: async () => false })).toBe(false);
  });

  it('persist が無い・失敗したら null', async () => {
    expect(await requestPersistent(undefined)).toBeNull();
    expect(await requestPersistent({})).toBeNull();
    expect(
      await requestPersistent({
        persist: async () => {
          throw new Error('NotAllowedError');
        },
      }),
    ).toBeNull();
  });
});
