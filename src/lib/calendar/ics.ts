// 予定表への書き出し（iCalendar / RFC 5545）。Library の追跡中 Edition の Release を .ics に組む純関数。

import { addDaysYmd, compareYmd } from '../dates';
import type { Edition, Release, Work } from '../db/schema';
import { displayReleaseLabel } from '../components/labels';
import { airingInstant } from '../components/onair';
import { startedStreamServices } from '../components/release-history';
import { precisionOf, trackedReleases } from '../components/release-view';

/** 書き出す期間（今日から60日分）。 */
export const ICS_DAYS = 60;
/** 時刻のある予定の枠の長さ。 */
export const ICS_EVENT_MINUTES = 30;
export const ICS_TZID = 'Asia/Tokyo';
const UID_DOMAIN = 'triptych.local';
const MAX_LINE_OCTETS = 75;
const JST_OFFSET_MS = 9 * 60 * 60 * 1000;

export type IcsWhen =
  /** date / end は 'YYYYMMDD'（end は翌日。終日の DTEND は含まない日）。 */
  | { allDay: true; date: string; end: string }
  /** start / end は JST の 'YYYYMMDDTHHMMSS'。 */
  | { allDay: false; start: string; end: string };

export interface IcsEvent {
  uid: string;
  summary: string;
  description?: string;
  when: IcsWhen;
}

// ---------------------------------------------------------------------------
// 文字の扱い
// ---------------------------------------------------------------------------

/** TEXT の値を逃がす（\ ; , と改行）。 */
export function escapeIcsText(text: string): string {
  return text
    .replace(/\\/g, '\\\\')
    .replace(/;/g, '\\;')
    .replace(/,/g, '\\,')
    .replace(/\r\n|\r|\n/g, '\\n');
}

function utf8Octets(codePoint: number): number {
  if (codePoint < 0x80) return 1;
  if (codePoint < 0x800) return 2;
  if (codePoint < 0x10000) return 3;
  return 4;
}

/** 75 オクテットごとに折り返す（続きの行は空白1つで始める）。UTF-8 の文字の途中では折らない。 */
export function foldIcsLine(line: string): string {
  const parts: string[] = [];
  let current = '';
  let size = 0;
  for (const ch of line) {
    const n = utf8Octets(ch.codePointAt(0) ?? 0);
    if (size + n > MAX_LINE_OCTETS) {
      parts.push(current);
      current = ' ';
      size = 1;
    }
    current += ch;
    size += n;
  }
  parts.push(current);
  return parts.join('\r\n');
}

// ---------------------------------------------------------------------------
// Release → VEVENT
// ---------------------------------------------------------------------------

function pad2(n: number): string {
  return String(n).padStart(2, '0');
}

/** 実時刻を JST の 'YYYYMMDDTHHMMSS' に。 */
function jstLocal(instant: Date): string {
  const t = new Date(instant.getTime() + JST_OFFSET_MS);
  return (
    `${String(t.getUTCFullYear()).padStart(4, '0')}${pad2(t.getUTCMonth() + 1)}${pad2(t.getUTCDate())}` +
    `T${pad2(t.getUTCHours())}${pad2(t.getUTCMinutes())}${pad2(t.getUTCSeconds())}`
  );
}

function utcStamp(instant: Date): string {
  return (
    `${String(instant.getUTCFullYear()).padStart(4, '0')}${pad2(instant.getUTCMonth() + 1)}${pad2(instant.getUTCDate())}` +
    `T${pad2(instant.getUTCHours())}${pad2(instant.getUTCMinutes())}${pad2(instant.getUTCSeconds())}Z`
  );
}

function compactDate(ymd: string): string {
  return ymd.replace(/-/g, '');
}

/** 1つの Release を予定に。時刻（'25:30' は翌日の 01:30）があれば 30 分の枠、無ければ終日。UID は Release の id で固定。 */
export function releaseToIcsEvent(
  release: Pick<Release, 'id' | 'date' | 'time'>,
  summary: string,
  description?: string,
): IcsEvent {
  const at = airingInstant(release);
  const when: IcsWhen = at
    ? { allDay: false, start: jstLocal(at), end: jstLocal(new Date(at.getTime() + ICS_EVENT_MINUTES * 60 * 1000)) }
    : { allDay: true, date: compactDate(release.date), end: compactDate(addDaysYmd(release.date, 1)) };
  return { uid: `${release.id}@${UID_DOMAIN}`, summary, description, when };
}

function unique(list: readonly string[]): string[] {
  return [...new Set(list.map((s) => s.trim()).filter(Boolean))];
}

export interface IcsSource {
  releases: readonly Release[];
  editions: ReadonlyMap<string, Edition>;
  works: ReadonlyMap<string, Pick<Work, 'title'>>;
  /** JST の今日（YYYY-MM-DD）。 */
  today: string;
  days?: number;
}

/**
 * 書き出す予定: 追跡中 Edition の日精度の Release のうち、今日から days 日分（配信中のサービスの次回の配信は除く）。
 * 放送の話には、同じ日時の番組（しょぼかる）の局名と配信サービスを DESCRIPTION に添える。
 */
export function icsEventsFor({ releases, editions, works, today, days = ICS_DAYS }: IcsSource): IcsEvent[] {
  const last = addDaysYmd(today, days - 1);
  const streaming = startedStreamServices(releases, today);

  return trackedReleases(releases, editions)
    .filter((r) => precisionOf(r) === 'day')
    .filter((r) => {
      try {
        return compareYmd(r.date, today) >= 0 && compareYmd(r.date, last) <= 0;
      } catch {
        return false; // 日付の壊れた行は書き出さない
      }
    })
    .map((release) => {
      const edition = editions.get(release.editionId);
      const title = (edition && works.get(edition.workId)?.title) || edition?.title || '';
      const label = displayReleaseLabel(release);
      const summary = [title, label].filter((s) => s.trim()).join(' ');

      const lines: string[] = [];
      if (release.kind === 'episode') {
        const programs = (edition?.syncHints?.broadcasts ?? []).filter(
          (b) => b.date === release.date && b.time === release.time,
        );
        const channels = unique(programs.filter((b) => !b.isStreaming).map((b) => b.channel));
        const services = unique([
          ...(streaming.get(release.editionId) ?? []),
          ...programs.filter((b) => b.isStreaming).map((b) => b.channel),
        ]);
        if (channels.length > 0) lines.push(`放送: ${channels.join('・')}`);
        if (services.length > 0) lines.push(`配信: ${services.join('・')}`);
      }
      return releaseToIcsEvent(release, summary, lines.length > 0 ? lines.join('\n') : undefined);
    });
}

// ---------------------------------------------------------------------------
// VCALENDAR
// ---------------------------------------------------------------------------

const VTIMEZONE = [
  'BEGIN:VTIMEZONE',
  `TZID:${ICS_TZID}`,
  'BEGIN:STANDARD',
  'DTSTART:19700101T000000',
  'TZOFFSETFROM:+0900',
  'TZOFFSETTO:+0900',
  'TZNAME:JST',
  'END:STANDARD',
  'END:VTIMEZONE',
];

function eventLines(event: IcsEvent, stamp: string): string[] {
  const lines = ['BEGIN:VEVENT', `UID:${escapeIcsText(event.uid)}`, `DTSTAMP:${stamp}`];
  if (event.when.allDay) {
    lines.push(`DTSTART;VALUE=DATE:${event.when.date}`, `DTEND;VALUE=DATE:${event.when.end}`);
  } else {
    lines.push(`DTSTART;TZID=${ICS_TZID}:${event.when.start}`, `DTEND;TZID=${ICS_TZID}:${event.when.end}`);
  }
  lines.push(`SUMMARY:${escapeIcsText(event.summary)}`);
  if (event.description) lines.push(`DESCRIPTION:${escapeIcsText(event.description)}`);
  // 予定表の「空き時間」を塞がない（見逃さないための印）
  lines.push('TRANSP:TRANSPARENT', 'END:VEVENT');
  return lines;
}

/** .ics の本文（CRLF・75 オクテットで折り返し）。now は DTSTAMP。 */
export function buildIcs(events: readonly IcsEvent[], now: Date): string {
  const stamp = utcStamp(now);
  const lines = [
    'BEGIN:VCALENDAR',
    'VERSION:2.0',
    'PRODID:-//triptych//Calendar//JA',
    'CALSCALE:GREGORIAN',
    'METHOD:PUBLISH',
    'X-WR-CALNAME:triptych',
    `X-WR-TIMEZONE:${ICS_TZID}`,
    ...VTIMEZONE,
    ...events.flatMap((event) => eventLines(event, stamp)),
    'END:VCALENDAR',
  ];
  return lines.map(foldIcsLine).join('\r\n') + '\r\n';
}

export function icsFilename(today: string): string {
  return `triptych-${compactDate(today)}.ics`;
}
