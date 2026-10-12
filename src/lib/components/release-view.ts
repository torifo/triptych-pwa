// 画面ごとの Release 絞り込み・並べ替え・入力検証。DOM にも store にも依存しない純関数に寄せ、vitest で固定する。

import type { DatePrecision, Edition, Release, SourceRef } from '../db/schema';
import {
  addDaysYmd,
  compareYmd,
  isPastRelease,
  isValidYmd,
  isWithinDays,
  precisionEnd,
} from '../dates';

export const TRACKED_STATUSES: readonly Edition['status'][] = ['plan', 'active', 'paused'];

export function isTrackedEdition(edition: Pick<Edition, 'status'> | undefined): boolean {
  return !!edition && TRACKED_STATUSES.includes(edition.status);
}

export function precisionOf(release: Pick<Release, 'precision'>): DatePrecision {
  return release.precision ?? 'day';
}

export function isPostponed(release: Pick<Release, 'dateHistory'>): boolean {
  return release.dateHistory.length > 0;
}

// ---------------------------------------------------------------------------
// 並べ替え
// ---------------------------------------------------------------------------

/** '25:30' → 分。時刻なし・不正値は同日の末尾に回す。 */
export function timeMinutes(time: string | undefined): number {
  const match = time ? /^(\d{1,2}):(\d{2})$/.exec(time) : null;
  return match ? Number(match[1]) * 60 + Number(match[2]) : Number.POSITIVE_INFINITY;
}

/** 日付 → 時刻 → ラベルの順。放送日（朝5時区切り）の中では 25:30 が 23:00 より後ろになる。 */
export function compareReleases(a: Release, b: Release): number {
  if (a.date !== b.date) return a.date < b.date ? -1 : 1;
  const ta = timeMinutes(a.time);
  const tb = timeMinutes(b.time);
  if (ta !== tb) return ta < tb ? -1 : 1;
  return a.label.localeCompare(b.label, 'ja');
}

// ---------------------------------------------------------------------------
// Home
// ---------------------------------------------------------------------------

/** 直近 n 日（day 精度のみ・追跡中のみ）。 */
export function upcomingReleases(
  releases: readonly Release[],
  editions: ReadonlyMap<string, Edition>,
  today: string,
  days: number,
): Release[] {
  return releases
    .filter((r) => isTrackedEdition(editions.get(r.editionId)) && isWithinDays(r, today, days))
    .sort(compareReleases);
}

/** 新着: 追跡中 Edition の seenAt 未設定の Release。 */
export function unseenReleases(
  releases: readonly Release[],
  editions: ReadonlyMap<string, Edition>,
): Release[] {
  return releases
    .filter((r) => !r.seenAt && isTrackedEdition(editions.get(r.editionId)))
    .sort(compareReleases);
}

// ---------------------------------------------------------------------------
// Calendar
// ---------------------------------------------------------------------------

export function trackedReleases(
  releases: readonly Release[],
  editions: ReadonlyMap<string, Edition>,
): Release[] {
  return releases.filter((r) => isTrackedEdition(editions.get(r.editionId))).sort(compareReleases);
}

export interface PrecisionSplit {
  days: Release[];
  months: Release[];
  years: Release[];
}

/** 精度ごとに分ける（入力の並びを保つ）。 */
export function splitByPrecision(releases: readonly Release[]): PrecisionSplit {
  const split: PrecisionSplit = { days: [], months: [], years: [] };
  for (const release of releases) {
    const precision = precisionOf(release);
    if (precision === 'year') split.years.push(release);
    else if (precision === 'month') split.months.push(release);
    else split.days.push(release);
  }
  return split;
}

/** day 精度の Release を日付キーで束ねる。 */
export function groupByDate(days: readonly Release[]): Map<string, Release[]> {
  const map = new Map<string, Release[]>();
  for (const release of days) {
    const list = map.get(release.date) ?? [];
    list.push(release);
    map.set(release.date, list);
  }
  return map;
}

/** month 精度のうち、指定の年月（month は 1〜12）に属するもの。 */
export function monthlyUndecided(
  months: readonly Release[],
  year: number,
  month: number,
): Release[] {
  const prefix = `${String(year).padStart(4, '0')}-${String(month).padStart(2, '0')}-`;
  return months.filter((r) => r.date.startsWith(prefix));
}

/** year 精度のうち、まだ過ぎていないもの（今年以降）。 */
export function yearlyUndecided(years: readonly Release[], today: string): Release[] {
  return years.filter((r) => !isPastRelease(r, today));
}

export interface MonthGroup {
  key: string; // 'YYYY-MM'
  year: number;
  month: number; // 1-12
  releases: Release[];
}

/** month 精度の Release を月ごとに束ねる（日付昇順）。 */
export function groupByMonth(months: readonly Release[]): MonthGroup[] {
  const groups = new Map<string, MonthGroup>();
  for (const release of months) {
    const key = release.date.slice(0, 7);
    let group = groups.get(key);
    if (!group) {
      group = { key, year: Number(key.slice(0, 4)), month: Number(key.slice(5, 7)), releases: [] };
      groups.set(key, group);
    }
    group.releases.push(release);
  }
  return [...groups.values()].sort((a, b) => (a.key < b.key ? -1 : a.key > b.key ? 1 : 0));
}

// ---------------------------------------------------------------------------
// WorkDetail（Edition ごとの Release 一覧）
// ---------------------------------------------------------------------------

export const PAST_DAYS_SHOWN = 30;

export const BROADCAST_KINDS: readonly Release['kind'][] = ['episode', 'stream-start'];

export interface EditionReleaseGroups {
  /** アニメ面のみ。episode / stream-start の今日以降。 */
  broadcast: Release[];
  /** 今日以降（アニメ面では broadcast を除く）。日付昇順。 */
  upcoming: Release[];
  /** 直近 30 日の過去分。新しい順。 */
  recent: Release[];
  /** 30 日より前で、表示していない件数。 */
  hiddenCount: number;
}

export function groupEditionReleases(
  releases: readonly Release[],
  mediaType: Edition['mediaType'],
  today: string,
  pastDays: number = PAST_DAYS_SHOWN,
): EditionReleaseGroups {
  const cutoff = addDaysYmd(today, -pastDays);
  const upcoming: Release[] = [];
  const recent: Release[] = [];
  let hiddenCount = 0;

  for (const release of releases) {
    // 日付が壊れた行は判定できないので、編集・削除できるよう予定側に残す。
    if (!isValidYmd(release.date) || !isPastRelease(release, today)) {
      upcoming.push(release);
    } else if (compareYmd(precisionEnd(release.date, precisionOf(release)), cutoff) >= 0) {
      recent.push(release);
    } else {
      hiddenCount += 1;
    }
  }

  upcoming.sort(compareReleases);
  recent.sort((a, b) => compareReleases(b, a));

  if (mediaType !== 'anime') return { broadcast: [], upcoming, recent, hiddenCount };
  return {
    broadcast: upcoming.filter((r) => BROADCAST_KINDS.includes(r.kind)),
    upcoming: upcoming.filter((r) => !BROADCAST_KINDS.includes(r.kind)),
    recent,
    hiddenCount,
  };
}

/** 手動追加フォームの種類の選択肢。編集中の Release の種類が範囲外でも消さない。 */
export function releaseKindOptions(
  allowed: readonly Release['kind'][],
  current?: Release['kind'],
): Release['kind'][] {
  return current && !allowed.includes(current) ? [...allowed, current] : [...allowed];
}

// ---------------------------------------------------------------------------
// 手動 Release フォームの入力検証
// ---------------------------------------------------------------------------

function pad2(n: number): string {
  return String(n).padStart(2, '0');
}

/** 手動 Release フォームが確定した値。 */
export interface ReleaseFormValue {
  kind: Release['kind'];
  label: string;
  date: string;
  precision: DatePrecision;
  time: string | undefined;
}

export interface DateFields {
  day: string; // YYYY-MM-DD（<input type="date"> の値）
  year: string;
  month: string; // '1'〜'12'
}

/** 'YYYY-MM-DD' を入力欄の値に分解する。不正なら null。 */
export function splitDateFields(date: string): DateFields | null {
  if (!isValidYmd(date)) return null;
  return { day: date, year: String(Number(date.slice(0, 4))), month: String(Number(date.slice(5, 7))) };
}

/** 精度に応じた入力欄から 'YYYY-MM-DD' を作る（month/year は月初・年初）。不正なら null。 */
export function buildReleaseDate(precision: DatePrecision, fields: DateFields): string | null {
  if (precision === 'day') return isValidYmd(fields.day) ? fields.day : null;
  const year = Number(fields.year);
  if (!/^\d{4}$/.test(fields.year.trim()) || year < 1900 || year > 2999) return null;
  if (precision === 'year') return `${year}-01-01`;
  const month = Number(fields.month);
  if (!Number.isInteger(month) || month < 1 || month > 12) return null;
  return `${year}-${pad2(month)}-01`;
}

export type TimeParse = { ok: true; value: string | undefined } | { ok: false };

/** 放送時刻の入力（'25:30' 形式・時は 0〜29）。空欄は「時刻なし」。全角でも受ける。 */
export function parseTimeInput(raw: string): TimeParse {
  const text = raw.normalize('NFKC').trim();
  if (text === '') return { ok: true, value: undefined };
  const match = /^(\d{1,2}):([0-5]\d)$/.exec(text);
  if (!match) return { ok: false };
  const hour = Number(match[1]);
  if (hour > 29) return { ok: false };
  return { ok: true, value: `${pad2(hour)}:${match[2]}` };
}

// ---------------------------------------------------------------------------
// ソース（sourceRef）
// ---------------------------------------------------------------------------

export function sourceRefKey(source: string, externalId: string): string {
  return `${source}:${externalId}`;
}

/** どれかの Edition に登録済みの (source, externalId)。 */
export function registeredSourceKeys(editions: readonly Pick<Edition, 'sourceRefs'>[]): Set<string> {
  const keys = new Set<string>();
  for (const edition of editions) {
    for (const ref of edition.sourceRefs) keys.add(sourceRefKey(ref.source, ref.externalId));
  }
  return keys;
}

export interface ResolvableResult {
  source: SourceRef['source'];
  externalId: string;
  url: string;
  direct?: boolean;
}

/** 貼り付けの解決結果。先頭が direct の時だけ sourceRef にする。 */
export function directSourceRef(results: readonly ResolvableResult[]): SourceRef | null {
  const first = results[0];
  if (!first?.direct) return null;
  return { source: first.source, externalId: first.externalId, url: first.url };
}

/** 外部リンクにしてよい URL（http/https のみ）。 */
export function isSafeHttpUrl(url: string): boolean {
  try {
    const parsed = new URL(url);
    return parsed.protocol === 'http:' || parsed.protocol === 'https:';
  } catch {
    return false;
  }
}

/** 検索結果の重複（同じ source・externalId・mediaType）を先頭を残して除く。 */
export function dedupeResults<T extends { source: string; externalId: string; mediaType: string }>(
  results: readonly T[],
): T[] {
  const seen = new Set<string>();
  const out: T[] = [];
  for (const result of results) {
    const key = `${result.source}:${result.externalId}:${result.mediaType}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(result);
  }
  return out;
}
