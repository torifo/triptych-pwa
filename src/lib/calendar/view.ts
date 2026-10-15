// カレンダー画面の表示の決まり（週・月・リストの切り替え、媒体の絞り込み、週の日付）。純関数で vitest 固定。

import type { CatalogEventKind } from '../catalog/types';
import { addDaysYmd, precisionEnd } from '../dates';
import type { Edition, MediaType, Release } from '../db/schema';

// ---------------------------------------------------------------------------
// 表示の切り替え（端末に保存）
// ---------------------------------------------------------------------------

export type CalendarView = 'week' | 'month' | 'list';

export const CALENDAR_VIEW_KEY = 'triptych:calendar-view';
export const CALENDAR_OVERLAY_KEY = 'triptych:calendar-catalog';

/** localStorage そのものに触れるだけで例外になる環境があるので、取り出しも包む。 */
function browserStorage(): Storage | null {
  try {
    return typeof localStorage === 'undefined' ? null : localStorage;
  } catch {
    return null;
  }
}

/** 端末に保存した表示。保存が無い・読めない・知らない値なら週。 */
export function loadCalendarView(storage: Pick<Storage, 'getItem'> | null = browserStorage()): CalendarView {
  try {
    const value = storage?.getItem(CALENDAR_VIEW_KEY);
    return value === 'month' || value === 'list' ? value : 'week';
  } catch {
    return 'week';
  }
}

export function saveCalendarView(view: CalendarView, storage: Pick<Storage, 'setItem'> | null = browserStorage()): void {
  try {
    storage?.setItem(CALENDAR_VIEW_KEY, view);
  } catch {
    // 容量超過・プライベートモードなど。表示は切り替わるので黙って諦める
  }
}

/** カタログの重ね表示。既定は ON（保存が無い・読めない時も ON）。OFF にした時だけ OFF。 */
export function loadCatalogOverlay(storage: Pick<Storage, 'getItem'> | null = browserStorage()): boolean {
  try {
    return storage?.getItem(CALENDAR_OVERLAY_KEY) !== 'off';
  } catch {
    return true;
  }
}

export function saveCatalogOverlay(on: boolean, storage: Pick<Storage, 'setItem'> | null = browserStorage()): void {
  try {
    storage?.setItem(CALENDAR_OVERLAY_KEY, on ? 'on' : 'off');
  } catch {
    // 同上
  }
}

// ---------------------------------------------------------------------------
// 媒体の絞り込み
// ---------------------------------------------------------------------------

export type MediaFilter = 'all' | 'anime' | 'book' | 'web';

export const MEDIA_FILTERS: readonly { value: MediaFilter; label: string }[] = [
  { value: 'all', label: 'すべて' },
  { value: 'anime', label: 'アニメ' },
  { value: 'book', label: '本' },
  { value: 'web', label: 'Web' },
];

const MEDIA_OF_FILTER: Record<Exclude<MediaFilter, 'all'>, readonly MediaType[]> = {
  anime: ['anime'],
  book: ['light-novel', 'manga'],
  web: ['web-novel'],
};

/** Library の Edition の媒体が絞り込みに当たるか（本 = 文庫と漫画）。 */
export function matchesMediaFilter(mediaType: MediaType | undefined, filter: MediaFilter): boolean {
  if (filter === 'all') return true;
  return !!mediaType && MEDIA_OF_FILTER[filter].includes(mediaType);
}

/** Library の Release のうち、Edition の媒体が絞り込みに当たるもの（並びは保つ）。 */
export function filterByMedia<T extends Pick<Release, 'editionId'>>(
  releases: readonly T[],
  editions: ReadonlyMap<string, Pick<Edition, 'mediaType'>>,
  filter: MediaFilter,
): T[] {
  if (filter === 'all') return [...releases];
  return releases.filter((r) => matchesMediaFilter(editions.get(r.editionId)?.mediaType, filter));
}

/** カタログに問い合わせる種類。Web 小説はカタログの出来事に無いので空（重ね表示を出さない）。 */
export function catalogKindsFor(filter: MediaFilter): CatalogEventKind[] {
  if (filter === 'all') return ['anime', 'book'];
  if (filter === 'anime') return ['anime'];
  if (filter === 'book') return ['book'];
  return [];
}

// ---------------------------------------------------------------------------
// 週・月の期間
// ---------------------------------------------------------------------------

export const WEEK_LENGTH = 7;

/** 今日から offset 週ずらした週の初日（週は今日から始まる7日）。 */
export function weekStart(today: string, offset: number): string {
  return addDaysYmd(today, offset * WEEK_LENGTH);
}

export function weekDates(start: string): string[] {
  return Array.from({ length: WEEK_LENGTH }, (_, i) => addDaysYmd(start, i));
}

/** 月（month は 1〜12）の初日と末日。 */
export function monthRange(year: number, month: number): { from: string; to: string } {
  const from = `${String(year).padStart(4, '0')}-${String(month).padStart(2, '0')}-01`;
  return { from, to: precisionEnd(from, 'month') };
}

const WEEKDAYS_EN = ['SUN', 'MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT'] as const;
const WEEKDAYS_JA = ['日', '月', '火', '水', '木', '金', '土'] as const;

function weekdayIndex(ymd: string): number {
  return new Date(Date.UTC(Number(ymd.slice(0, 4)), Number(ymd.slice(5, 7)) - 1, Number(ymd.slice(8, 10)))).getUTCDay();
}

/** 週の日の見出し: 等幅の '10.15' と 'THU'、読み上げ用の '10月15日（木）'。 */
export function dayHeading(ymd: string): { date: string; weekday: string; label: string } {
  const index = weekdayIndex(ymd);
  return {
    date: `${ymd.slice(5, 7)}.${ymd.slice(8, 10)}`,
    weekday: WEEKDAYS_EN[index],
    label: `${Number(ymd.slice(5, 7))}月${Number(ymd.slice(8, 10))}日（${WEEKDAYS_JA[index]}）`,
  };
}

/** 週の送りの間に出す期間（'10.15 – 10.21'）。年をまたぐ時は年も付ける。 */
export function rangeLabel(from: string, to: string): string {
  const short = (ymd: string) => `${ymd.slice(5, 7)}.${ymd.slice(8, 10)}`;
  const long = (ymd: string) => `${ymd.slice(0, 4)}.${short(ymd)}`;
  return from.slice(0, 4) === to.slice(0, 4) ? `${short(from)} – ${short(to)}` : `${long(from)} – ${long(to)}`;
}
