// カレンダーに重ねるカタログの出来事（Library の外の放送・発売）。取得・除外・並べ方を純関数で vitest 固定。

import { ApiError } from '../api';
import { DETAIL_NOT_BUILT } from '../catalog/api';
import type {
  CatalogEvent,
  CatalogEventKind,
  CatalogEventPage,
  CatalogEventQuery,
  CatalogEventType,
} from '../catalog/types';
import { addDaysYmd, isValidYmd } from '../dates';
import type { Edition, Work } from '../db/schema';
import { normalizeTitle } from '../normalize';
import type { CoverSource } from '../components/cover';
import { isSafeHttpUrl, timeMinutes } from '../components/release-view';

// ---------------------------------------------------------------------------
// Library にある作品を除く
// ---------------------------------------------------------------------------

export interface LibraryKeys {
  /** Work の題名（正規化済み）。 */
  titles: Set<string>;
  /** Edition の AniList の entryId（'anilist:<id>'）。 */
  entryIds: Set<string>;
}

export function libraryKeys(
  works: readonly Pick<Work, 'title'>[],
  editions: readonly Pick<Edition, 'sourceRefs'>[],
): LibraryKeys {
  const titles = new Set<string>();
  for (const work of works) {
    const key = normalizeTitle(work.title);
    if (key) titles.add(key);
  }
  const entryIds = new Set<string>();
  for (const edition of editions) {
    for (const ref of edition.sourceRefs) {
      if (ref.source === 'anilist' && ref.externalId) entryIds.add(`anilist:${ref.externalId}`);
    }
  }
  return { titles, entryIds };
}

export function isInLibrary(event: Pick<CatalogEvent, 'title' | 'entryId'>, keys: LibraryKeys): boolean {
  return keys.entryIds.has(event.entryId) || keys.titles.has(normalizeTitle(event.title));
}

/**
 * 重ねて出す出来事: 絞り込みの種類だけ、Library にある作品は除き、同じ作品の同じ日の同じ出来事は1つに。
 * 並びは入力のまま（date 昇順→人気順）。
 */
export function overlayEvents(
  events: readonly CatalogEvent[],
  keys: LibraryKeys,
  kinds: readonly CatalogEventKind[],
): CatalogEvent[] {
  const seen = new Set<string>();
  const out: CatalogEvent[] = [];
  for (const event of events) {
    if (!kinds.includes(event.kind) || isInLibrary(event, keys)) continue;
    const key = `${event.clusterId}|${event.date}|${event.event}|${event.label}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(event);
  }
  return out;
}

// ---------------------------------------------------------------------------
// 日ごとの並びと畳み
// ---------------------------------------------------------------------------

/** 日ごとに束ねる。日の中は時刻のあるもの（時刻順）→ 時刻の無いもの（入力の順＝人気順）。 */
export function groupEventsByDate(events: readonly CatalogEvent[]): Map<string, CatalogEvent[]> {
  const map = new Map<string, CatalogEvent[]>();
  for (const event of events) {
    const list = map.get(event.date) ?? [];
    list.push(event);
    map.set(event.date, list);
  }
  for (const [date, list] of map) {
    // sort は安定なので、時刻の無いもの同士は入力の順のまま
    map.set(
      date,
      [...list].sort((a, b) => {
        const ta = timeMinutes(a.time ?? undefined);
        const tb = timeMinutes(b.time ?? undefined);
        return ta === tb ? 0 : ta < tb ? -1 : 1;
      }),
    );
  }
  return map;
}

/** 1日に出す上限。超えたら「ほか n 件」で畳む。 */
export const DAY_EVENT_LIMIT = 5;

export function foldEvents<T>(list: readonly T[], expanded: boolean, limit = DAY_EVENT_LIMIT): { shown: T[]; rest: number } {
  if (expanded || list.length <= limit) return { shown: [...list], rest: 0 };
  return { shown: list.slice(0, limit), rest: list.length - limit };
}

// ---------------------------------------------------------------------------
// 行の文言・表紙・行き先
// ---------------------------------------------------------------------------

const EVENT_KIND_WORDS: Record<CatalogEventType, string> = {
  episode: '放送',
  'season-start': '放送開始',
  movie: '公開',
  release: '発売',
};

/** 行の右に出す種別。 */
export function eventKindWord(type: CatalogEventType): string {
  return EVENT_KIND_WORDS[type];
}

/** 題名の下に出す言葉（「第3話」「2巻」）。空なら種別。 */
export function eventLabel(event: Pick<CatalogEvent, 'label' | 'event'>): string {
  return event.label.trim() || eventKindWord(event.event);
}

export function eventCover(event: Pick<CatalogEvent, 'cover' | 'title' | 'kind'>): CoverSource {
  const url = event.cover?.trim();
  return {
    url: url && isSafeHttpUrl(url) ? url : undefined,
    title: event.title,
    // 本は文庫か漫画か分からないので、文字の表紙に媒体名は出さない
    mediaType: event.kind === 'anime' ? 'anime' : undefined,
  };
}

export function eventHref(event: Pick<CatalogEvent, 'clusterId'>): string {
  return `#/catalog/${encodeURIComponent(event.clusterId)}`;
}

// ---------------------------------------------------------------------------
// 取得
// ---------------------------------------------------------------------------

/** 応答の形が契約と違う（items が無いなど）。 */
export class EventsShapeError extends Error {
  constructor() {
    super('unexpected /api/catalog/events response');
  }
}

const KINDS = new Set<string>(['anime', 'book']);
const TYPES = new Set<string>(['episode', 'season-start', 'movie', 'release']);
const TIME_RE = /^\d{1,2}:\d{2}$/;

function isString(value: unknown): value is string {
  return typeof value === 'string';
}

/** 応答を確かめる。形の崩れた行は落とし（画面を壊さない）、items が無ければ EventsShapeError。 */
export function normalizeEventPage(raw: unknown): CatalogEventPage {
  const body = raw as { items?: unknown; truncated?: unknown } | null;
  if (!body || !Array.isArray(body.items)) throw new EventsShapeError();
  const items: CatalogEvent[] = [];
  for (const value of body.items) {
    const item = value as Partial<Record<keyof CatalogEvent, unknown>> | null;
    if (!item || !isString(item.clusterId) || !isString(item.title) || !isString(item.date)) continue;
    if (!isValidYmd(item.date) || !isString(item.kind) || !KINDS.has(item.kind)) continue;
    if (!isString(item.event) || !TYPES.has(item.event)) continue;
    items.push({
      clusterId: item.clusterId,
      entryId: isString(item.entryId) ? item.entryId : '',
      title: item.title,
      kind: item.kind as CatalogEventKind,
      event: item.event as CatalogEventType,
      date: item.date,
      time: isString(item.time) && TIME_RE.test(item.time) ? item.time : null,
      label: isString(item.label) ? item.label : '',
      cover: isString(item.cover) ? item.cover : null,
      popularity: typeof item.popularity === 'number' ? item.popularity : null,
    });
  }
  return { items, truncated: body.truncated === true };
}

/** 1つの期間で取り直す回数の上限（1回 600 件。月の本が多い時の取りこぼしを防ぐ）。 */
export const MAX_EVENT_PAGES = 8;

/**
 * 期間の出来事をすべて取る。サーバーは date 昇順→人気順に 600 件で切るので、切れたら最後の日から取り直してつなぐ
 * （最後の日は途中で切れているため、その日を頭から取り直して重複を除く）。1日だけで上限を超えた日は切れたまま次の日へ。
 */
export async function fetchEventsRange(
  fetcher: (q: CatalogEventQuery) => Promise<unknown>,
  query: CatalogEventQuery,
  maxPages = MAX_EVENT_PAGES,
): Promise<CatalogEventPage> {
  const seen = new Set<string>();
  const items: CatalogEvent[] = [];
  let truncated = false;
  let from = query.from;

  for (let page = 0; page < maxPages; page++) {
    const result = normalizeEventPage(await fetcher({ ...query, from }));
    for (const item of result.items) {
      const key = `${item.clusterId}|${item.entryId}|${item.date}|${item.event}|${item.time ?? ''}|${item.label}`;
      if (seen.has(key)) continue;
      seen.add(key);
      items.push(item);
    }
    if (!result.truncated) return { items, truncated };

    const last = result.items[result.items.length - 1]?.date;
    if (!last || last < from) return { items, truncated: true };
    let next = last;
    if (last === from) {
      truncated = true;
      next = addDaysYmd(last, 1);
    }
    if (next > query.to) return { items, truncated };
    from = next;
  }
  return { items, truncated: true };
}

/**
 * 表示中の期間の出来事。月はアニメと本を別々に取って合わせる（1か月分を1回で取ると本だけで上限に掛かるため）。
 * 合わせた後は date 昇順→人気順（日の中の時刻順は groupEventsByDate で付ける）。
 */
export async function loadOverlayEvents(
  fetcher: (q: CatalogEventQuery) => Promise<unknown>,
  range: { from: string; to: string },
  kinds: readonly CatalogEventKind[],
  splitKinds: boolean,
): Promise<CatalogEventPage> {
  if (kinds.length === 0) return { items: [], truncated: false };
  if (!splitKinds || kinds.length === 1) return fetchEventsRange(fetcher, { ...range, kinds: [...kinds] });

  const pages = await Promise.all(kinds.map((kind) => fetchEventsRange(fetcher, { ...range, kinds: [kind] })));
  const items = pages
    .flatMap((page) => page.items)
    .map((item, index) => ({ item, index }))
    .sort((a, b) => {
      if (a.item.date !== b.item.date) return a.item.date < b.item.date ? -1 : 1;
      return (b.item.popularity ?? -1) - (a.item.popularity ?? -1) || a.index - b.index;
    })
    .map(({ item }) => item);
  return { items, truncated: pages.some((page) => page.truncated) };
}

/** 重ね表示を出せない理由（トグルの横に小さく出す）。 */
export function describeOverlayError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 404) return err.detail === DETAIL_NOT_BUILT ? 'カタログ未作成' : 'collector が未対応';
    return `取得できませんでした（${err.status}）`;
  }
  if (err instanceof EventsShapeError || err instanceof SyntaxError) return '応答を読めませんでした';
  return 'collector に接続できません';
}

// ---------------------------------------------------------------------------
// トグルの横の一言
// ---------------------------------------------------------------------------

export type OverlayStatus =
  | { status: 'off' }
  | { status: 'no-kinds' }
  | { status: 'loading' }
  | { status: 'error'; reason: string }
  | { status: 'ready'; count: number; truncated: boolean };

/** トグルの横に小さく出す一言。出すものが無ければ null。 */
export function overlayNote(state: OverlayStatus): string | null {
  switch (state.status) {
    case 'off':
      return null;
    case 'no-kinds':
      return 'Web はカタログの対象外';
    case 'loading':
      return '読み込み中…';
    case 'error':
      return state.reason;
    case 'ready':
      if (state.truncated) return `${state.count} 件（多いため一部）`;
      return state.count > 0 ? `${state.count} 件` : 'この期間は無し';
  }
}
