// Sync Engine（仕様は specs/data-coverage/design.md「F2」）。
// status.svelte.ts は import しない（vitest は svelte プラグインを通さず `$state` が未定義になる）。

import { ApiError, apiGet } from '../api';
import { isSafeHttpUrl } from '../components/release-view';
import { db as defaultDB, type TriptychDB } from '../db/schema';
import type {
  AiringStatus,
  BroadcastHint,
  DatePrecision,
  Edition,
  Release,
  Source,
  SourceRef,
  Work,
} from '../db/schema';
import {
  createRelease,
  deleteRelease,
  getEdition,
  listEditions,
  listReleasesByEdition,
  listWorks,
  mergeRelease,
  setEditionCompleted,
  updateEdition,
  updateEditionProgress,
  updateEditionSyncHints,
  updateRelease,
  type EditionSyncHintsPatch,
  type ReleaseMergeInput,
} from '../db/store';
import { isPastRelease, isValidYmd, todayJst } from '../dates';
import { preferences } from '../preferences';
import { runDetection } from './detect';

// ---------------------------------------------------------------------------
// Collector DTO（backend/dto.py の写し。型生成の仕組みは無いのでここに置く）
// ---------------------------------------------------------------------------

interface ReleaseDTO {
  kind: Release['kind'];
  label: string;
  date: string;
  precision?: DatePrecision;
  time?: string | null;
  source: Source;
  externalId?: string | null;
}

interface MediaDetailDTO {
  source: 'anilist';
  externalId: string;
  mediaType: string;
  title: string;
  cover?: string | null;
  url: string;
  status?: AiringStatus | null;
  releases: ReleaseDTO[];
}

interface NdlSeriesDTO {
  lines: Array<{ key: string; label: string; count: number }>;
  line: string;
  releases: ReleaseDTO[];
}

/** しょぼかるの番組1枠（日付は朝5時区切り、時刻は '25:30' 表記）。 */
interface BroadcastDTO {
  channel: string;
  isStreaming: boolean;
  date: string;
  time: string;
  episodeCount?: number | null;
}

interface SyoboiTitleDTO {
  status: 'ok' | 'queued' | 'not_found';
  tid?: string | null;
  title?: string | null;
  /** 今日以降の番組（collector が日時順に最大 8 件）。古い collector には無い。 */
  programs?: BroadcastDTO[] | null;
  streamStarts?: ReleaseDTO[];
  /** streamStarts のうち、直近にも配信があった（配信中の）externalId。古い collector には無い。 */
  startedStreams?: string[] | null;
}

interface WebNovelDTO {
  source: 'narou' | 'kakuyomu';
  externalId: string;
  title: string;
  author?: string | null;
  url: string;
  episodeCount: number;
  completed: boolean;
  isShortStory?: boolean;
  lastUpdate?: string | null;
}

// ---------------------------------------------------------------------------
// Sync status / options
// ---------------------------------------------------------------------------

const SYNC_META_KEY = 'sync';
const THROTTLE_MS = 60 * 60 * 1000; // 1h
const TRACKED_STATUSES: Edition['status'][] = ['plan', 'active', 'paused'];
const ENDED_AIRING = new Set<AiringStatus>(['FINISHED', 'CANCELLED']);
/** Edition に AniList id が複数ある時は、いちばん動いている状態をその Edition の状態にする。 */
const AIRING_PRIORITY: AiringStatus[] = [
  'RELEASING',
  'NOT_YET_RELEASED',
  'HIATUS',
  'FINISHED',
  'CANCELLED',
];

export interface SyncSourceError {
  message: string;
  occurredAt: string; // ISO 8601
}

/** ソース名、または検知（'detect'）ごとの直近エラー。 */
export type SyncErrorKey = Source | 'detect';
export type SyncErrors = Partial<Record<SyncErrorKey, SyncSourceError>>;

interface SyncMeta {
  lastSyncAt?: string;
  lastSyncErrors?: SyncErrors;
}

export interface SyncOptions {
  /** 1h スロットルを無視して実行する（手動同期）。collector のキャッシュも `?refresh=1` で使わせない。 */
  force?: boolean;
  database?: TriptychDB;
  /** テスト用の時刻注入フック。省略時は `new Date()`。 */
  now?: () => Date;
  /** ソースごとの ON/OFF。OFF のソースは照会せず、エラーにもしない。省略したソースは設定に従う。 */
  sources?: Partial<Record<SyncSource, boolean>>;
  /** 同期のあとにアニメ化検知を走らせるか。省略時は設定に従う。 */
  detect?: boolean;
}

export interface SyncRunResult {
  ran: boolean;
  skipped?: 'throttled';
  errors: SyncErrors;
  /** 実行後に meta に残っている最終同期時刻（全ソース失敗なら前回のまま）。 */
  lastSyncAt?: string;
}

// ---------------------------------------------------------------------------
// runSync
// ---------------------------------------------------------------------------

const inflight = new WeakMap<TriptychDB, Promise<SyncRunResult>>();

/** 実行中の同期があれば、新しい呼び出しはその Promise をそのまま返す（多重実行防止）。 */
export function runSync(opts: SyncOptions = {}): Promise<SyncRunResult> {
  const database = opts.database ?? defaultDB;
  const running = inflight.get(database);
  if (running) return running;
  const promise = runSyncOnce({ ...opts, database }).finally(() => {
    inflight.delete(database);
  });
  inflight.set(database, promise);
  return promise;
}

/** App 起動時フックから呼ぶ薄いラッパー（1h スロットル付き）。 */
export async function maybeAutoSync(opts: SyncOptions = {}): Promise<SyncRunResult> {
  return runSync({ ...opts, force: opts.force ?? false });
}

interface SyncContext {
  database: TriptychDB;
  force: boolean;
  now: () => Date;
  /** この同期で取れた AniList の放送状態（キーは airingKey）。 */
  airing: Map<string, AiringStatus>;
}

type SyncSource = 'anilist' | 'syoboi' | 'ndl' | 'narou' | 'kakuyomu';

interface SyncTask {
  label: string;
  run: (ctx: SyncContext) => Promise<void>;
}

async function runSyncOnce(opts: SyncOptions & { database: TriptychDB }): Promise<SyncRunResult> {
  const { database } = opts;
  const now = opts.now ?? (() => new Date());
  const ctx: SyncContext = { database, force: opts.force ?? false, now, airing: new Map() };

  const metaEntry = await database.meta.get(SYNC_META_KEY);
  const meta: SyncMeta = (metaEntry?.value as SyncMeta | undefined) ?? {};

  if (!ctx.force && meta.lastSyncAt) {
    const elapsed = now().getTime() - new Date(meta.lastSyncAt).getTime();
    if (elapsed < THROTTLE_MS) {
      return {
        ran: false,
        skipped: 'throttled',
        errors: meta.lastSyncErrors ?? {},
        lastSyncAt: meta.lastSyncAt,
      };
    }
  }

  const prefs = preferences.all();
  const sources = { ...prefs.syncSources, ...opts.sources };
  const editions = (await listEditions(database)).filter((e) => TRACKED_STATUSES.includes(e.status));
  const works = new Map((await listWorks(database)).map((w) => [w.id, w]));
  const plan = planTasks(editions, works, (source) => sources[source] !== false);

  const errors: SyncErrors = {};
  let succeeded = 0;
  let failed = 0;

  const runLane = async (source: SyncSource) => {
    for (const task of plan.get(source) ?? []) {
      try {
        await task.run(ctx);
        succeeded += 1;
      } catch (err) {
        failed += 1;
        errors[source] = {
          message: `『${task.label}』: ${errorMessage(err)}`,
          occurredAt: now().toISOString(),
        };
      }
    }
  };

  // しょぼかるは同じ同期で取った AniList の放送状態を使うので、AniList のあとに回す。
  const others = Array.from(plan.keys()).filter((s) => s !== 'anilist' && s !== 'syoboi');
  await Promise.all([runLane('anilist').then(() => runLane('syoboi')), ...others.map(runLane)]);

  const syncedAt = now().toISOString();

  // 検知の 24h スロットルは force と独立。検知の失敗で同期結果を壊さない。
  if (opts.detect ?? prefs.detection) {
    try {
      const detection = await runDetection({ database, now: opts.now, refresh: ctx.force });
      const messages = Object.values(detection.errors);
      if (messages.length > 0) {
        errors.detect = {
          message: messages.length === 1 ? messages[0] : `${messages[0]} ほか${messages.length - 1}件`,
          occurredAt: now().toISOString(),
        };
      }
    } catch (err) {
      errors.detect = { message: errorMessage(err), occurredAt: now().toISOString() };
    }
  }

  const allFailed = succeeded === 0 && failed > 0;
  const lastSyncAt = allFailed ? meta.lastSyncAt : syncedAt;
  const nextMeta: SyncMeta = { lastSyncAt, lastSyncErrors: errors };
  await database.meta.put({ key: SYNC_META_KEY, value: nextMeta });

  return { ran: true, errors, lastSyncAt };
}

/** mediaType 別に、ソースごとのタスク列を組み立てる。OFF のソース（enabled が false）は積まない。 */
function planTasks(
  editions: Edition[],
  works: Map<string, Work>,
  enabled: (source: SyncSource) => boolean,
): Map<SyncSource, SyncTask[]> {
  const plan = new Map<SyncSource, SyncTask[]>();
  const push = (source: SyncSource, task: SyncTask) => {
    if (!enabled(source)) return;
    const list = plan.get(source) ?? [];
    list.push(task);
    plan.set(source, list);
  };

  for (const edition of editions) {
    const work = works.get(edition.workId);
    switch (edition.mediaType) {
      case 'anime': {
        for (const ref of edition.sourceRefs) {
          if (ref.source !== 'anilist') continue;
          push('anilist', {
            label: edition.title,
            run: (ctx) => syncAnimeMedia(edition, ref, ctx),
          });
        }
        push('syoboi', { label: edition.title, run: (ctx) => syncSyoboi(edition, work, ctx) });
        break;
      }
      case 'light-novel':
      case 'manga': {
        // AniList の sourceRef は検知用の ID としてだけ使い、Release は作らない。
        push('ndl', { label: edition.title, run: (ctx) => syncPaperBooks(edition, work, ctx) });
        break;
      }
      case 'web-novel': {
        let primary = true;
        for (const ref of edition.sourceRefs) {
          if (ref.source !== 'narou' && ref.source !== 'kakuyomu') continue;
          // OFF のソースは先頭にしない（ON のソースで進捗と完結を決める）
          if (!enabled(ref.source)) continue;
          const isPrimary = primary;
          primary = false;
          push(ref.source, {
            label: edition.title,
            run: (ctx) => syncWebNovel(edition, ref, isPrimary, ctx),
          });
        }
        break;
      }
    }
  }
  return plan;
}

// ---------------------------------------------------------------------------
// anime: AniList
// ---------------------------------------------------------------------------

/** 次話は AniList 1件ごとにローリング1件。毎週出るので新着にしない。 */
async function syncAnimeMedia(edition: Edition, ref: SourceRef, ctx: SyncContext): Promise<void> {
  const dto = await apiGet<MediaDetailDTO>(
    `/api/anilist/media/${encodeURIComponent(ref.externalId)}`,
    queryParams(ctx),
  );
  if (dto.status) ctx.airing.set(airingKey(edition.id, ref.externalId), dto.status);

  let nextEpisode: ReleaseDTO | undefined;
  for (const release of dto.releases) {
    if (release.kind === 'episode') {
      nextEpisode ??= release;
      continue;
    }
    await skipInvalidDate(() =>
      mergeRelease(toMergeInput(edition.id, release, 'anilist'), ctx.database, { now: ctx.now() }),
    );
  }

  await rollNextEpisode(edition, ref, nextEpisode, ctx);

  // 表紙は先頭の AniList id のものだけを使う（id が複数ある Edition で、同期のたびに入れ替わらないように）。
  if (edition.sourceRefs.find((r) => r.source === 'anilist')?.externalId === ref.externalId) {
    await syncCover(edition.id, dto.cover, ctx);
  }
}

/**
 * AniList の表紙を Edition に入れる。更新するのは、表紙が空か、前の同期が入れた表紙（coverFrom が anilist）の時だけ。
 * 手で設定した表紙（coverFrom が無いまま coverUrl がある）やカタログから借りた表紙には触らない。
 */
async function syncCover(editionId: string, cover: string | null | undefined, ctx: SyncContext): Promise<void> {
  const url = cover?.trim();
  if (!url || !isSafeHttpUrl(url)) return;
  const { database } = ctx;
  // 判定と書き込みを同じトランザクションにして、同期の最中に手で変えた表紙を上書きしない。
  await database.transaction('rw', database.editions, async () => {
    const current = await getEdition(editionId, database);
    if (!current) return;
    if (current.coverUrl?.trim() && current.syncHints?.coverFrom !== 'anilist') return;
    if (current.coverUrl !== url) await updateEdition(editionId, { coverUrl: url }, database);
    await updateEditionSyncHints(editionId, { coverFrom: 'anilist' }, database); // 同じ値なら書かれない
  });
}

async function rollNextEpisode(
  edition: Edition,
  ref: SourceRef,
  next: ReleaseDTO | undefined,
  ctx: SyncContext,
): Promise<void> {
  const { database } = ctx;
  const nextId = next ? (next.externalId ?? `${ref.externalId}:${next.label}`) : undefined;
  const anilistIds = new Set(
    edition.sourceRefs.filter((r) => r.source === 'anilist').map((r) => r.externalId),
  );

  const rows = (await listReleasesByEdition(edition.id, database)).filter(
    (r) => r.kind === 'episode' && r.origin !== 'manual',
  );
  for (const row of rows) {
    if (nextId !== undefined && row.externalId === nextId) continue;
    // 同じ Edition に紐づく別の AniList の次話は残す。
    const owner = row.externalId ? episodeOwner(row.externalId) : undefined;
    if (owner === undefined || owner === ref.externalId || !anilistIds.has(owner)) {
      await deleteRelease(row.id, database);
    }
  }

  if (!next || nextId === undefined) return;
  const existed = rows.some((r) => r.externalId === nextId);
  await skipInvalidDate(async () => {
    const release = await mergeRelease(
      { ...toMergeInput(edition.id, next, 'anilist'), externalId: nextId },
      database,
      { now: ctx.now() },
    );
    if (!existed && release.origin !== 'manual' && !release.seenAt) {
      await updateRelease(release.id, { seenAt: ctx.now().toISOString() }, database);
    }
  });
}

function episodeOwner(externalId: string): string | undefined {
  const i = externalId.lastIndexOf(':');
  return i > 0 ? externalId.slice(0, i) : undefined;
}

// ---------------------------------------------------------------------------
// anime: しょぼいカレンダー
// ---------------------------------------------------------------------------

async function syncSyoboi(edition: Edition, work: Work | undefined, ctx: SyncContext): Promise<void> {
  const { database } = ctx;
  const airing = editionAiring(edition, ctx);
  // 放送終了・中止の期は配信の予定がもう増えないので、Crawl-delay 60 の枠を使わない。
  if (airing && ENDED_AIRING.has(airing)) return;

  const knownTid =
    edition.syncHints?.syoboiTid ??
    edition.sourceRefs.find((r) => r.source === 'syoboi')?.externalId;
  const title = edition.title || work?.title || '';
  if (!knownTid && !title) return;

  // 手動同期でも refresh は付けない（Crawl-delay 60 のため取り直すと queued になり、その回はデータが入らない。24h キャッシュで十分）
  const dto = await apiGet<SyoboiTitleDTO>('/api/syoboi/title', knownTid ? { tid: knownTid } : { title });
  if (dto.status !== 'ok') return; // queued は次の同期で拾う

  const today = todayJst(ctx.now());
  const hints: EditionSyncHintsPatch = {};
  if (dto.tid && dto.tid !== edition.syncHints?.syoboiTid) hints.syoboiTid = dto.tid;
  // programs の無い古い collector では保存済みの番組を触らない。今日以降が無ければ消す（null）。
  if (dto.programs) hints.broadcasts = upcomingBroadcasts(dto.programs, today);
  // 中身が同じなら updateEditionSyncHints が書かない。
  if (Object.keys(hints).length > 0) await updateEditionSyncHints(edition.id, hints, database);

  const streams = (await listReleasesByEdition(edition.id, database)).filter(
    (r) => r.kind === 'stream-start',
  );
  const rows = streams.filter((r) => r.origin !== 'manual');
  // startedStreams の無い古い collector では、放送中の期の配信はすべて配信中とみなす。
  const started = dto.startedStreams ? new Set(dto.startedStreams) : undefined;
  for (const release of dto.streamStarts ?? []) {
    if (release.kind !== 'stream-start') continue;
    const ongoing =
      rows.some((r) => r.rolling && sameStream(r, release)) ||
      (started ? started.has(release.externalId ?? '') : airing === 'RELEASING');
    if (ongoing) {
      await skipInvalidDate(() => rollStream(edition, release, streams, ctx));
      continue;
    }
    // 始まった配信を、毎週の「今日以降の最初の番組」で延期扱いにしない。
    const existing = release.externalId
      ? rows.find((r) => r.externalId === release.externalId)
      : undefined;
    if (existing && isPastRelease(existing, today)) continue;
    await skipInvalidDate(() =>
      mergeRelease(toMergeInput(edition.id, release, 'syoboi'), database, { now: ctx.now() }),
    );
  }
}

const MAX_BROADCASTS = 8;
const CLOCK_RE = /^(\d{1,2}):(\d{2})$/;

/** '25:30' -> 分（1530）。朝5時区切りなので 24 時以降もそのまま数える。形式が違えば undefined。 */
function clockMinutes(time: string): number | undefined {
  const match = CLOCK_RE.exec(time.trim());
  return match ? Number(match[1]) * 60 + Number(match[2]) : undefined;
}

/**
 * しょぼかるの番組のうち今日（JST）以降を日時順に最大 8 件。
 * 日付・時刻・局名が不正な枠は捨てる。1件も無ければ null（保存済みの番組を消す）。
 */
function upcomingBroadcasts(programs: BroadcastDTO[], today: string): BroadcastHint[] | null {
  const rows: Array<{ minutes: number; hint: BroadcastHint }> = [];
  for (const p of programs) {
    const channel = p.channel?.trim();
    const minutes = typeof p.time === 'string' ? clockMinutes(p.time) : undefined;
    if (!channel || minutes === undefined || !isValidYmd(p.date) || p.date < today) continue;
    const hint: BroadcastHint = { channel, date: p.date, time: p.time.trim(), isStreaming: p.isStreaming === true };
    if (typeof p.episodeCount === 'number' && Number.isInteger(p.episodeCount) && p.episodeCount > 0) {
      hint.episode = p.episodeCount;
    }
    rows.push({ minutes, hint });
  }
  rows.sort((a, b) => a.hint.date.localeCompare(b.hint.date) || a.minutes - b.minutes);
  return rows.length > 0 ? rows.slice(0, MAX_BROADCASTS).map((r) => r.hint) : null;
}

/** 配信中のサービスは「次回の配信」1件のローリング。日付は延期として記録せずに動かし、新着にしない。 */
async function rollStream(
  edition: Edition,
  next: ReleaseDTO,
  streams: Release[],
  ctx: SyncContext,
): Promise<void> {
  const { database } = ctx;
  if (!isValidYmd(next.date)) return;
  if (streams.some((r) => r.origin === 'manual' && sameExternalId(r, next))) return;

  const same = streams.filter((r) => r.origin !== 'manual' && sameStream(r, next));
  const current = same.find((r) => r.rolling);
  // 配信開始として作った行は、延期履歴や新着を持ち越さないよう作り直す。
  for (const row of same) {
    if (row !== current) await deleteRelease(row.id, database);
  }

  const seenAt = ctx.now().toISOString();
  const time = next.time ?? undefined;
  const externalId = next.externalId ?? current?.externalId;
  if (!current) {
    await createRelease(
      {
        editionId: edition.id,
        kind: 'stream-start',
        label: next.label,
        date: next.date,
        precision: 'day',
        time,
        externalId,
        source: next.source ?? 'syoboi',
        seenAt,
        rolling: true,
      },
      database,
    );
    return;
  }
  const changed =
    current.date !== next.date ||
    current.time !== time ||
    current.label !== next.label ||
    current.externalId !== externalId ||
    (current.precision ?? 'day') !== 'day';
  if (!changed && current.seenAt) return;
  await updateRelease(
    current.id,
    {
      label: next.label,
      date: next.date,
      precision: 'day',
      time,
      externalId,
      origin: 'sync',
      seenAt: current.seenAt ?? seenAt,
    },
    database,
  );
}

/** 同じ配信先の行: 同じ externalId か、ローリングで同じラベル（ABEMA は週ごとにチャンネルが変わることがある）。 */
function sameStream(row: Release, release: ReleaseDTO): boolean {
  return sameExternalId(row, release) || (!!row.rolling && row.label === release.label);
}

function sameExternalId(row: Release, release: ReleaseDTO): boolean {
  return !!release.externalId && row.externalId === release.externalId;
}

/** この同期で取れた AniList の放送状態。取れなかった id があれば undefined（分からないので照会する）。 */
function editionAiring(edition: Edition, ctx: SyncContext): AiringStatus | undefined {
  const statuses = edition.sourceRefs
    .filter((r) => r.source === 'anilist')
    .map((r) => ctx.airing.get(airingKey(edition.id, r.externalId)));
  if (statuses.length === 0 || statuses.some((s) => s === undefined)) return undefined;
  return AIRING_PRIORITY.find((s) => statuses.includes(s));
}

function airingKey(editionId: string, anilistId: string): string {
  return `${editionId}\u0000${anilistId}`;
}

// ---------------------------------------------------------------------------
// light-novel / manga: NDL
// ---------------------------------------------------------------------------

async function syncPaperBooks(edition: Edition, work: Work | undefined, ctx: SyncContext): Promise<void> {
  const { database } = ctx;
  const title = work?.title || edition.title;
  const savedLine = edition.syncHints?.ndlLine;
  const line = savedLine ?? ndlRefLine(edition);
  const query: Record<string, string> = {
    title,
    kind: edition.mediaType === 'manga' ? 'manga' : 'novel',
  };
  if (line !== undefined) query.line = line;

  const dto = await apiGet<NdlSeriesDTO>('/api/ndl/series', queryParams(ctx, query));

  if (savedLine === undefined) {
    if (line !== undefined) {
      await updateEditionSyncHints(edition.id, { ndlLine: line }, database);
    } else if (dto.lines.length > 0) {
      // 何も見つからない時に本編へ固定しない。
      await updateEditionSyncHints(edition.id, { ndlLine: dto.line ?? '' }, database);
    }
  }

  // 移行措置: 旧ラベル照合時代の行（externalId の無い同期 volume）を消す。
  for (const row of await listReleasesByEdition(edition.id, database)) {
    if (row.kind === 'volume' && row.origin !== 'manual' && !row.externalId) {
      await deleteRelease(row.id, database);
    }
  }

  for (const release of dto.releases) {
    if (!release.externalId) continue; // ISBN の無い巻は同一性を判定できない
    await skipInvalidDate(() =>
      mergeRelease(toMergeInput(edition.id, release, 'ndl'), database, { now: ctx.now() }),
    );
  }
}

/** 検索で選んだ NDL シリーズ（externalId "<kind>:<line>:<シリーズ名>"）の line。 */
function ndlRefLine(edition: Edition): string | undefined {
  for (const ref of edition.sourceRefs) {
    if (ref.source !== 'ndl') continue;
    const first = ref.externalId.indexOf(':');
    const second = first < 0 ? -1 : ref.externalId.indexOf(':', first + 1);
    if (second < 0) continue;
    return ref.externalId.slice(first + 1, second);
  }
  return undefined;
}

// ---------------------------------------------------------------------------
// web-novel: なろう / カクヨム
// ---------------------------------------------------------------------------

/** progress は total だけ更新する（current と unit は本人のもの）。 */
async function syncWebNovel(
  edition: Edition,
  ref: SourceRef,
  primary: boolean,
  ctx: SyncContext,
): Promise<void> {
  const source = ref.source as 'narou' | 'kakuyomu';
  const path =
    source === 'narou'
      ? `/api/narou/novel/${encodeURIComponent(ref.externalId)}`
      : `/api/kakuyomu/work/${encodeURIComponent(ref.externalId)}`;
  const dto = await apiGet<WebNovelDTO>(path, queryParams(ctx));

  await rollLatestEpisode(edition, ref, source, dto, ctx);

  // 1つの Edition に複数の Web 小説ソースがあっても、進捗と完結は先頭のソースだけで決める。
  if (!primary) return;
  const count = dto.episodeCount;
  if (count > 0) {
    const prev = edition.progress;
    if (!prev) {
      await updateEditionProgress(
        edition.id,
        { unit: source === 'narou' ? 'part' : 'episode', current: 0, total: count },
        ctx.database,
      );
    } else if (prev.total !== count) {
      await updateEditionProgress(edition.id, { ...prev, total: count }, ctx.database);
    }
  }
  await setEditionCompleted(edition.id, dto.completed, ctx.database);
}

const EPISODE_COUNT_RE = /^第(\d+)(?:部分|話)/;

async function rollLatestEpisode(
  edition: Edition,
  ref: SourceRef,
  source: 'narou' | 'kakuyomu',
  dto: WebNovelDTO,
  ctx: SyncContext,
): Promise<void> {
  const { database } = ctx;
  const count = dto.episodeCount;
  const date = dto.lastUpdate;
  if (count <= 0 || !date || !isValidYmd(date)) return; // 旧行を消す前に、作れることを確かめる

  const key = `${source}:${ref.externalId}:latest`;
  const label = source === 'narou' ? `第${count}部分` : `第${count}話`;

  // 今回のキーの行と、externalId の無い旧行（更新ごとに増えていた `第N部分`）。
  const rows = (await listReleasesByEdition(edition.id, database)).filter(
    (r) =>
      r.kind === 'episode' &&
      r.origin !== 'manual' &&
      r.source === source &&
      (r.externalId === key || !r.externalId),
  );

  const create = async (seen: boolean) => {
    await skipInvalidDate(async () => {
      const release = await mergeRelease(
        { editionId: edition.id, kind: 'episode', label, date, precision: 'day', externalId: key, source },
        database,
        { now: ctx.now(), markPastSeen: false },
      );
      if (seen && release.origin !== 'manual' && !release.seenAt) {
        await updateRelease(release.id, { seenAt: ctx.now().toISOString() }, database);
      }
    });
  };

  if (rows.length === 0) {
    await create(true); // 初回は既読で作る
    return;
  }

  const prevCount = maxEpisodeCount(rows) ?? edition.progress?.total;
  if (prevCount !== undefined && count > prevCount) {
    // 話数が増えた: 延期として記録させず、作り直して新着にする。
    for (const row of rows) await deleteRelease(row.id, database);
    await create(false);
    return;
  }

  const current = rows.find((r) => r.externalId === key);
  for (const row of rows) {
    if (row !== current) await deleteRelease(row.id, database);
  }
  if (!current) {
    await create(true);
    return;
  }
  if (current.label !== label || current.date !== date || (current.precision ?? 'day') !== 'day') {
    // 話数が増えていない時の日付・ラベルの変化は延期ではないので、記録せずに直す。
    await updateRelease(current.id, { label, date, precision: 'day', origin: 'sync' }, database);
  }
}

function maxEpisodeCount(rows: Release[]): number | undefined {
  let max: number | undefined;
  for (const row of rows) {
    const match = EPISODE_COUNT_RE.exec(row.label);
    if (!match) continue;
    const n = Number(match[1]);
    if (max === undefined || n > max) max = n;
  }
  return max;
}

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

function queryParams(ctx: SyncContext, base: Record<string, string> = {}): Record<string, string> | undefined {
  const params = { ...base };
  if (ctx.force) params.refresh = '1';
  return Object.keys(params).length > 0 ? params : undefined;
}

function toMergeInput(editionId: string, release: ReleaseDTO, fallbackSource: Source): ReleaseMergeInput {
  const input: ReleaseMergeInput = {
    editionId,
    kind: release.kind,
    label: release.label,
    date: release.date,
    precision: release.precision ?? 'day',
    source: release.source ?? fallbackSource,
  };
  if (release.time) input.time = release.time;
  if (release.externalId) input.externalId = release.externalId;
  return input;
}

/** 1件の Release の日付が不正でも、そのソース全体は止めない。 */
async function skipInvalidDate(fn: () => Promise<unknown>): Promise<void> {
  try {
    await fn();
  } catch (err) {
    if (err instanceof RangeError) return;
    throw err;
  }
}

// collector の状態コードを、Home・設定に出す言葉にする（相手先の多くは一時的な障害）
const API_STATUS_WORDS: Record<number, string> = {
  401: 'トークンが違います',
  403: '断られました',
  404: '見つかりませんでした',
  429: '問い合わせが多すぎます',
  502: '相手先が応答しませんでした',
  503: '相手先が混み合っています',
  504: '相手先の応答が遅すぎました',
};

/** 同期のエラーの理由。collector の状態コードは言葉に、つながらない時（fetch の TypeError）はそう書く。 */
export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    const words =
      API_STATUS_WORDS[err.status] ?? (err.status >= 500 ? '相手先でエラーが起きました' : '受け付けられませんでした');
    return `${words}（${err.status}）`;
  }
  if (err instanceof TypeError) return 'collector に接続できませんでした';
  if (err instanceof Error) return err.message;
  return String(err);
}
