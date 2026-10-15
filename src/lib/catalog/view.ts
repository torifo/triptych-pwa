// カタログ画面の表示用ラベルと整形（純関数）。

import { animeFormatLabel } from '../components/labels';
import { formatReleaseDate, todayJst } from '../dates';
import type {
  CatalogEntry,
  CatalogKind,
  CatalogPrecision,
  CatalogQuery,
  CatalogSort,
  CatalogStats,
  CatalogWorkSummary,
} from './types';

/** 面の並び順（絞り込みチップ・詳細のセクション）。 */
export const KIND_ORDER: readonly CatalogKind[] = ['novel', 'manga', 'anime', 'web-novel', 'book'];

export const KIND_LABELS: Record<CatalogKind, string> = {
  novel: '原作小説',
  manga: '漫画',
  anime: 'アニメ',
  'web-novel': 'Web小説',
  book: '紙の本',
};

/** 活字バッジ用の短いラベル（MediaBadge の WEB / 漫画 / ANIME に揃える）。 */
export const KIND_BADGES: Record<CatalogKind, string> = {
  novel: '小説',
  manga: '漫画',
  anime: 'ANIME',
  'web-novel': 'WEB',
  book: '書籍',
};

/** 章見出しの右肩（.shou .no）。 */
export const KIND_CODES: Record<CatalogKind, string> = {
  novel: 'NOVEL',
  manga: 'COMIC',
  anime: 'ANIME',
  'web-novel': 'WEB NOVEL',
  book: 'BOOKS',
};

type AnimeStatus = NonNullable<CatalogQuery['status']>;
type Season = NonNullable<CatalogQuery['season']>;

export const STATUS_FILTERS: readonly { value: AnimeStatus; label: string }[] = [
  { value: 'RELEASING', label: '放送中' },
  { value: 'NOT_YET_RELEASED', label: '放送予定' },
  { value: 'FINISHED', label: '放送済' },
];

export const SEASON_FILTERS: readonly { value: Season; label: string }[] = [
  { value: 'WINTER', label: '冬' },
  { value: 'SPRING', label: '春' },
  { value: 'SUMMER', label: '夏' },
  { value: 'FALL', label: '秋' },
];

export const SORT_OPTIONS: readonly { value: CatalogSort; label: string }[] = [
  { value: 'popularity', label: '人気' },
  { value: 'recent', label: '新しい順' },
  { value: 'upcoming', label: '近日' },
  { value: 'title', label: '題名' },
];

const STATUS_LABELS: Record<string, string> = {
  RELEASING: '放送中',
  NOT_YET_RELEASED: '放送予定',
  FINISHED: '放送済',
  CANCELLED: '中止',
  HIATUS: '休止中',
};

/** 一覧のアニメの状態。 */
export function animeStatusLabel(status: string | null | undefined): string | null {
  if (!status) return null;
  return STATUS_LABELS[status] ?? status;
}

/** 各ソース行の状態（AniList の列挙値はそのまま、なろう・NDL の「連載中 / 完結」は素通し）。 */
export function entryStatusLabel(entry: Pick<CatalogEntry, 'kind' | 'status'>): string | null {
  if (!entry.status) return null;
  if (entry.kind === 'anime') return animeStatusLabel(entry.status);
  const upper: Record<string, string> = {
    RELEASING: '連載中',
    NOT_YET_RELEASED: '発売予定',
    FINISHED: '完結',
    CANCELLED: '中止',
    HIATUS: '休載中',
  };
  return upper[entry.status] ?? entry.status;
}

// ---------------------------------------------------------------------------
// 日付・期間
// ---------------------------------------------------------------------------

/** 年の範囲（2019–2024 / 2019）。 */
export function yearRange(first?: number | null, last?: number | null): string {
  const a = first ?? undefined;
  const b = last ?? undefined;
  if (a === undefined && b === undefined) return '';
  if (a !== undefined && b !== undefined && a !== b) return `${a}–${b}`;
  return String(a ?? b);
}

export interface NextView {
  /** 精度どおりの日付（`10/16(金)` / `2026年8月` / `2026年`）。day 精度で今年以外は年を前に付ける。 */
  date: string;
  label: string | null;
}

/** 近日の予定。nextDate が無ければ null。 */
export function formatNext(
  work: Pick<CatalogWorkSummary, 'nextDate' | 'nextPrecision' | 'nextLabel'>,
  today: string = todayJst(),
): NextView | null {
  if (!work.nextDate) return null;
  const precision: CatalogPrecision = work.nextPrecision ?? 'day';
  const base = formatReleaseDate({ date: work.nextDate, precision });
  const year = work.nextDate.slice(0, 4);
  const date =
    precision === 'day' && /^\d{4}$/.test(year) && year !== today.slice(0, 4) ? `${year}/${base}` : base;
  return { date, label: work.nextLabel?.trim() || null };
}

/** 行内の日付（精度どおり: 2019-04-06 / 2019-04 / 2019）。 */
export function formatEntryDate(date: string, precision: CatalogPrecision | null | undefined): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(date);
  if (!m) return date;
  if (precision === 'year') return m[1];
  if (precision === 'month') return `${m[1]}-${m[2]}`;
  return date;
}

/** 期間。終了日が無く連載中なら「2019-04〜」、本（1冊）は発売日だけ。 */
export function formatEntryPeriod(
  entry: Pick<CatalogEntry, 'kind' | 'status' | 'startDate' | 'startPrecision' | 'endDate' | 'endPrecision'>,
): string | null {
  if (!entry.startDate) return null;
  const start = formatEntryDate(entry.startDate, entry.startPrecision);
  if (entry.kind === 'book') return start;
  const end = entry.endDate ? formatEntryDate(entry.endDate, entry.endPrecision ?? entry.startPrecision) : null;
  if (end && end !== start) return `${start}〜${end}`;
  if (!end && (entry.status === 'RELEASING' || entry.status === '連載中')) return `${start}〜`;
  return start;
}

const JST_OFFSET_MS = 9 * 60 * 60 * 1000;

/** ISO 8601（UTC）を JST の `2026-10-14 21:30` にする。読めなければ原文。 */
export function formatJstDateTime(iso: string): string {
  let text = iso.trim();
  text = text.replace(/(\.\d{3})\d+/, '$1'); // マイクロ秒は Date.parse が嫌う環境がある
  if (!/(Z|[+-]\d{2}:?\d{2})$/i.test(text)) text = `${text}Z`;
  const t = Date.parse(text);
  if (Number.isNaN(t)) return iso;
  const jst = new Date(t + JST_OFFSET_MS).toISOString();
  return `${jst.slice(0, 10)} ${jst.slice(11, 16)}`;
}

/** ソース別の最終収集時刻のうち一番新しいもの。 */
export function latestHarvestAt(stats: Pick<CatalogStats, 'sources'>): string | null {
  let best: { iso: string; t: number } | null = null;
  for (const s of stats.sources) {
    if (!s.lastHarvestAt) continue;
    const t = Date.parse(s.lastHarvestAt.replace(/(\.\d{3})\d+/, '$1'));
    if (Number.isNaN(t)) continue;
    if (!best || t > best.t) best = { iso: s.lastHarvestAt, t };
  }
  return best?.iso ?? null;
}

// ---------------------------------------------------------------------------
// 行の中身
// ---------------------------------------------------------------------------

const MANGA_FORMATS: Record<string, string> = {
  MANGA: '漫画',
  NOVEL: '小説',
  ONE_SHOT: '読切',
};

/** 形式（TV / 映画 / 漫画 / 読切 …）。 */
export function formatLabel(entry: Pick<CatalogEntry, 'kind' | 'format'>): string | null {
  if (!entry.format) return null;
  if (entry.kind === 'anime') return animeFormatLabel(entry.format);
  return MANGA_FORMATS[entry.format] ?? entry.format;
}

/** 話数・巻数（`24話` `全11巻`）。 */
export function entryCounts(
  entry: Pick<CatalogEntry, 'kind' | 'episodes' | 'chapters' | 'volumes'>,
): string[] {
  const out: string[] = [];
  const positive = (n: number | null | undefined): n is number => typeof n === 'number' && n > 0;
  if (positive(entry.episodes)) out.push(entry.kind === 'web-novel' ? `${entry.episodes}部分` : `${entry.episodes}話`);
  if (positive(entry.chapters)) out.push(`${entry.chapters}話`);
  if (positive(entry.volumes)) out.push(`${entry.volumes}巻`);
  return out;
}

/** 名前の並び（3つまで、残りは「ほかN」）。 */
export function namesLine(names: readonly string[], limit = 3): string {
  const shown = names.slice(0, limit).join(' / ');
  const rest = names.length - limit;
  return rest > 0 ? `${shown} ほか${rest}` : shown;
}

export const LINK_METHOD_LABELS: Record<string, string> = {
  'title-exact': '題名一致',
  'title-year': '題名・年一致',
  synonym: '別名一致',
  'title-prefix': '題名前方一致',
  series: 'シリーズ名一致',
};

/** 非 AniList の行の結び付き（`題名一致 85%`）。AniList の行や情報の無い行は null。 */
export function linkBadge(
  entry: Pick<CatalogEntry, 'source' | 'linkMethod' | 'linkConfidence'>,
): string | null {
  if (entry.source === 'anilist') return null;
  const method = entry.linkMethod ?? null;
  const confidence = entry.linkConfidence ?? null;
  if (method === null && confidence === null) return null;
  const label = method === null ? '照合' : (LINK_METHOD_LABELS[method] ?? method);
  return confidence === null ? label : `${label} ${Math.round(confidence * 100)}%`;
}

// ---------------------------------------------------------------------------
// 放送中・絞り込みの要約
// ---------------------------------------------------------------------------

/** 一覧のアニメの状態が「放送中」か（表紙に ON AIR のランプを付ける）。 */
export function isAnimeAiring(status: string | null | undefined): boolean {
  return status === 'RELEASING';
}

/** 詳細: アニメの行のどれかが放送中か。 */
export function isAiringNow(entries: readonly Pick<CatalogEntry, 'kind' | 'status'>[]): boolean {
  return entries.some((e) => e.kind === 'anime' && isAnimeAiring(e.status));
}

/** 絞り込みの要約（種別・アニメ化あり・状態・年・季の順）。検索語と並び順は数えない。 */
export function activeFilterLabels(q: CatalogQuery): string[] {
  const out: string[] = [];
  if (q.kind) out.push(KIND_LABELS[q.kind]);
  for (const kind of q.has ?? []) out.push(kind === 'anime' ? 'アニメ化あり' : `${KIND_LABELS[kind]}あり`);
  if (q.status) out.push(STATUS_FILTERS.find((s) => s.value === q.status)?.label ?? q.status);
  if (q.year !== undefined) out.push(`${q.year}年`);
  if (q.season) out.push(SEASON_FILTERS.find((s) => s.value === q.season)?.label ?? q.season);
  return out;
}
