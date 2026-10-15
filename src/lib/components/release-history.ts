// WorkDetail の Release 一覧の補助（30 日より前の展開・次話と配信先の組み立て）。純関数で vitest 固定。

import type { Edition, Release } from '../db/schema';
import { isPastRelease } from '../dates';
import { compareReleases, groupEditionReleases, PAST_DAYS_SHOWN } from './release-view';

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
  /** 配信開始日が過ぎている。今日始まるものは時刻がまだ先かもしれないので「予定」に数える。 */
  started: boolean;
}

/** 配信開始（stream-start）を日付順に。過去分も含める。 */
export function streamEntries(releases: readonly Release[], today: string): StreamEntry[] {
  return releases
    .filter((r) => r.kind === 'stream-start')
    .sort(compareReleases)
    .map((release) => ({
      release,
      service: streamServiceName(release.label),
      started: isPastRelease(release, today),
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
