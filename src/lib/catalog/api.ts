// カタログ API（/api/catalog/*）の呼び出し。型は ./types.ts が正。

import { ApiError, TOKEN_KEY, apiGet, getApiBase, readErrorDetail } from '../api';
import type {
  CatalogEventPage,
  CatalogEventQuery,
  CatalogQuery,
  CatalogStats,
  CatalogWorkDetail,
  CatalogWorkPage,
} from './types';
import { normalizeWorkDetail, normalizeWorkSummary } from './origin';

/** サーバ側の上限（per は最大 100）。 */
export const MAX_PER = 100;

/** カタログを作る collector 側のコマンド。 */
export const BUILD_COMMAND = 'python -m backend.catalog build';

/** collector（backend/api/catalog.py）が 404 に付ける detail。同じ 404 でも理由が違う。 */
export const DETAIL_NOT_BUILT = 'catalog not built';
export const DETAIL_WORK_NOT_FOUND = 'work not found';

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

export async function fetchCatalogWorks(q: CatalogQuery): Promise<CatalogWorkPage> {
  const page = await apiGet<CatalogWorkPage>('/api/catalog/works', buildWorksQuery(q));
  return { ...page, items: page.items.map(normalizeWorkSummary) };
}

export async function fetchCatalogWork(id: string): Promise<CatalogWorkDetail> {
  return normalizeWorkDetail(await apiGet<CatalogWorkDetail>(`/api/catalog/works/${encodeURIComponent(id)}`));
}

// ---------------------------------------------------------------------------
// カレンダーの重ね表示（期間の出来事）
// ---------------------------------------------------------------------------

/** GET /api/catalog/events の上限（期間は最大 62 日、1 回 600 件）。 */
export const MAX_EVENT_DAYS = 62;
export const MAX_EVENT_ITEMS = 600;

/** CatalogEventQuery をクエリ文字列用の値に直す。kind はカンマで連結し、空なら付けない（全種）。 */
export function buildEventsQuery(q: CatalogEventQuery): Record<string, string> {
  const out: Record<string, string> = { from: q.from, to: q.to };
  const kinds = [...new Set(q.kinds ?? [])];
  if (kinds.length > 0) out.kind = kinds.join(',');
  return out;
}

export function fetchCatalogEvents(q: CatalogEventQuery): Promise<CatalogEventPage> {
  return apiGet<CatalogEventPage>('/api/catalog/events', buildEventsQuery(q));
}

export type WorkLoadFailureKind = 'not-built' | 'not-found' | 'error';

export interface WorkLoadFailure {
  kind: WorkLoadFailureKind;
  title: string;
  message: string;
}

/**
 * 詳細の取得失敗を、画面に出す文言に直す。
 * 404 は detail で「カタログ未作成」と「作品なし」を分ける。detail が無い 404（catalog の無い collector や
 * 間のプロキシ）や 404 以外は、どちらとも決めつけずに取得失敗として扱う。
 */
export function describeWorkLoadError(err: unknown): WorkLoadFailure {
  if (err instanceof ApiError && err.status === 404) {
    if (err.detail === DETAIL_NOT_BUILT) {
      return { kind: 'not-built', title: 'カタログ未作成', message: 'collector 側で次のコマンドを実行すると作られます。' };
    }
    if (err.detail === DETAIL_WORK_NOT_FOUND) {
      return {
        kind: 'not-found',
        title: '作品が見つかりません',
        message: 'カタログにない作品です。作品 ID が古いか、カタログを作り直した可能性があります。一覧から探し直してください。',
      };
    }
  }
  return {
    kind: 'error',
    title: '作品を取得できませんでした',
    message:
      err instanceof ApiError ? `collector が ${err.status} を返しました。` : 'collector に接続できませんでした。通信を確認してください。',
  };
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
  if (!res.ok) throw new ApiError(res.status, `${path}: ${res.status}`, await readErrorDetail(res));

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
