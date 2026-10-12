// Detection Engine（仕様は specs/data-coverage/design.md「F2」/ requirements.md US-103）。
// 経路: sequel（追跡中アニメの SEQUEL）→ relation（原作の ADAPTATION）→ どちらも ID が無い Work だけ title。

import { apiGet } from '../api';
import { db as defaultDB, type TriptychDB } from '../db/schema';
import type {
  AiringStatus,
  DatePrecision,
  DetectionCandidate,
  DetectionEvent,
  Edition,
  MediaType,
  Work,
} from '../db/schema';
import {
  createDetectionEvent,
  createEdition,
  createRelease,
  listDetectionEventsByWork,
  listEditions,
  listWorks,
  updateDetectionEventState,
  updateEditionSyncHints,
} from '../db/store';
import { isValidYmd } from '../dates';

// ---------------------------------------------------------------------------
// Collector DTO（backend/dto.py の写し）
// ---------------------------------------------------------------------------

interface DetectionCandidateDTO {
  source: 'anilist';
  externalId: string;
  title: string;
  airingStatus: AiringStatus;
  format?: string | null;
  startDate?: string | null;
  startPrecision?: DatePrecision | null;
  matchScore: number;
  via?: 'relation' | 'sequel' | 'title';
}

interface RelationEdgeDTO {
  relationType: string;
  externalId: string;
  type: 'ANIME' | 'MANGA';
  format?: string | null;
  title: string;
  status?: AiringStatus | null;
  startDate?: string | null;
  startPrecision?: DatePrecision | null;
  isAdult?: boolean;
}

interface RelationsDTO {
  externalId: string;
  type: 'ANIME' | 'MANGA';
  format?: string | null;
  title: string;
  relations: RelationEdgeDTO[];
}

interface SearchResultDTO {
  source: string;
  externalId: string;
  mediaType: MediaType;
  title: string;
  url: string;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** title 経路で一致とみなす matchScore の下限。 */
export const DETECTION_MATCH_THRESHOLD = 0.85;

const DETECTION_META_KEY = 'detectionThrottle';
const DETECTION_THROTTLE_MS = 24 * 60 * 60 * 1000; // 24h
const TRACKED_STATUSES: Edition['status'][] = ['plan', 'active', 'paused'];
const ORIGINAL_MEDIA_TYPES: MediaType[] = ['web-novel', 'light-novel', 'manga'];
const SEQUEL_MAX_DEPTH = 3;
const ADAPTATION_RELATION_TYPES = new Set(['ADAPTATION']);
const EXCLUDED_FORMATS = new Set(['MUSIC']);
const ENDED_STATUSES = new Set<AiringStatus>(['FINISHED', 'CANCELLED']);

type DetectionVia = NonNullable<DetectionEvent['via']>;

interface DetectionThrottleMeta {
  [workId: string]: string; // ISO 8601: 照会が全部成功した直近の時刻
}

// ---------------------------------------------------------------------------
// runDetection
// ---------------------------------------------------------------------------

export interface DetectionOptions {
  /** 24h スロットルを無視して実行する。 */
  force?: boolean;
  /** collector のキャッシュを使わない（`?refresh=1`）。 */
  refresh?: boolean;
  database?: TriptychDB;
  /** テスト用の時刻注入フック。省略時は `new Date()`。 */
  now?: () => Date;
}

export interface DetectionRunResult {
  /** 照会した Work 数（スロットル通過分のみ）。 */
  checked: number;
  /** 新規に作成された DetectionEvent。 */
  created: DetectionEvent[];
  /** Work id → エラー文（照会に失敗した Work。スロットルは記録しない）。 */
  errors: Record<string, string>;
}

interface DetectionContext {
  database: TriptychDB;
  refresh: boolean;
  /** どの Work でも sourceRefs に入っている AniList id。 */
  registered: Set<string>;
  relations: Map<string, Promise<RelationsDTO>>;
  resolved: Map<string, Promise<SearchResultDTO[]>>;
}

export async function runDetection(opts: DetectionOptions = {}): Promise<DetectionRunResult> {
  const database = opts.database ?? defaultDB;
  const now = opts.now ?? (() => new Date());
  const force = opts.force ?? false;

  const throttleEntry = await database.meta.get(DETECTION_META_KEY);
  const throttle: DetectionThrottleMeta =
    (throttleEntry?.value as DetectionThrottleMeta | undefined) ?? {};

  const editions = await listEditions(database);
  const ctx: DetectionContext = {
    database,
    refresh: opts.refresh ?? false,
    registered: new Set(
      editions.flatMap((e) =>
        e.sourceRefs.filter((r) => r.source === 'anilist').map((r) => r.externalId),
      ),
    ),
    relations: new Map(),
    resolved: new Map(),
  };

  const trackedByWork = new Map<string, Edition[]>();
  for (const edition of editions) {
    if (!TRACKED_STATUSES.includes(edition.status)) continue;
    const list = trackedByWork.get(edition.workId) ?? [];
    list.push(edition);
    trackedByWork.set(edition.workId, list);
  }

  const result: DetectionRunResult = { checked: 0, created: [], errors: {} };
  const nextThrottle: DetectionThrottleMeta = { ...throttle };

  for (const work of await listWorks(database)) {
    const tracked = trackedByWork.get(work.id) ?? [];
    const animeIds = tracked
      .filter((e) => e.mediaType === 'anime')
      .flatMap((e) => e.sourceRefs.filter((r) => r.source === 'anilist').map((r) => r.externalId));
    const originals = tracked.filter((e) => ORIGINAL_MEDIA_TYPES.includes(e.mediaType));
    if (animeIds.length === 0 && originals.length === 0) continue;

    if (!force) {
      const last = throttle[work.id];
      if (last && now().getTime() - new Date(last).getTime() < DETECTION_THROTTLE_MS) continue;
    }

    result.checked += 1;
    const outcome = await detectForWork(work, unique(animeIds), originals, ctx);
    result.created.push(...outcome.created);
    if (outcome.failures.length > 0) {
      result.errors[work.id] = `${work.title}: ${outcome.failures[0]}`;
    } else {
      nextThrottle[work.id] = now().toISOString();
    }
  }

  await database.meta.put({ key: DETECTION_META_KEY, value: nextThrottle });
  return result;
}

interface Found {
  candidate: DetectionCandidate;
  matchScore: number;
  via: DetectionVia;
}

async function detectForWork(
  work: Work,
  animeIds: string[],
  originals: Edition[],
  ctx: DetectionContext,
): Promise<{ created: DetectionEvent[]; failures: string[] }> {
  const found = new Map<string, Found>();
  const failures: string[] = [];
  let idResolved = animeIds.length > 0;

  const add = (candidate: DetectionCandidate, matchScore: number, via: DetectionVia) => {
    if (ctx.registered.has(candidate.externalId)) return;
    if (candidate.format && EXCLUDED_FORMATS.has(candidate.format)) return;
    // 検知は「発表を知る」ためのもの。放送済み・中止の期は提案しない（続編の探索自体は通り抜ける）
    if (ENDED_STATUSES.has(candidate.airingStatus)) return;
    if (!found.has(candidate.externalId)) found.set(candidate.externalId, { candidate, matchScore, via });
  };
  const attempt = async (fn: () => Promise<void>) => {
    try {
      await fn();
    } catch (err) {
      failures.push(errorMessage(err));
    }
  };

  for (const animeId of animeIds) {
    await attempt(() => followSequels(animeId, ctx, (edge) => add(edgeCandidate(edge), 1, 'sequel')));
  }

  for (const edition of originals) {
    await attempt(async () => {
      const sourceId = await resolveSourceId(edition, work, ctx);
      if (!sourceId) return;
      idResolved = true;
      const relations = await fetchRelations(sourceId, ctx);
      for (const edge of relations.relations) {
        if (edge.type !== 'ANIME' || !ADAPTATION_RELATION_TYPES.has(edge.relationType)) continue;
        add(edgeCandidate(edge), 1, 'relation');
      }
    });
  }

  // 照会に失敗した Work は次回に回す（title の弱い一致で先に埋めない）。
  if (!idResolved && originals.length > 0 && failures.length === 0) {
    await attempt(async () => {
      const candidates = await apiGet<DetectionCandidateDTO[]>(
        '/api/anilist/detect',
        queryParams(ctx, { title: work.title }),
      );
      for (const c of candidates) {
        if (c.matchScore < DETECTION_MATCH_THRESHOLD) continue;
        add(
          compact({
            source: 'anilist',
            externalId: c.externalId,
            title: c.title,
            airingStatus: c.airingStatus,
            format: c.format ?? undefined,
            startDate: c.startDate ?? undefined,
            startPrecision: c.startPrecision ?? undefined,
          }),
          c.matchScore,
          'title',
        );
      }
    });
  }

  const existing = new Set(
    (await listDetectionEventsByWork(work.id, ctx.database)).map((ev) => ev.candidate.externalId),
  );
  const created: DetectionEvent[] = [];
  for (const { candidate, matchScore, via } of found.values()) {
    if (existing.has(candidate.externalId)) continue;
    created.push(await createEvent(work.id, candidate, matchScore, via, ctx.database));
  }
  return { created, failures };
}

/** SEQUEL（type=ANIME）を最大3段まで辿る。登録済みの期も通り抜けて先を探す。 */
async function followSequels(
  startId: string,
  ctx: DetectionContext,
  onSequel: (edge: RelationEdgeDTO) => void,
): Promise<void> {
  const visited = new Set([startId]);
  let frontier = [startId];
  for (let depth = 0; depth < SEQUEL_MAX_DEPTH && frontier.length > 0; depth++) {
    const next: string[] = [];
    for (const id of frontier) {
      const relations = await fetchRelations(id, ctx);
      for (const edge of relations.relations) {
        if (edge.relationType !== 'SEQUEL' || edge.type !== 'ANIME') continue;
        if (edge.format && EXCLUDED_FORMATS.has(edge.format)) continue;
        if (visited.has(edge.externalId)) continue;
        visited.add(edge.externalId);
        onSequel(edge);
        next.push(edge.externalId);
      }
    }
    frontier = next;
  }
}

/** 原作の AniList NOVEL/MANGA id: sourceRefs → syncHints.anilistSourceId → resolve-source の先頭（保存する）。 */
async function resolveSourceId(
  edition: Edition,
  work: Work,
  ctx: DetectionContext,
): Promise<string | undefined> {
  const ref = edition.sourceRefs.find((r) => r.source === 'anilist');
  if (ref) return ref.externalId;
  if (edition.syncHints?.anilistSourceId) return edition.syncHints.anilistSourceId;

  const kind = edition.mediaType === 'manga' ? 'manga' : 'novel';
  const key = `${kind}\u0000${work.title}`;
  let pending = ctx.resolved.get(key);
  if (!pending) {
    pending = apiGet<SearchResultDTO[]>(
      '/api/anilist/resolve-source',
      queryParams(ctx, { title: work.title, kind }),
    );
    ctx.resolved.set(key, pending);
  }
  const id = (await pending)[0]?.externalId;
  if (id) await updateEditionSyncHints(edition.id, { anilistSourceId: id }, ctx.database);
  return id;
}

function fetchRelations(id: string, ctx: DetectionContext): Promise<RelationsDTO> {
  let pending = ctx.relations.get(id);
  if (!pending) {
    pending = apiGet<RelationsDTO>(
      `/api/anilist/relations/${encodeURIComponent(id)}`,
      queryParams(ctx),
    );
    ctx.relations.set(id, pending);
  }
  return pending;
}

function edgeCandidate(edge: RelationEdgeDTO): DetectionCandidate {
  return compact({
    source: 'anilist',
    externalId: edge.externalId,
    title: edge.title,
    airingStatus: edge.status ?? 'NOT_YET_RELEASED',
    format: edge.format ?? undefined,
    startDate: edge.startDate ?? undefined,
    startPrecision: edge.startPrecision ?? undefined,
  });
}

async function createEvent(
  workId: string,
  candidate: DetectionCandidate,
  matchScore: number,
  via: DetectionVia,
  database: TriptychDB,
): Promise<DetectionEvent> {
  // createDetectionEvent は via を受け取らないので、同じトランザクションで補う。
  return database.transaction('rw', database.detections, async () => {
    const event = await createDetectionEvent({ workId, candidate, matchScore }, database);
    await database.detections.update(event.id, { via });
    return { ...event, via };
  });
}

function queryParams(
  ctx: DetectionContext,
  base: Record<string, string> = {},
): Record<string, string> | undefined {
  const params = { ...base };
  if (ctx.refresh) params.refresh = '1';
  return Object.keys(params).length > 0 ? params : undefined;
}

/** undefined のキーを落とす（IndexedDB に空のキーを残さない）。 */
function compact<T extends object>(value: T): T {
  return Object.fromEntries(Object.entries(value).filter(([, v]) => v !== undefined)) as T;
}

function unique(values: string[]): string[] {
  return Array.from(new Set(values));
}

function errorMessage(err: unknown): string {
  if (err instanceof Error) return err.message;
  return String(err);
}

// ---------------------------------------------------------------------------
// accept / reject
// ---------------------------------------------------------------------------

export interface AcceptDetectionResult {
  event: DetectionEvent;
  edition: Edition;
}

/** 検知を承認してアニメ Edition を作る。season-start は本人が承認したものなので新着にしない。 */
export async function acceptDetection(
  id: string,
  database: TriptychDB = defaultDB,
): Promise<AcceptDetectionResult> {
  const event = await getDetectionEventOrThrow(id, database);
  const { candidate } = event;

  const edition = await createEdition(
    {
      workId: event.workId,
      mediaType: 'anime',
      title: candidate.title,
      sourceRefs: [
        {
          source: 'anilist',
          externalId: candidate.externalId,
          url: `https://anilist.co/anime/${candidate.externalId}`,
        },
      ],
      status: 'plan',
      linkSource: 'auto',
    },
    database,
  );

  if (candidate.startDate && isValidYmd(candidate.startDate)) {
    await createRelease(
      {
        editionId: edition.id,
        kind: 'season-start',
        label: candidate.title,
        date: candidate.startDate,
        precision: candidate.startPrecision ?? 'day',
        externalId: candidate.externalId,
        source: 'anilist',
        seenAt: new Date().toISOString(),
      },
      database,
    );
  }

  const updated = await updateDetectionEventState(id, 'accepted', database);
  return { event: updated, edition };
}

/** 検知を却下する。rejected は残り続け、以降の再検知から除外される。 */
export async function rejectDetection(
  id: string,
  database: TriptychDB = defaultDB,
): Promise<DetectionEvent> {
  await getDetectionEventOrThrow(id, database);
  return updateDetectionEventState(id, 'rejected', database);
}

async function getDetectionEventOrThrow(id: string, database: TriptychDB): Promise<DetectionEvent> {
  const event = await database.detections.get(id);
  if (!event) throw new Error(`DetectionEvent not found: ${id}`);
  return event;
}
