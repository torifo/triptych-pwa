// カタログの行 -> Library（Work / Edition）への変換と、登録済みの判定（純関数）。

import { isSafeHttpUrl } from '../components/release-view';
import type { Edition, MediaType, Source, SourceRef, Work } from '../db/schema';
import type { CreateEditionInput } from '../db/store';
import { normalizeTitle } from '../normalize';
import type { CatalogEntry, CatalogKind, CatalogWorkDetail } from './types';
import { KIND_ORDER } from './view';

const NDL_SEARCH_URL = 'https://ndlsearch.ndl.go.jp/search';

const KNOWN_SOURCES: readonly Source[] = ['anilist', 'narou', 'openbd', 'ndl', 'syoboi', 'kakuyomu', 'manual'];

/** createEdition の入力から workId を除いたもの（Work が決まってから足す）。 */
export interface EditionInput extends Omit<CreateEditionInput, 'workId' | 'sourceRefs'> {
  sourceRefs: SourceRef[];
  /**
   * 他の面から借りて入れた表紙の印（本だけ）。createEdition は syncHints を受けないので、
   * 作成のあとで syncHints.coverFrom に書く（書かないと、手で設定した表紙と区別できない）。
   */
  coverFrom?: 'catalog';
}

function text(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

function asSource(value: string): Source | null {
  return KNOWN_SOURCES.find((s) => s === value) ?? null;
}

/** 参照の外部 ID を比べる形にする（なろうの ncode は検索の登録に合わせて小文字）。 */
function normalizeExternalId(source: string, externalId: string): string {
  const id = externalId.trim();
  return source === 'narou' ? id.toLowerCase() : id;
}

/** `source:externalId`。登録済みの照合に使う。 */
export function refKey(source: string, externalId: string): string {
  return `${source}:${normalizeExternalId(source, externalId)}`;
}

// ---------------------------------------------------------------------------
// 本（NDL）
// ---------------------------------------------------------------------------

/** NDL の系統参照の種別。extra.kindHint が manga なら manga、それ以外は novel。 */
export function ndlKind(entry: Pick<CatalogEntry, 'extra'>): 'novel' | 'manga' {
  return entry.extra?.kindHint === 'manga' ? 'manga' : 'novel';
}

/** 本のシリーズ名。extra.series が無ければ題名。 */
export function ndlSeriesName(entry: Pick<CatalogEntry, 'extra' | 'title'>): string {
  return text(entry.extra?.series) || entry.title;
}

/** NDL の系統参照の externalId `"<novel|manga>:<line>:<シリーズ名>"`（既存の同期が line を読み戻す）。 */
export function ndlSeriesExternalId(entry: Pick<CatalogEntry, 'extra' | 'title'>): string {
  return `${ndlKind(entry)}:${text(entry.extra?.line)}:${ndlSeriesName(entry)}`;
}

function ndlSearchUrl(entry: Pick<CatalogEntry, 'extra' | 'title'>): string {
  const keyword = `${ndlSeriesName(entry)} ${text(entry.extra?.line)}`.trim();
  return `${NDL_SEARCH_URL}?${new URLSearchParams({ cs: 'bib', keyword })}`;
}

// ---------------------------------------------------------------------------
// 変換
// ---------------------------------------------------------------------------

/** anime→anime / manga→manga / novel→light-novel / web-novel→web-novel / book→kindHint が manga なら manga、他は light-novel。 */
export function entryMediaType(entry: Pick<CatalogEntry, 'kind' | 'extra'>): MediaType {
  switch (entry.kind) {
    case 'anime':
      return 'anime';
    case 'manga':
      return 'manga';
    case 'novel':
      return 'light-novel';
    case 'web-novel':
      return 'web-novel';
    case 'book':
      return entry.extra?.kindHint === 'manga' ? 'manga' : 'light-novel';
  }
}

function fallbackUrl(entry: CatalogEntry, source: Source, externalId: string): string {
  switch (source) {
    case 'anilist':
      return `https://anilist.co/${entry.kind === 'anime' ? 'anime' : 'manga'}/${externalId}`;
    case 'syoboi':
      return `https://cal.syoboi.jp/tid/${externalId}`;
    case 'narou':
      return `https://ncode.syosetu.com/${externalId}/`;
    case 'kakuyomu':
      return `https://kakuyomu.jp/works/${externalId}`;
    default:
      return '';
  }
}

/**
 * 行の sourceRefs。anilist / syoboi(TID) / narou / kakuyomu は行そのもの、
 * 本は NDL の系統参照（巻ではなくシリーズ単位）。
 */
export function entrySourceRefs(entry: CatalogEntry): SourceRef[] {
  if (entry.kind === 'book') {
    return [{ source: 'ndl', externalId: ndlSeriesExternalId(entry), url: ndlSearchUrl(entry) }];
  }
  const source = asSource(entry.source);
  if (!source) return [];
  const externalId = normalizeExternalId(source, entry.externalId);
  const url = entry.url && isSafeHttpUrl(entry.url) ? entry.url : fallbackUrl(entry, source, externalId);
  return [{ source, externalId, url }];
}

/**
 * CatalogEntry -> createEdition の入力。本はシリーズ名を Edition の題名にする。
 * 表紙の無い本は、同じカタログ作品の行（siblings）から AniList の表紙を借りる。
 */
export function toEditionInput(entry: CatalogEntry, siblings: readonly CatalogEntry[] = []): EditionInput {
  const input: EditionInput = {
    mediaType: entryMediaType(entry),
    title: entry.kind === 'book' ? ndlSeriesName(entry) : entry.title,
    sourceRefs: entrySourceRefs(entry),
    linkSource: 'manual',
  };
  if (entry.cover && isSafeHttpUrl(entry.cover)) {
    input.coverUrl = entry.cover;
  } else if (entry.kind === 'book') {
    const borrowed = borrowedCover(input.mediaType, siblings);
    if (borrowed) {
      input.coverUrl = borrowed;
      input.coverFrom = 'catalog';
    }
  }
  return input;
}

/** 本が表紙を借りる面の順: 本の種別に合う面（漫画→漫画、文庫→小説）、もう一方、アニメ。 */
function borrowOrder(mediaType: MediaType): CatalogKind[] {
  return mediaType === 'manga' ? ['manga', 'novel', 'anime'] : ['novel', 'manga', 'anime'];
}

/** 同じカタログ作品の AniList の行から借りる表紙（http/https のみ）。面ごとに人気が最大の行を使う。 */
function borrowedCover(mediaType: MediaType, siblings: readonly CatalogEntry[]): string | undefined {
  for (const kind of borrowOrder(mediaType)) {
    const withCover = siblings.filter(
      (e) => e.kind === kind && e.source === 'anilist' && isSafeHttpUrl(text(e.cover)),
    );
    const best = mostPopular(withCover);
    if (best) return text(best.cover);
  }
  return undefined;
}

// ---------------------------------------------------------------------------
// 登録済みの判定
// ---------------------------------------------------------------------------

/** 既存 Edition の sourceRefs から `source:externalId` の集合。 */
export function registeredKeys(editions: readonly Pick<Edition, 'sourceRefs'>[]): Set<string> {
  const keys = new Set<string>();
  for (const edition of editions) {
    for (const ref of edition.sourceRefs) keys.add(refKey(ref.source, ref.externalId));
  }
  return keys;
}

/** その行がすでに Library にあるか。本は NDL の系統参照の形式で判定する。 */
export function isRegistered(entry: CatalogEntry, keys: ReadonlySet<string>): boolean {
  return entrySourceRefs(entry).some((ref) => keys.has(refKey(ref.source, ref.externalId)));
}

/** 同じ参照になる行（同じシリーズの別の巻など）を先頭だけ残す。 */
export function uniqueByRef(entries: readonly CatalogEntry[]): CatalogEntry[] {
  const seen = new Set<string>();
  const out: CatalogEntry[] = [];
  for (const entry of entries) {
    const refs = entrySourceRefs(entry);
    const keys = refs.map((r) => refKey(r.source, r.externalId));
    if (keys.length > 0 && keys.every((k) => seen.has(k))) continue;
    for (const k of keys) seen.add(k);
    out.push(entry);
  }
  return out;
}

/**
 * 追加先の既存 Work。この作品の行がすでに入っている Work（最も多いもの）を優先し、
 * 無ければ正規化題名が同じ Work（Search.svelte と同じ作法）。
 */
export function findExistingWork(
  title: string,
  entries: readonly CatalogEntry[],
  works: readonly Work[],
  editions: readonly Pick<Edition, 'workId' | 'sourceRefs'>[],
): Work | undefined {
  const keys = new Set<string>();
  for (const entry of entries) {
    for (const ref of entrySourceRefs(entry)) keys.add(refKey(ref.source, ref.externalId));
  }
  const counts = new Map<string, number>();
  for (const edition of editions) {
    if (edition.sourceRefs.some((ref) => keys.has(refKey(ref.source, ref.externalId)))) {
      counts.set(edition.workId, (counts.get(edition.workId) ?? 0) + 1);
    }
  }
  let bestId: string | undefined;
  let best = 0;
  for (const [workId, n] of counts) {
    if (n > best && works.some((w) => w.id === workId)) {
      best = n;
      bestId = workId;
    }
  }
  if (bestId) return works.find((w) => w.id === bestId);

  const normalized = normalizeTitle(title);
  return normalized ? works.find((w) => w.titleNormalized === normalized) : undefined;
}

// ---------------------------------------------------------------------------
// 既定の選択
// ---------------------------------------------------------------------------

function popularityOf(entry: CatalogEntry): number {
  return entry.popularity ?? -1;
}

/** 人気が最大の行（同点は先に出てきた方）。 */
function mostPopular(entries: readonly CatalogEntry[]): CatalogEntry | undefined {
  let best: CatalogEntry | undefined;
  for (const entry of entries) {
    if (!best || popularityOf(entry) > popularityOf(best)) best = entry;
  }
  return best;
}

/** 最新の巻（発売日が新しいもの。同日は巻数、それも同じなら後に出てきた方）。 */
function latestBook(entries: readonly CatalogEntry[]): CatalogEntry | undefined {
  let best: CatalogEntry | undefined;
  for (const entry of entries) {
    if (!best) {
      best = entry;
      continue;
    }
    const a = entry.startDate ?? '';
    const b = best.startDate ?? '';
    if (a > b || (a === b && (entry.volumes ?? 0) >= (best.volumes ?? 0))) best = entry;
  }
  return best;
}

/** 放送中の期（TV を優先し、その中で人気最大）。放送中が無ければ undefined。 */
function airingSeason(entries: readonly CatalogEntry[]): CatalogEntry | undefined {
  const airing = entries.filter((e) => e.status === 'RELEASING');
  if (airing.length === 0) return undefined;
  const tv = airing.filter((e) => e.format === 'TV');
  return mostPopular(tv.length > 0 ? tv : airing);
}

/**
 * 各面から代表1件。
 * anime・漫画・原作小説は AniList の人気最大（AniList の行が無い面は全体から）。
 * ただし anime は放送中の期があればそれ（人気は第1期が高く、追いかけたいのは今の期なので）。
 * Web 小説は人気最大、本は最新の巻。行が無い面は含めない。
 */
export function defaultSelection(
  detail: Pick<CatalogWorkDetail, 'entries'>,
): Partial<Record<CatalogKind, CatalogEntry>> {
  const out: Partial<Record<CatalogKind, CatalogEntry>> = {};
  for (const kind of KIND_ORDER) {
    const members = detail.entries.filter((e) => e.kind === kind);
    if (members.length === 0) continue;
    let pick: CatalogEntry | undefined;
    if (kind === 'book') {
      pick = latestBook(members);
    } else if (kind === 'web-novel') {
      pick = mostPopular(members);
    } else {
      const anilist = members.filter((e) => e.source === 'anilist');
      const pool = anilist.length > 0 ? anilist : members;
      pick = (kind === 'anime' ? airingSeason(pool) : undefined) ?? mostPopular(pool);
    }
    if (pick) out[kind] = pick;
  }
  return out;
}
