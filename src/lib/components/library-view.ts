// Library の一覧用。同じ Work・同じ媒体の Edition を見分ける副題と並び順、棚の表紙と表示の保存。

import type { Edition, EditionProgress, MediaType, Release, Status, Work } from '../db/schema';
import { formatReleaseDate, isPastRelease, isValidYmd, todayJst } from '../dates';
import { normalizeTitle } from '../normalize';
import { editionCover, workCover, type CoverSource } from './cover';
import { MEDIA_TYPES, PROGRESS_UNIT_LABELS } from './labels';

type LibraryEdition = Pick<Edition, 'id' | 'workId' | 'mediaType' | 'title' | 'createdAt'>;

export type SeasonStart = Pick<Release, 'date' | 'precision'>;

const SEPARATOR_HEAD = /^[\s　:：\-－―・/／]+/u;

/** Edition 題名から Work 題名の頭を除いた副題。同じ題名なら空、頭が違えば題名そのまま。 */
export function editionSubtitle(workTitle: string, editionTitle: string): string {
  const work = workTitle.trim();
  const title = editionTitle.trim();
  if (!title || normalizeTitle(title) === normalizeTitle(work)) return '';
  if (work && title.startsWith(work)) {
    const rest = title.slice(work.length).replace(SEPARATOR_HEAD, '').trim();
    return rest || title;
  }
  return title;
}

/** Edition ごとの最初の新期開始（season-start）。日付が壊れたものは無視する。 */
export function seasonStartByEdition(releases: readonly Release[]): Map<string, SeasonStart> {
  const byEdition = new Map<string, SeasonStart>();
  for (const release of releases) {
    if (release.kind !== 'season-start' || !isValidYmd(release.date)) continue;
    const current = byEdition.get(release.editionId);
    if (!current || release.date < current.date) {
      byEdition.set(release.editionId, { date: release.date, precision: release.precision });
    }
  }
  return byEdition;
}

/** 放送開始の年月（'2026年10月〜'）。年だけ分かっているなら '2026年〜'。 */
export function seasonLabel(start: SeasonStart): string {
  const precision = start.precision === 'year' ? 'year' : 'month';
  return `${formatReleaseDate({ date: start.date, precision })}〜`;
}

/** '登録 2026/10/02'（JST の暦日）。日時が読めなければ空。 */
function registeredLabel(createdAt: string): string {
  const time = Date.parse(createdAt);
  return Number.isNaN(time) ? '' : `登録 ${todayJst(new Date(time)).replaceAll('-', '/')}`;
}

/**
 * 同じ Work・同じ媒体に Edition が 2 つ以上ある時だけ、見分ける副題（開始年月・題名）を返す。
 * 副題で見分けがつかない組には登録日を足す。1 つだけの Edition は含めない。
 */
export function editionDisambiguators(
  editions: readonly LibraryEdition[],
  workTitleOf: (workId: string) => string,
  seasonStarts: ReadonlyMap<string, SeasonStart>,
): Map<string, string> {
  const groups = new Map<string, LibraryEdition[]>();
  for (const edition of editions) {
    const key = `${edition.workId}\u0000${edition.mediaType}`;
    const list = groups.get(key) ?? [];
    list.push(edition);
    groups.set(key, list);
  }

  const labels = new Map<string, string>();
  for (const group of groups.values()) {
    if (group.length < 2) continue;
    const base = group.map((edition) => {
      const start = seasonStarts.get(edition.id);
      return [
        start ? seasonLabel(start) : '',
        editionSubtitle(workTitleOf(edition.workId), edition.title),
      ]
        .filter(Boolean)
        .join(' ・ ');
    });
    group.forEach((edition, i) => {
      const clashes = base.filter((label) => label === base[i]).length > 1;
      const label = [base[i], clashes ? registeredLabel(edition.createdAt) : ''].filter(Boolean).join(' ・ ');
      if (label) labels.set(edition.id, label);
    });
  }
  return labels;
}

/** Work 題名 → 媒体 → 開始日（無いものは後ろ）→ 登録順。同じ Work の Edition が毎回同じ順に並ぶようにする。 */
export function sortLibraryEditions<T extends LibraryEdition>(
  editions: readonly T[],
  workTitleOf: (workId: string) => string,
  seasonStarts: ReadonlyMap<string, SeasonStart>,
): T[] {
  const mediaRank = (mediaType: Edition['mediaType']) => MEDIA_TYPES.indexOf(mediaType);
  return [...editions].sort((a, b) => {
    const byWork = workTitleOf(a.workId).localeCompare(workTitleOf(b.workId), 'ja');
    if (byWork !== 0) return byWork;
    if (a.workId !== b.workId) return a.workId < b.workId ? -1 : 1;
    const byMedia = mediaRank(a.mediaType) - mediaRank(b.mediaType);
    if (byMedia !== 0) return byMedia;
    const sa = seasonStarts.get(a.id)?.date ?? '9999-12-31';
    const sb = seasonStarts.get(b.id)?.date ?? '9999-12-31';
    if (sa !== sb) return sa < sb ? -1 : 1;
    if (a.createdAt !== b.createdAt) return a.createdAt < b.createdAt ? -1 : 1;
    return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
  });
}

// ---------------------------------------------------------------------------
// 棚・一覧の表示
// ---------------------------------------------------------------------------

/** 並べ方: 作品名順 / 次の予定が近い順 / 追加した順（新しいものから）。 */
export type LibrarySort = 'title' | 'next' | 'added';
export const LIBRARY_SORT_KEY = 'triptych:library-sort';
const LIBRARY_SORTS: LibrarySort[] = ['title', 'next', 'added'];

/** Edition ごとの、今日以降で最も近い予定の日（月・年精度は期間の終わりまで今日以降とみなす）。 */
export function nextDateByEdition(releases: readonly Release[], today: string): Map<string, string> {
  const next = new Map<string, string>();
  for (const r of releases) {
    if (!isValidYmd(r.date) || isPastRelease(r, today)) continue;
    const current = next.get(r.editionId);
    if (!current || r.date < current) next.set(r.editionId, r.date);
  }
  return next;
}

/** 並べ方に従って Edition を並べる。次の予定が無いもの・同じ日のものは作品名順。 */
export function sortLibrary<T extends LibraryEdition>(
  sort: LibrarySort,
  editions: readonly T[],
  workTitleOf: (workId: string) => string,
  seasonStarts: ReadonlyMap<string, SeasonStart>,
  nextDates: ReadonlyMap<string, string>,
): T[] {
  const byTitle = sortLibraryEditions(editions, workTitleOf, seasonStarts);
  if (sort === 'title') return byTitle;
  const rank = new Map(byTitle.map((e, i) => [e.id, i]));
  const tie = (a: T, b: T) => (rank.get(a.id) ?? 0) - (rank.get(b.id) ?? 0);
  if (sort === 'added') {
    return [...byTitle].sort((a, b) => (a.createdAt === b.createdAt ? tie(a, b) : a.createdAt < b.createdAt ? 1 : -1));
  }
  return [...byTitle].sort((a, b) => {
    const da = nextDates.get(a.id);
    const db = nextDates.get(b.id);
    if (da !== db) {
      if (!da) return 1;
      if (!db) return -1;
      return da < db ? -1 : 1;
    }
    return tie(a, b);
  });
}

export function loadLibrarySort(storage: Pick<Storage, 'getItem'> | null = browserStorage()): LibrarySort {
  try {
    const value = storage?.getItem(LIBRARY_SORT_KEY);
    return LIBRARY_SORTS.includes(value as LibrarySort) ? (value as LibrarySort) : 'title';
  } catch {
    return 'title';
  }
}

export function saveLibrarySort(sort: LibrarySort, storage: Pick<Storage, 'setItem'> | null = browserStorage()): void {
  try {
    storage?.setItem(LIBRARY_SORT_KEY, sort);
  } catch {
    // 保存できない環境では、並べ替え自体はできるので黙って諦める
  }
}

export type LibraryLayout = 'shelf' | 'list';

export const LIBRARY_LAYOUT_KEY = 'triptych:library-layout';

/** localStorage そのものに触れるだけで例外になる環境があるので、取り出しも包む。 */
function browserStorage(): Storage | null {
  try {
    return typeof localStorage === 'undefined' ? null : localStorage;
  } catch {
    return null;
  }
}

/** 端末に保存した表示。保存が無い・読めない・知らない値なら棚。 */
export function loadLibraryLayout(storage: Pick<Storage, 'getItem'> | null = browserStorage()): LibraryLayout {
  try {
    return storage?.getItem(LIBRARY_LAYOUT_KEY) === 'list' ? 'list' : 'shelf';
  } catch {
    return 'shelf';
  }
}

/** 表示の選択を端末に残す。保存できない環境では何もしない（切り替え自体はできる）。 */
export function saveLibraryLayout(
  layout: LibraryLayout,
  storage: Pick<Storage, 'setItem'> | null = browserStorage(),
): void {
  try {
    storage?.setItem(LIBRARY_LAYOUT_KEY, layout);
  } catch {
    // 容量超過・プライベートモードなど。表示は切り替わるので黙って諦める
  }
}

/**
 * 棚・一覧の表紙。Edition 自身の表紙 → 同じ Work の代表表紙（アニメの表紙を漫画・Web の棚にも借りる）。
 * 題名は Work 名、文字の表紙に出す媒体は Edition のもの。
 */
export function libraryCover(
  edition: Pick<Edition, 'coverUrl' | 'title' | 'mediaType'>,
  work: Pick<Work, 'title' | 'coverEditionId'> | undefined,
  workEditions: readonly Pick<Edition, 'id' | 'coverUrl' | 'mediaType'>[],
): CoverSource {
  const own = editionCover(edition, work?.title);
  if (own.url || !work) return own;
  return { ...own, url: workCover(work, workEditions).url };
}

/** 進捗の割合（0〜1）。全体数が分からなければ undefined。 */
export function progressRatio(progress: EditionProgress | undefined): number | undefined {
  if (!progress?.total || progress.total <= 0) return undefined;
  return Math.min(1, Math.max(0, progress.current / progress.total));
}

/** '3/12話'・'5巻'（全体数が無い時）。進捗が無ければ空。 */
export function progressText(progress: EditionProgress | undefined): string {
  if (!progress) return '';
  const total = progress.total ? `/${progress.total}` : '';
  return `${progress.current}${total}${PROGRESS_UNIT_LABELS[progress.unit]}`;
}

// ---------------------------------------------------------------------------
// 棚は作品ごとに1枚
// ---------------------------------------------------------------------------

/** 棚に出す媒体の順（表紙の色が強いアニメから。Work 詳細のタブと同じ）。 */
const SHELF_MEDIA_ORDER: MediaType[] = ['anime', 'manga', 'light-novel', 'web-novel'];
/** 作品の状態として出す順（いちばん動いているもの）。 */
const SHELF_STATUS_ORDER: Status[] = ['active', 'paused', 'plan', 'done', 'dropped'];

type ShelfEdition = Pick<Edition, 'id' | 'workId' | 'status' | 'mediaType'>;

export interface WorkShelfItem<E extends ShelfEdition> {
  workId: string;
  /** 絞り込みを通ったこの作品の Edition（渡された順のまま） */
  editions: E[];
  /** 押した時に開く Edition（今日放送のあるもの → 先頭） */
  lead: E;
  onAir: boolean;
  media: { mediaType: MediaType; count: number }[];
  status: Status;
}

/** 絞り込み済みの Edition を作品ごとにまとめる。並びは各作品の最初の Edition が出てきた順（作品名順を保つ）。 */
export function groupShelfByWork<E extends ShelfEdition>(
  editions: readonly E[],
  onAir: ReadonlySet<string>,
): WorkShelfItem<E>[] {
  const byWork = new Map<string, E[]>();
  for (const edition of editions) {
    const list = byWork.get(edition.workId);
    if (list) list.push(edition);
    else byWork.set(edition.workId, [edition]);
  }
  return [...byWork.entries()].map(([workId, list]) => {
    const counts = new Map<MediaType, number>();
    for (const e of list) counts.set(e.mediaType, (counts.get(e.mediaType) ?? 0) + 1);
    const statuses = new Set(list.map((e) => e.status));
    return {
      workId,
      editions: list,
      lead: list.find((e) => onAir.has(e.id)) ?? list[0],
      onAir: list.some((e) => onAir.has(e.id)),
      media: SHELF_MEDIA_ORDER.filter((m) => counts.has(m)).map((mediaType) => ({ mediaType, count: counts.get(mediaType) ?? 0 })),
      status: SHELF_STATUS_ORDER.find((s) => statuses.has(s)) ?? list[0].status,
    };
  });
}
