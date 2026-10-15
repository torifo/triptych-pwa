// JST の暦日を扱う純関数群（toISOString は UTC になるため日付生成に使わない）

import type { DatePrecision, Release } from './db/schema';

/** 日付表示・判定に必要な Release の部分集合。 */
export type ReleaseDateLike = Pick<Release, 'date'> & Partial<Pick<Release, 'precision' | 'time'>>;

const JST_OFFSET_MS = 9 * 60 * 60 * 1000;
const DAY_MS = 24 * 60 * 60 * 1000;
const YMD_RE = /^(\d{4})-(\d{2})-(\d{2})$/;
const WEEKDAYS_JA = ['日', '月', '火', '水', '木', '金', '土'] as const;

interface Ymd {
  y: number;
  m: number;
  d: number;
}

function pad2(n: number): string {
  return String(n).padStart(2, '0');
}

function formatYmd(y: number, m: number, d: number): string {
  return `${String(y).padStart(4, '0')}-${pad2(m)}-${pad2(d)}`;
}

/** 'YYYY-MM-DD' を分解する。形式が不正、または実在しない日付（2026-02-30 など）なら null。 */
function parseYmd(ymd: string): Ymd | null {
  const match = YMD_RE.exec(ymd);
  if (!match) return null;
  const y = Number(match[1]);
  const m = Number(match[2]);
  const d = Number(match[3]);
  const t = new Date(Date.UTC(y, m - 1, d));
  if (t.getUTCFullYear() !== y || t.getUTCMonth() !== m - 1 || t.getUTCDate() !== d) return null;
  return { y, m, d };
}

function mustParseYmd(ymd: string): Ymd {
  const parsed = parseYmd(ymd);
  if (!parsed) throw new RangeError(`Invalid date (expected YYYY-MM-DD): ${ymd}`);
  return parsed;
}

function dayNumber({ y, m, d }: Ymd): number {
  return Math.round(Date.UTC(y, m - 1, d) / DAY_MS);
}

/** 'YYYY-MM-DD' として実在する日付か。 */
export function isValidYmd(ymd: string): boolean {
  return parseYmd(ymd) !== null;
}

/** JST の暦日（YYYY-MM-DD）。now を省略すると現在時刻。実行環境のタイムゾーンに依存しない。 */
export function todayJst(now: Date = new Date()): string {
  const jst = new Date(now.getTime() + JST_OFFSET_MS);
  return formatYmd(jst.getUTCFullYear(), jst.getUTCMonth() + 1, jst.getUTCDate());
}

/** ymd の n 日後（負なら前）。 */
export function addDaysYmd(ymd: string, n: number): string {
  const { y, m, d } = mustParseYmd(ymd);
  const t = new Date(Date.UTC(y, m - 1, d + n));
  return formatYmd(t.getUTCFullYear(), t.getUTCMonth() + 1, t.getUTCDate());
}

/** a が b より前なら -1、同じなら 0、後なら 1。 */
export function compareYmd(a: string, b: string): -1 | 0 | 1 {
  const diff = diffDaysYmd(a, b);
  return diff < 0 ? -1 : diff > 0 ? 1 : 0;
}

/** a - b を日数で返す（a が b の 3 日後なら 3）。 */
export function diffDaysYmd(a: string, b: string): number {
  return dayNumber(mustParseYmd(a)) - dayNumber(mustParseYmd(b));
}

/** 精度の期間の初日。day はそのまま、month は月初、year は 1 月 1 日。 */
export function precisionStart(ymd: string, precision: DatePrecision): string {
  const { y, m } = mustParseYmd(ymd);
  if (precision === 'year') return formatYmd(y, 1, 1);
  if (precision === 'month') return formatYmd(y, m, 1);
  return ymd;
}

/** 精度の期間の最終日。day はそのまま、month は月末、year は 12 月 31 日。 */
export function precisionEnd(ymd: string, precision: DatePrecision): string {
  const { y, m } = mustParseYmd(ymd);
  if (precision === 'year') return formatYmd(y, 12, 31);
  if (precision === 'month') return formatYmd(y, m, new Date(Date.UTC(y, m, 0)).getUTCDate());
  return ymd;
}

/** 精度の終わりが today より前なら過去。月精度の 2026-08 は 8/31 を過ぎるまで過去にならない。 */
export function isPastRelease(release: ReleaseDateLike, today: string): boolean {
  if (!isValidYmd(release.date)) return false;
  return compareYmd(precisionEnd(release.date, release.precision ?? 'day'), today) < 0;
}

/** 今日〜今日+n 日に収まるか。day 精度のみ（月・年精度は特定の日ではないので false）。 */
export function isWithinDays(release: ReleaseDateLike, today: string, n: number): boolean {
  if ((release.precision ?? 'day') !== 'day') return false;
  if (!isValidYmd(release.date)) return false;
  return compareYmd(release.date, today) >= 0 && compareYmd(release.date, addDaysYmd(today, n)) <= 0;
}

// 深夜の時刻の書き方（設定 timeNotation）。'late-night' は放送日のまま 25:30、'clock' は翌日の 1:30。
// 表示だけの切り替えなので、保存する値（放送日・25:30）は変えない。App が起動時と設定の変更時に合わせる。
let timeNotation: 'late-night' | 'clock' = 'late-night';

export function setTimeNotation(mode: 'late-night' | 'clock'): void {
  timeNotation = mode;
}

const LATE_TIME_RE = /^(\d{1,2}):(\d{2})$/;

/** 表示に使う日付と時刻。時計表記では 24 時以降を翌日に送る（nextDay=true）。 */
export function displayReleaseTime(release: Pick<ReleaseDateLike, 'date' | 'time'>): {
  date: string;
  time: string | undefined;
  nextDay: boolean;
} {
  const match = release.time ? LATE_TIME_RE.exec(release.time) : null;
  if (timeNotation !== 'clock' || !match || Number(match[1]) < 24 || !isValidYmd(release.date)) {
    return { date: release.date, time: release.time ?? undefined, nextDay: false };
  }
  return {
    date: addDaysYmd(release.date, 1),
    time: `${Number(match[1]) - 24}:${match[2]}`,
    nextDay: true,
  };
}

/** 表示用の日付（day `10/16(金) 25:30` / month `2026年8月` / year `2026年`）。時計表記では `10/17(土) 1:30`。 */
export function formatReleaseDate(release: ReleaseDateLike): string {
  const precision = release.precision ?? 'day';
  const shown = precision === 'day' ? displayReleaseTime(release) : { date: release.date, time: undefined };
  const parsed = parseYmd(shown.date);
  if (!parsed) return release.date;
  const { y, m, d } = parsed;
  if (precision === 'year') return `${y}年`;
  if (precision === 'month') return `${y}年${m}月`;
  const weekday = WEEKDAYS_JA[new Date(Date.UTC(y, m - 1, d)).getUTCDay()];
  const base = `${m}/${d}(${weekday})`;
  return shown.time ? `${base} ${shown.time}` : base;
}
