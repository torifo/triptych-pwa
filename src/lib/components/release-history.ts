// WorkDetail の Release 一覧の補助（30 日より前の展開・次話と配信先の組み立て）。純関数で vitest 固定。

import type { BroadcastHint, Edition, Release } from '../db/schema';
import { isPastRelease, isValidYmd, todayJst } from '../dates';
import { airingInstant, broadcastDayJst, countdownLabel } from './onair';
import { compareReleases, groupEditionReleases, PAST_DAYS_SHOWN, precisionOf, timeMinutes } from './release-view';

// ---------------------------------------------------------------------------
// 配信先（stream-start）
// ---------------------------------------------------------------------------

const STREAM_LABEL_SUFFIX = /\s*配信$/;

/** 配信開始 Release のラベル（collector は 'Netflix 配信' の形）からサービス名を取り出す。 */
export function streamServiceName(label: string): string {
  const trimmed = label.trim();
  const name = trimmed.replace(STREAM_LABEL_SUFFIX, '').trim();
  return name || trimmed;
}

export interface StreamEntry {
  release: Release;
  service: string;
  /** 配信が始まっている（isStreamStarted）。 */
  started: boolean;
}

/** 配信が始まっているか。開始日が過ぎたもの（今日はまだ「予定」）と、配信中のサービスの次回の配信（rolling）。 */
export function isStreamStarted(release: Release, today: string): boolean {
  return !!release.rolling || isPastRelease(release, today);
}

/** 配信開始（stream-start）を日付順に。過去分も含める。 */
export function streamEntries(releases: readonly Release[], today: string): StreamEntry[] {
  return releases
    .filter((r) => r.kind === 'stream-start')
    .sort(compareReleases)
    .map((release) => ({
      release,
      service: streamServiceName(release.label),
      started: isStreamStarted(release, today),
    }));
}

/** Edition ごとの「すでに配信が始まっているサービス名」（重複なし・始まった順）。Home / Calendar の次話行に添える。 */
export function startedStreamServices(
  releases: readonly Release[],
  today: string,
): Map<string, string[]> {
  const byEdition = new Map<string, string[]>();
  for (const entry of streamEntries(releases, today)) {
    if (!entry.started || !entry.service) continue;
    const list = byEdition.get(entry.release.editionId) ?? [];
    if (!list.includes(entry.service)) list.push(entry.service);
    byEdition.set(entry.release.editionId, list);
  }
  return byEdition;
}

// ---------------------------------------------------------------------------
// Edition の Release 一覧
// ---------------------------------------------------------------------------

export interface EditionReleaseView {
  /** アニメ面のみ。今日以降の episode（先頭が次話）。日付昇順。 */
  nextEpisodes: Release[];
  /** アニメ面のみ。配信開始の全件（過去も含む）。日付昇順。 */
  streams: StreamEntry[];
  /** 今日以降（アニメ面では episode / stream-start を除く）。日付昇順。 */
  upcoming: Release[];
  /** 直近 30 日の過去分（アニメ面では stream-start を除く）。新しい順。 */
  recent: Release[];
  /** 30 日より前の過去分（アニメ面では stream-start を除く）。新しい順。初期は畳んでおく。 */
  older: Release[];
}

export function buildEditionReleaseView(
  releases: readonly Release[],
  mediaType: Edition['mediaType'],
  today: string,
  pastDays: number = PAST_DAYS_SHOWN,
): EditionReleaseView {
  const groups = groupEditionReleases(releases, mediaType, today, pastDays);
  const anime = mediaType === 'anime';
  // 配信開始は streams にまとめて出すので、他の一覧からは外す（二重に出さない）。
  const isStream = (r: Release) => anime && r.kind === 'stream-start';

  const listed = new Set([...groups.broadcast, ...groups.upcoming, ...groups.recent].map((r) => r.id));
  const older = releases
    .filter((r) => !listed.has(r.id) && !isStream(r))
    .sort((a, b) => compareReleases(b, a));

  return {
    nextEpisodes: groups.broadcast.filter((r) => !isStream(r)),
    streams: anime ? streamEntries(releases, today) : [],
    upcoming: groups.upcoming,
    recent: groups.recent.filter((r) => !isStream(r)),
    older,
  };
}

// ---------------------------------------------------------------------------
// 次話の帯・番組（局名と時刻）
// ---------------------------------------------------------------------------

/**
 * Release 一覧の「今日」。アニメは放送日（朝5時区切り）にして、25:30 の回を深夜 0 時を過ぎても次話に残す。
 * 本・Web は暦日のまま。
 */
export function releaseDayFor(mediaType: Edition['mediaType'], now: Date): string {
  return mediaType === 'anime' ? broadcastDayJst(now) : todayJst(now);
}

export interface NextEpisodeBand {
  release: Release;
  /** 今日（放送日）の回。ON AIR のランプを点ける。 */
  onAirToday: boolean;
  /** 24 時間以内なら「あと 3:12」。 */
  countdown?: string;
}

/** 次話の帯に出す回（nextEpisodes の先頭）。today は releaseDayFor の放送日。 */
export function nextEpisodeBand(
  nextEpisodes: readonly Release[],
  today: string,
  now: Date,
): NextEpisodeBand | undefined {
  const release = nextEpisodes[0];
  if (!release) return undefined;
  return {
    release,
    onAirToday: precisionOf(release) === 'day' && release.date === today,
    countdown: countdownLabel(release, now),
  };
}

/** 帯の後ろの「予定」: 帯に出した次話より後の放送と、それ以外の今日以降を日付順に。 */
export function laterReleases(view: Pick<EditionReleaseView, 'nextEpisodes' | 'upcoming'>): Release[] {
  return [...view.nextEpisodes.slice(1), ...view.upcoming].sort(compareReleases);
}

function compareBroadcasts(a: BroadcastHint, b: BroadcastHint): number {
  if (a.date !== b.date) return a.date < b.date ? -1 : 1;
  const ta = timeMinutes(a.time);
  const tb = timeMinutes(b.time);
  if (ta !== tb) return ta < tb ? -1 : 1;
  return a.channel.localeCompare(b.channel, 'ja');
}

/**
 * 帯の下に出す番組（しょぼかるの局名と時刻）。今日（放送日）以降で、まだ始まっていないものを日時順に先頭 limit 件。
 * 時刻が読めない番組は日付だけで判断する。
 */
export function upcomingBroadcasts(
  broadcasts: readonly BroadcastHint[] | undefined,
  now: Date,
  limit = 3,
): BroadcastHint[] {
  const today = broadcastDayJst(now);
  return (broadcasts ?? [])
    .filter((b) => isValidYmd(b.date) && b.date >= today)
    .filter((b) => {
      const at = airingInstant(b);
      return !at || at.getTime() > now.getTime();
    })
    .sort(compareBroadcasts)
    .slice(0, limit);
}

/** 帯の下の1行。同じ日時（と放送／配信の別）の局をまとめ、表に出す局と残りの局数を持つ。 */
export interface BroadcastSlot {
  channel: string;
  /** まとめた残りの局数（「ほか 2 局」） */
  others: number;
  date: string;
  time: string;
  isStreaming: boolean;
}

// 同じ時刻に系列局が並ぶ時に表へ出す局（全国波・在京キー局）。先に書いたものほど優先する。
const KEY_STATIONS = ['NHK総合', 'NHK Eテレ', '日本テレビ', 'TBS', 'フジテレビ', 'テレビ朝日', 'テレビ東京', 'TOKYO MX'];

function stationRank(channel: string): number {
  const i = KEY_STATIONS.findIndex((key) => channel.startsWith(key));
  return i === -1 ? KEY_STATIONS.length : i;
}

/**
 * upcomingBroadcasts を同じ日時ごとに1行へまとめ、先頭 limit 行。
 * 系列の地方局が同じ時刻に並ぶと局名だけの行が続くので、キー局（無ければ局名の順で先頭）を表に出して残りを数える。
 */
export function upcomingBroadcastSlots(
  broadcasts: readonly BroadcastHint[] | undefined,
  now: Date,
  limit = 3,
): BroadcastSlot[] {
  const groups = new Map<string, BroadcastHint[]>();
  for (const b of upcomingBroadcasts(broadcasts, now, Number.POSITIVE_INFINITY)) {
    const key = `${b.date}|${b.time}|${b.isStreaming ? 'stream' : 'air'}`;
    const group = groups.get(key);
    if (group) group.push(b);
    else groups.set(key, [b]);
  }
  const slots = [...groups.values()].map((group): BroadcastSlot => {
    const [lead] = [...group].sort(
      (a, b) => stationRank(a.channel) - stationRank(b.channel) || a.channel.localeCompare(b.channel, 'ja'),
    );
    return { channel: lead.channel, others: group.length - 1, date: lead.date, time: lead.time, isStreaming: lead.isStreaming };
  });
  // 同じ日時なら放送を先、配信を後に
  slots.sort((a, b) => {
    if (a.date !== b.date) return a.date < b.date ? -1 : 1;
    const ta = timeMinutes(a.time);
    const tb = timeMinutes(b.time);
    if (ta !== tb) return ta < tb ? -1 : 1;
    return Number(a.isStreaming) - Number(b.isStreaming);
  });
  return slots.slice(0, limit);
}
