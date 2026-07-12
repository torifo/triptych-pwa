// Detection Engine (FR-005, specs/mvp-core/design.md "Detection Engine" /
// "検知スロットル" / US-004).
//
// runSync 完了後に呼ばれ、追跡中(status ∈ {plan, active, paused})の原作系
// Edition（web-novel/light-novel/manga）を持つ Work について、collector の
// `/api/anilist/detect?title=` を照会してアニメ化の兆候を DetectionEvent
// として記録する。Work 毎に24時間スロットルをかけ（meta.detectionThrottle
// に記録）、追跡数が増えても同期時間とAniListレートを圧迫しないようにする
// （design.md「検知スロットル」）。
//
// 一致は matchScore が DETECTION_MATCH_THRESHOLD 以上のもののみを対象とし、
// 以下は検知結果から除外する:
//   - その Work に同一 externalId のアニメ Edition が既に存在する
//   - 同一 (workId, externalId) の DetectionEvent が既に存在する
//     （rejected は「再検知しない」の永続除外リストとして機能する。加えて
//     pending/accepted も除外することで、同じ候補の重複 DetectionEvent が
//     毎日積み上がるのを防ぐ — design.md の「rejected を除外」は最小要件
//     だが、重複防止のため existing state 全般に広げている）

import { apiGet, ApiError } from '../api';
import { db as defaultDB, type TriptychDB } from '../db/schema';
import type { DetectionEvent, Edition, MediaType, Work } from '../db/schema';
import {
  createDetectionEvent,
  createEdition,
  createRelease,
  listDetectionEventsByWork,
  listEditions,
  listWorks,
  updateDetectionEventState,
} from '../db/store';

// ---------------------------------------------------------------------------
// Collector DTO shape (mirrors backend/dto.py: DetectionCandidateDTO)
// ---------------------------------------------------------------------------

interface DetectionCandidateDTO {
  source: 'anilist';
  externalId: string;
  title: string;
  airingStatus: 'NOT_YET_RELEASED' | 'RELEASING' | 'FINISHED';
  startDate?: string | null;
  matchScore: number;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** 一致とみなす matchScore の下限（design.md には数値の明記はないため MVP 初期値として定数化）。 */
export const DETECTION_MATCH_THRESHOLD = 0.85;

const DETECTION_META_KEY = 'detectionThrottle';
const DETECTION_THROTTLE_MS = 24 * 60 * 60 * 1000; // 24h (design.md「検知スロットル」)
const TRACKED_STATUSES: Edition['status'][] = ['plan', 'active', 'paused'];
const ORIGINAL_MEDIA_TYPES: MediaType[] = ['web-novel', 'light-novel', 'manga'];

interface DetectionThrottleMeta {
  [workId: string]: string; // ISO 8601: 直近の検知実行時刻
}

// ---------------------------------------------------------------------------
// runDetection
// ---------------------------------------------------------------------------

export interface DetectionOptions {
  /** 24h スロットルを無視して強制実行する。 */
  force?: boolean;
  database?: TriptychDB;
  /** テスト用の時刻注入フック。省略時は `new Date()`。 */
  now?: () => Date;
}

export interface DetectionRunResult {
  /** 検知対象になった Work 数（スロットル通過分のみ）。 */
  checked: number;
  /** 新規に作成された DetectionEvent。 */
  created: DetectionEvent[];
  /** Work 単位で発生したエラー（ソース障害等）。同期結果は壊さない。 */
  errors: Record<string, string>;
}

export async function runDetection(opts: DetectionOptions = {}): Promise<DetectionRunResult> {
  const database = opts.database ?? defaultDB;
  const now = opts.now ?? (() => new Date());
  const force = opts.force ?? false;

  const throttleEntry = await database.meta.get(DETECTION_META_KEY);
  const throttle: DetectionThrottleMeta = (throttleEntry?.value as DetectionThrottleMeta | undefined) ?? {};

  const editions = await listEditions(database);
  const originalWorkIds = new Set(
    editions
      .filter((e) => ORIGINAL_MEDIA_TYPES.includes(e.mediaType) && TRACKED_STATUSES.includes(e.status))
      .map((e) => e.workId),
  );

  const works = (await listWorks(database)).filter((w) => originalWorkIds.has(w.id));

  const result: DetectionRunResult = { checked: 0, created: [], errors: {} };
  const nextThrottle: DetectionThrottleMeta = { ...throttle };

  for (const work of works) {
    if (!force) {
      const last = throttle[work.id];
      if (last && now().getTime() - new Date(last).getTime() < DETECTION_THROTTLE_MS) {
        continue;
      }
    }

    result.checked += 1;
    nextThrottle[work.id] = now().toISOString();

    try {
      const created = await detectForWork(work, editions, database);
      result.created.push(...created);
    } catch (err) {
      result.errors[work.id] = err instanceof ApiError ? err.message : errorMessage(err);
    }
  }

  await database.meta.put({ key: DETECTION_META_KEY, value: nextThrottle });

  return result;
}

async function detectForWork(
  work: Work,
  allEditions: Edition[],
  database: TriptychDB,
): Promise<DetectionEvent[]> {
  const candidates = await apiGet<DetectionCandidateDTO[]>('/api/anilist/detect', { title: work.title });

  const workAnimeExternalIds = new Set(
    allEditions
      .filter((e) => e.workId === work.id && e.mediaType === 'anime')
      .flatMap((e) => e.sourceRefs.filter((ref) => ref.source === 'anilist').map((ref) => ref.externalId)),
  );

  const existingEvents = await listDetectionEventsByWork(work.id, database);
  const existingExternalIds = new Set(existingEvents.map((ev) => ev.candidate.externalId));

  const created: DetectionEvent[] = [];

  for (const candidate of candidates) {
    if (candidate.matchScore < DETECTION_MATCH_THRESHOLD) continue;
    if (workAnimeExternalIds.has(candidate.externalId)) continue;
    if (existingExternalIds.has(candidate.externalId)) continue;

    const event = await createDetectionEvent(
      {
        workId: work.id,
        candidate: {
          source: 'anilist',
          externalId: candidate.externalId,
          title: candidate.title,
          airingStatus: candidate.airingStatus,
          startDate: candidate.startDate ?? undefined,
        },
        matchScore: candidate.matchScore,
      },
      database,
    );
    created.push(event);
    existingExternalIds.add(candidate.externalId); // 同一レスポンス内の重複候補も除外
  }

  return created;
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

/**
 * 検知を承認し、アニメ Edition を自動生成する（linkSource: 'auto'）。
 * candidate.startDate があれば kind='season-start' の Release も追加する。
 */
export async function acceptDetection(
  id: string,
  database: TriptychDB = defaultDB,
): Promise<AcceptDetectionResult> {
  const event = await getDetectionEventOrThrow(id, database);

  const edition = await createEdition(
    {
      workId: event.workId,
      mediaType: 'anime',
      title: event.candidate.title,
      sourceRefs: [
        {
          source: 'anilist',
          externalId: event.candidate.externalId,
          url: `https://anilist.co/anime/${event.candidate.externalId}`,
        },
      ],
      status: 'plan',
      linkSource: 'auto',
    },
    database,
  );

  if (event.candidate.startDate) {
    await createRelease(
      {
        editionId: edition.id,
        kind: 'season-start',
        label: event.candidate.title,
        date: event.candidate.startDate,
        source: 'anilist',
      },
      database,
    );
  }

  const updated = await updateDetectionEventState(id, 'accepted', database);
  return { event: updated, edition };
}

/** 検知を却下する。rejected は永続的に保持され、以降の再検知から除外される。 */
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
