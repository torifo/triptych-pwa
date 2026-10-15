// 放送局の要素（ON AIR・カウントダウン）の判定。放送日は朝5時区切り（25:30 は前日の番組）。

import { todayJst } from '../dates';
import type { Edition, Release } from '../db/schema';

const BROADCAST_DAY_START_HOURS = 5;
const HOUR_MS = 60 * 60 * 1000;
const JST_OFFSET_MS = 9 * HOUR_MS;
const TIME_RE = /^(\d{1,2}):(\d{2})$/;
const ON_AIR_KINDS = new Set<Release['kind']>(['episode', 'stream-start']);
const TRACKED_STATUSES = new Set<Edition['status']>(['plan', 'active', 'paused']);

/** 放送日（JST・朝5時区切り）。深夜 1 時は前日の放送日。 */
export function broadcastDayJst(now: Date = new Date()): string {
  return todayJst(new Date(now.getTime() - BROADCAST_DAY_START_HOURS * HOUR_MS));
}

/** 放送日 date と時刻 time（'25:30' のような 24 時超えを含む）から実際の時刻。不正なら undefined。 */
export function airingInstant(release: Pick<Release, 'date' | 'time'>): Date | undefined {
  const match = release.time ? TIME_RE.exec(release.time) : null;
  const ymd = /^(\d{4})-(\d{2})-(\d{2})$/.exec(release.date);
  if (!match || !ymd) return undefined;
  const hours = Number(match[1]);
  const minutes = Number(match[2]);
  if (minutes > 59 || hours > 47) return undefined;
  const midnightUtc = Date.UTC(Number(ymd[1]), Number(ymd[2]) - 1, Number(ymd[3]));
  return new Date(midnightUtc - JST_OFFSET_MS + (hours * 60 + minutes) * 60 * 1000);
}

/** 今日（放送日）に放送・配信がある追跡中 Edition の id。配信中のサービスの次回配信（rolling）も数える。 */
export function onAirEditionIds(
  releases: readonly Release[],
  editions: ReadonlyMap<string, Pick<Edition, 'status'>>,
  today: string,
): Set<string> {
  const ids = new Set<string>();
  for (const r of releases) {
    if (!ON_AIR_KINDS.has(r.kind) || (r.precision ?? 'day') !== 'day' || r.date !== today) continue;
    const edition = editions.get(r.editionId);
    if (edition && TRACKED_STATUSES.has(edition.status)) ids.add(r.editionId);
  }
  return ids;
}

/** 「あと 3:12」（24 時間以内の未来だけ）。時刻の無いもの・過ぎたものは undefined。 */
export function countdownLabel(release: Pick<Release, 'date' | 'time'>, now: Date): string | undefined {
  const at = airingInstant(release);
  if (!at) return undefined;
  const ms = at.getTime() - now.getTime();
  if (ms <= 0 || ms > 24 * HOUR_MS) return undefined;
  const totalMinutes = Math.ceil(ms / 60000);
  const h = Math.floor(totalMinutes / 60);
  const m = totalMinutes % 60;
  return `あと ${h}:${String(m).padStart(2, '0')}`;
}
