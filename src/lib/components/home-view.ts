// Home・ヘッダーの表示の組み立て（今日の並び・表紙の選び方・今期・ON AIR の数）。
// DOM にも store にも依存しない純関数に寄せ、vitest で固定する。

import type { Edition, Release, Work } from '../db/schema';
import type { CatalogQuery } from '../catalog/types';
import { displayReleaseTime, todayJst } from '../dates';
import { editionDisplayCover, type CoverSource } from './cover';
import { airingInstant, broadcastDayJst, countdownLabel, onAirEditionIds } from './onair';
import { compareReleases, isTrackedEdition, precisionOf, timeMinutes } from './release-view';
import { streamServiceName } from './release-history';
import { displayReleaseLabel } from './labels';

// ---------------------------------------------------------------------------
// 表紙
// ---------------------------------------------------------------------------

/** Edition ごとの表紙（editionDisplayCover: 自分の表紙だけ、Web 小説だけ文庫の表紙を借りる）。題名は Work 名。 */
export function buildCoverIndex(
  editions: readonly Edition[],
  works: readonly Pick<Work, 'id' | 'title'>[],
): Map<string, CoverSource> {
  const workMap = new Map(works.map((w) => [w.id, w]));
  const byWork = new Map<string, Edition[]>();
  for (const edition of editions) {
    const list = byWork.get(edition.workId) ?? [];
    list.push(edition);
    byWork.set(edition.workId, list);
  }

  const index = new Map<string, CoverSource>();
  for (const edition of editions) {
    index.set(edition.id, editionDisplayCover(edition, workMap.get(edition.workId), byWork.get(edition.workId) ?? []));
  }
  return index;
}

// ---------------------------------------------------------------------------
// 今日（Home の表紙の横並び）
// ---------------------------------------------------------------------------

/** 放送の Release（日付は朝5時区切りの放送日）。それ以外（巻）は暦日で「今日」を決める。 */
const BROADCAST_DAY_KINDS = new Set<Release['kind']>(['episode', 'stream-start', 'season-start']);

/** 1つの Edition に今日の Release が複数ある時、表に出すものの順（話数の分かる放送を先に）。 */
const PRIMARY_ORDER: Release['kind'][] = ['episode', 'season-start', 'stream-start', 'volume'];

/** 時刻が無い時に表紙の左下へ載せる言葉。 */
const BADGE_WITHOUT_TIME: Record<Release['kind'], string> = {
  episode: '放送',
  'season-start': '放送開始',
  'stream-start': '配信',
  volume: '発売',
};

export interface TodayItem {
  /** 表に出す Release（並び・カウントダウン・遷移に使う）。 */
  release: Release;
  editionId: string;
  /** この項目にまとめた Release の id（「直近」から外すのに使う）。 */
  releaseIds: string[];
  /** 表紙の左下に載せる等幅の文字。時刻、無ければ種類の言葉。 */
  badge: string;
  /** 放送の話数（'#14' に組む）。 */
  episode?: number;
  /** 今日配信のあるサービス名（重複なし）。 */
  services: string[];
}

/** 話数。externalId（'<anilistId>:<n>'）を優先し、無ければラベルの最後の「n話」。 */
export function episodeNumberOf(release: Pick<Release, 'kind' | 'label' | 'externalId'>): number | undefined {
  if (release.kind !== 'episode') return undefined;
  const fromId = release.externalId ? /:(\d+)$/.exec(release.externalId) : null;
  if (fromId) return Number(fromId[1]);
  const matches = [...release.label.matchAll(/(\d+)\s*話/g)];
  const last = matches[matches.length - 1];
  return last ? Number(last[1]) : undefined;
}

function isToday(release: Release, broadcastDay: string, calendarDay: string): boolean {
  if (precisionOf(release) !== 'day') return false;
  return release.date === (BROADCAST_DAY_KINDS.has(release.kind) ? broadcastDay : calendarDay);
}

function comparePrimary(a: Release, b: Release): number {
  const ka = PRIMARY_ORDER.indexOf(a.kind);
  const kb = PRIMARY_ORDER.indexOf(b.kind);
  return ka - kb || compareReleases(a, b);
}

/** 今日の放送・配信（rolling を含む）・発売・放送開始を、追跡中 Edition ごとに1つにまとめて時刻順に。 */
export function todayItems(
  releases: readonly Release[],
  editions: ReadonlyMap<string, Pick<Edition, 'status'>>,
  now: Date,
): TodayItem[] {
  const broadcastDay = broadcastDayJst(now);
  const calendarDay = todayJst(now);

  const byEdition = new Map<string, Release[]>();
  for (const release of releases) {
    if (!isTrackedEdition(editions.get(release.editionId))) continue;
    if (!isToday(release, broadcastDay, calendarDay)) continue;
    const list = byEdition.get(release.editionId) ?? [];
    list.push(release);
    byEdition.set(release.editionId, list);
  }

  const items: TodayItem[] = [];
  for (const [editionId, list] of byEdition) {
    const sorted = [...list].sort(comparePrimary);
    const primary = sorted[0];
    const services: string[] = [];
    for (const r of list.filter((x) => x.kind === 'stream-start').sort(compareReleases)) {
      const name = streamServiceName(r.label);
      if (name && !services.includes(name)) services.push(name);
    }
    items.push({
      release: primary,
      editionId,
      releaseIds: sorted.map((r) => r.id),
      badge: badgeTime(primary) ?? BADGE_WITHOUT_TIME[primary.kind],
      episode: episodeNumberOf(primary),
      services,
    });
  }

  return items.sort(
    (a, b) => timeMinutes(a.release.time) - timeMinutes(b.release.time) || comparePrimary(a.release, b.release),
  );
}

/** 表紙に載せる時刻。時計表記の設定では 25:30 を「翌1:30」と書く。 */
function badgeTime(release: Pick<Release, 'date' | 'time'>): string | undefined {
  const shown = displayReleaseTime(release);
  return shown.time ? `${shown.nextDay ? '翌' : ''}${shown.time}` : undefined;
}

export interface TodaySubLabel {
  text: string;
  /** カウントダウン（まだ始まっていない）。強めに出す。 */
  upcoming: boolean;
}

/** 表紙の下の2行目: 「あと 3:12」→「#14」→ 配信サービス名 → ラベル（第12巻）。 */
export function todaySubLabel(item: TodayItem, now: Date): TodaySubLabel {
  const countdown = countdownLabel(item.release, now);
  if (countdown) return { text: countdown, upcoming: true };
  if (item.episode !== undefined) return { text: `#${item.episode}`, upcoming: false };
  if (item.services.length > 0) return { text: item.services.join('・'), upcoming: false };
  return { text: displayReleaseLabel(item.release), upcoming: false };
}

const AIRING_WINDOW_MINUTES = 30;

/** いま放送中か（放送の時刻から 30 分）。時刻の無いもの・放送でないものは false。 */
export function isAiringNow(release: Pick<Release, 'kind' | 'date' | 'time'>, now: Date): boolean {
  if (release.kind !== 'episode') return false;
  const at = airingInstant(release);
  if (!at) return false;
  const elapsed = now.getTime() - at.getTime();
  return elapsed >= 0 && elapsed < AIRING_WINDOW_MINUTES * 60 * 1000;
}

/** 「直近」から今日の横並びに出したものを除く（同じものを2度出さない）。 */
export function withoutToday(releases: readonly Release[], items: readonly TodayItem[]): Release[] {
  const shown = new Set(items.flatMap((item) => item.releaseIds));
  return releases.filter((r) => !shown.has(r.id));
}

/**
 * 新着の欄に出す Release。直近や「今日」にすでに出ているものは、そちらに NEW の印で出すので外す（同じ行が2回並ばないように）。
 * inline は外した数（「上に NEW で出しています」の案内に使う）。
 */
export function freshOutsideLists(
  unseen: readonly Release[],
  upcoming: readonly Pick<Release, 'id'>[],
  todays: readonly Pick<TodayItem, 'releaseIds'>[],
): { list: Release[]; inline: number } {
  const shown = new Set([...upcoming.map((r) => r.id), ...todays.flatMap((item) => item.releaseIds)]);
  const list = unseen.filter((r) => !shown.has(r.id));
  return { list, inline: unseen.length - list.length };
}

// ---------------------------------------------------------------------------
// ヘッダー
// ---------------------------------------------------------------------------

/** ヘッダーの「ON AIR n」の n（今日の放送日に放送・配信がある追跡中 Edition の数）。 */
export function onAirCount(
  releases: readonly Release[],
  editions: ReadonlyMap<string, Pick<Edition, 'status'>>,
  now: Date,
): number {
  return onAirEditionIds(releases, editions, broadcastDayJst(now)).size;
}

const WEEKDAYS_EN = ['SUN', 'MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT'] as const;

/** Home の日付の帯（'2026.10.15 THU'）。不正な日付はそのまま返す。 */
export function datelineLabel(ymd: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(ymd);
  if (!match) return ymd;
  const weekday = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]))).getUTCDay();
  return `${match[1]}.${match[2]}.${match[3]} ${WEEKDAYS_EN[weekday]}`;
}

// ---------------------------------------------------------------------------
// 今期（Library が空の時の「今期の放送中」）
// ---------------------------------------------------------------------------

export type AnimeSeason = NonNullable<CatalogQuery['season']>;

/** 暦日の季節（1-3 冬 / 4-6 春 / 7-9 夏 / 10-12 秋）。 */
export function currentSeason(today: string): { year: number; season: AnimeSeason } {
  const year = Number(today.slice(0, 4));
  const month = Number(today.slice(5, 7));
  const season: AnimeSeason = month <= 3 ? 'WINTER' : month <= 6 ? 'SPRING' : month <= 9 ? 'SUMMER' : 'FALL';
  return { year, season };
}
