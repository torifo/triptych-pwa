// 一覧の絞り込み状態 <-> URL（ハッシュのクエリ）。画面から戻った時に復元するための純関数群。

import { buildWorksQuery } from './api';
import type { CatalogKind, CatalogQuery, CatalogSort } from './types';

export const PAGE_SIZE = 30;
export const SEARCH_DEBOUNCE_MS = 300;
export const DEFAULT_SORT: CatalogSort = 'popularity';

const KINDS: readonly CatalogKind[] = ['anime', 'manga', 'novel', 'web-novel', 'book'];
const STATUSES = ['RELEASING', 'NOT_YET_RELEASED', 'FINISHED'] as const;
const SEASONS = ['WINTER', 'SPRING', 'SUMMER', 'FALL'] as const;
const SORTS: readonly CatalogSort[] = ['popularity', 'recent', 'upcoming', 'title'];

function pick<T extends string>(allowed: readonly T[], value: string | undefined): T | undefined {
  return allowed.find((a) => a === value);
}

/** ハッシュのクエリ（文字列だけの辞書）を CatalogQuery に直す。不正な値は捨てる。 */
export function parseCatalogQuery(raw: Record<string, string>): CatalogQuery {
  const out: CatalogQuery = {};

  const text = raw.q?.trim();
  if (text) out.q = text;

  const kind = pick(KINDS, raw.kind);
  if (kind) out.kind = kind;

  const has = (raw.has ?? '')
    .split(',')
    .map((s) => pick(KINDS, s.trim()))
    .filter((k): k is CatalogKind => k !== undefined);
  if (has.length > 0) out.has = [...new Set(has)];

  const status = pick(STATUSES, raw.status);
  if (status) out.status = status;

  if (/^\d{4}$/.test(raw.year ?? '')) out.year = Number(raw.year);

  const season = pick(SEASONS, raw.season);
  if (season) out.season = season;

  const sort = pick(SORTS, raw.sort);
  if (sort) out.sort = sort;

  if (/^\d{1,6}$/.test(raw.page ?? '')) {
    const page = Number(raw.page);
    if (page >= 1) out.page = page;
  }
  return out;
}

/** URL に載せる値。既定値（人気順・1ページ目）と per は載せない。 */
export function serializeCatalogQuery(q: CatalogQuery): Record<string, string> {
  const out = buildWorksQuery({ ...q, per: undefined });
  if (out.sort === DEFAULT_SORT) delete out.sort;
  if (out.page === '1') delete out.page;
  return out;
}

/** `#/catalog?kind=anime` の形。条件が無ければ `#/catalog`。 */
export function catalogListHash(q: CatalogQuery): string {
  const search = new URLSearchParams(serializeCatalogQuery(q)).toString();
  return search ? `#/catalog?${search}` : '#/catalog';
}

/** 絞り込みが何も掛かっていないか（ページ・並べ替えは数えない）。 */
export function hasActiveFilter(q: CatalogQuery): boolean {
  return !!(q.q || q.kind || q.has?.length || q.status || q.year !== undefined || q.season);
}

// 詳細から一覧へ戻るリンク用。一覧が最後に居た URL（絞り込みつき）を覚えておく。
let lastListHash = '#/catalog';

export function rememberListHash(hash: string): void {
  lastListHash = hash;
}

export function recalledListHash(): string {
  return lastListHash;
}

export interface Debouncer {
  run(fn: () => void): void;
  /** 待っている処理があれば今すぐ実行する。 */
  flush(): void;
  cancel(): void;
}

/** 入力が止まって ms 経ってから実行する。最後に渡した fn だけが走る。 */
export function createDebouncer(ms: number): Debouncer {
  let timer: ReturnType<typeof setTimeout> | undefined;
  let pending: (() => void) | undefined;

  const cancel = () => {
    if (timer !== undefined) clearTimeout(timer);
    timer = undefined;
    pending = undefined;
  };
  return {
    run(fn) {
      if (timer !== undefined) clearTimeout(timer);
      pending = fn;
      timer = setTimeout(() => {
        const job = pending;
        timer = undefined;
        pending = undefined;
        job?.();
      }, ms);
    },
    flush() {
      const job = pending;
      cancel();
      job?.();
    },
    cancel,
  };
}
