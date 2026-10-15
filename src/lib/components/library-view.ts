// Library の一覧用。同じ Work・同じ媒体の Edition を見分ける副題と並び順を作る。

import type { Edition, Release } from '../db/schema';
import { formatReleaseDate, isValidYmd, todayJst } from '../dates';
import { normalizeTitle } from '../normalize';
import { MEDIA_TYPES } from './labels';

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
