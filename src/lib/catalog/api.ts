// カタログ API（/api/catalog/*）の呼び出し。型は ./types.ts が正。

import { ApiError, apiGet, getApiBase } from '../api';
import type {
  CatalogQuery,
  CatalogStats,
  CatalogWorkDetail,
  CatalogWorkPage,
} from './types';

/** サーバ側の上限（per は最大 100）。 */
export const MAX_PER = 100;

// src/lib/api.ts の TOKEN_KEY と同じ値（api.ts は書き換えず、書き出しの fetch にだけ使う）。
const TOKEN_KEY = 'triptych.apiToken';

/**
 * CatalogQuery を GET /api/catalog/works のクエリ文字列用の値に直す。
 * 空の値（空文字・未指定・空配列・不正な数）は落とす。has はカンマで連結し、per は 1〜MAX_PER に丸める。
 */
export function buildWorksQuery(q: CatalogQuery): Record<string, string> {
  const out: Record<string, string> = {};

  const text = q.q?.trim();
  if (text) out.q = text;
  if (q.kind) out.kind = q.kind;

  const has = [...new Set(q.has ?? [])];
  if (has.length > 0) out.has = has.join(',');

  if (q.status) out.status = q.status;
  if (q.year !== undefined && Number.isInteger(q.year)) out.year = String(q.year);
  if (q.season) out.season = q.season;
  if (q.sort) out.sort = q.sort;
  if (q.page !== undefined && Number.isInteger(q.page) && q.page >= 1) out.page = String(q.page);
  if (q.per !== undefined && Number.isFinite(q.per)) {
    out.per = String(Math.min(MAX_PER, Math.max(1, Math.floor(q.per))));
  }
  return out;
}

export function fetchCatalogStats(): Promise<CatalogStats> {
  return apiGet<CatalogStats>('/api/catalog/stats');
}

export function fetchCatalogWorks(q: CatalogQuery): Promise<CatalogWorkPage> {
  return apiGet<CatalogWorkPage>('/api/catalog/works', buildWorksQuery(q));
}

export function fetchCatalogWork(id: string): Promise<CatalogWorkDetail> {
  return apiGet<CatalogWorkDetail>(`/api/catalog/works/${encodeURIComponent(id)}`);
}

// ---------------------------------------------------------------------------
// 書き出し（CSV / JSON）
// ---------------------------------------------------------------------------

export type ExportFormat = 'csv' | 'json';

export function exportPath(format: ExportFormat): string {
  return `/api/catalog/export?format=${format}`;
}

/** Content-Disposition からファイル名を取り出す。無い・不正なら fallback。パス区切りは除く。 */
export function filenameFromDisposition(header: string | null | undefined, fallback: string): string {
  if (!header) return fallback;
  let name: string | undefined;

  const extended = /filename\*\s*=\s*([^']*)'[^']*'([^;]+)/i.exec(header);
  if (extended) {
    try {
      name = decodeURIComponent(extended[2].trim());
    } catch {
      name = undefined;
    }
  }
  if (!name) {
    const plain = /filename\s*=\s*(?:"([^"]*)"|([^;]+))/i.exec(header);
    name = (plain?.[1] ?? plain?.[2])?.trim();
  }
  const safe = name?.replace(/[\\/]/g, '_').trim();
  return safe ? safe : fallback;
}

/** Bearer トークンが設定されていることがあるので、<a href> ではなく fetch → Blob → ダウンロードにする。 */
export async function downloadCatalogExport(format: ExportFormat): Promise<string> {
  const headers: Record<string, string> = {};
  let token: string | null = null;
  try {
    token = localStorage.getItem(TOKEN_KEY);
  } catch {
    token = null;
  }
  if (token) headers['Authorization'] = `Bearer ${token}`;

  const path = exportPath(format);
  const res = await fetch(`${getApiBase()}${path}`, { headers });
  if (!res.ok) throw new ApiError(res.status, `${path}: ${res.status}`);

  const blob = await res.blob();
  const filename = filenameFromDisposition(res.headers.get('Content-Disposition'), `catalog.${format}`);

  const href = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = href;
  anchor.download = filename;
  anchor.style.display = 'none';
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(href), 10_000);
  return filename;
}
