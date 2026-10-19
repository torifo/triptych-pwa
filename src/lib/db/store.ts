import { nanoid } from 'nanoid';
import { db as defaultDB, type TriptychDB } from './schema';
import type {
  Work,
  Edition,
  EditionProgress,
  EditionSyncHints,
  SourceRef,
  DatePrecision,
  Release,
  DetectionEvent,
  DetectionCandidate,
  Status,
} from './schema';
import { normalizeTitle } from '../normalize';
import {
  compareYmd,
  isPastRelease,
  isValidYmd,
  precisionEnd,
  precisionStart,
  todayJst,
} from '../dates';

function nowISO(now: Date = new Date()): string {
  return now.toISOString();
}

// ---------------------------------------------------------------------------
// Work
// ---------------------------------------------------------------------------

export interface CreateWorkInput {
  title: string;
  aliases?: string[];
  coverEditionId?: string;
  origin?: Work['origin'];
}

export async function createWork(
  input: CreateWorkInput,
  database: TriptychDB = defaultDB,
): Promise<Work> {
  const ts = nowISO();
  const work: Work = {
    id: nanoid(),
    title: input.title,
    titleNormalized: normalizeTitle(input.title),
    aliases: input.aliases ?? [],
    coverEditionId: input.coverEditionId,
    ...(input.origin ? { origin: input.origin } : {}),
    createdAt: ts,
    updatedAt: ts,
  };
  await database.works.add(work);
  return work;
}

export async function getWork(id: string, database: TriptychDB = defaultDB): Promise<Work | undefined> {
  return database.works.get(id);
}

export async function listWorks(database: TriptychDB = defaultDB): Promise<Work[]> {
  return database.works.toArray();
}

export type UpdateWorkInput = Partial<Pick<Work, 'title' | 'aliases' | 'coverEditionId' | 'origin'>>;

export async function updateWork(
  id: string,
  changes: UpdateWorkInput,
  database: TriptychDB = defaultDB,
): Promise<Work> {
  const existing = await database.works.get(id);
  if (!existing) throw new Error(`Work not found: ${id}`);

  const patch: Partial<Work> = { ...changes, updatedAt: nowISO() };
  if (changes.title !== undefined) {
    patch.titleNormalized = normalizeTitle(changes.title);
  }
  await database.works.update(id, patch);
  return { ...existing, ...patch };
}

export async function deleteWork(id: string, database: TriptychDB = defaultDB): Promise<void> {
  await database.transaction('rw', database.works, database.editions, database.releases, database.detections, async () => {
    const editions = await database.editions.where('workId').equals(id).toArray();
    for (const edition of editions) {
      await database.releases.where('editionId').equals(edition.id).delete();
    }
    await database.editions.where('workId').equals(id).delete();
    await database.detections.where('workId').equals(id).delete();
    await database.works.delete(id);
  });
}

// ---------------------------------------------------------------------------
// Edition
// ---------------------------------------------------------------------------

export interface CreateEditionInput {
  workId: string;
  mediaType: Edition['mediaType'];
  title: string;
  sourceRefs?: Edition['sourceRefs'];
  coverUrl?: string;
  status?: Status;
  progress?: EditionProgress;
  linkSource?: Edition['linkSource'];
  note?: string;
}

export async function createEdition(
  input: CreateEditionInput,
  database: TriptychDB = defaultDB,
): Promise<Edition> {
  const ts = nowISO();
  const edition: Edition = {
    id: nanoid(),
    workId: input.workId,
    mediaType: input.mediaType,
    title: input.title,
    sourceRefs: input.sourceRefs ?? [],
    coverUrl: input.coverUrl,
    status: input.status ?? 'plan',
    progress: input.progress,
    linkSource: input.linkSource ?? 'manual',
    note: input.note,
    createdAt: ts,
    updatedAt: ts,
  };
  await database.editions.add(edition);
  return edition;
}

export async function getEdition(
  id: string,
  database: TriptychDB = defaultDB,
): Promise<Edition | undefined> {
  return database.editions.get(id);
}

export async function listEditions(database: TriptychDB = defaultDB): Promise<Edition[]> {
  return database.editions.toArray();
}

export async function listEditionsByWork(
  workId: string,
  database: TriptychDB = defaultDB,
): Promise<Edition[]> {
  return database.editions.where('workId').equals(workId).toArray();
}

export type UpdateEditionInput = Partial<
  Pick<Edition, 'title' | 'mediaType' | 'sourceRefs' | 'coverUrl' | 'note' | 'linkSource'>
>;

export async function updateEdition(
  id: string,
  changes: UpdateEditionInput,
  database: TriptychDB = defaultDB,
): Promise<Edition> {
  const existing = await database.editions.get(id);
  if (!existing) throw new Error(`Edition not found: ${id}`);
  const patch: Partial<Edition> = { ...changes, updatedAt: nowISO() };
  await database.editions.update(id, patch);
  return { ...existing, ...patch };
}

export async function deleteEdition(id: string, database: TriptychDB = defaultDB): Promise<void> {
  await database.transaction('rw', database.editions, database.releases, async () => {
    await database.releases.where('editionId').equals(id).delete();
    await database.editions.delete(id);
  });
}

/**
 * Edition の workId 付け替え（リンク誤りの修正）。
 * Release は editionId 参照のため自動的に付け替え先へ追従する。
 */
export async function reassignEditionWork(
  editionId: string,
  newWorkId: string,
  database: TriptychDB = defaultDB,
): Promise<Edition> {
  const existing = await database.editions.get(editionId);
  if (!existing) throw new Error(`Edition not found: ${editionId}`);
  const patch: Partial<Edition> = { workId: newWorkId, updatedAt: nowISO() };
  await database.editions.update(editionId, patch);
  return { ...existing, ...patch };
}

export async function updateEditionStatus(
  editionId: string,
  status: Status,
  database: TriptychDB = defaultDB,
): Promise<Edition> {
  const existing = await database.editions.get(editionId);
  if (!existing) throw new Error(`Edition not found: ${editionId}`);
  const patch: Partial<Edition> = { status, updatedAt: nowISO() };
  await database.editions.update(editionId, patch);
  return { ...existing, ...patch };
}

export async function updateEditionProgress(
  editionId: string,
  progress: EditionProgress,
  database: TriptychDB = defaultDB,
): Promise<Edition> {
  const existing = await database.editions.get(editionId);
  if (!existing) throw new Error(`Edition not found: ${editionId}`);
  const patch: Partial<Edition> = { progress, updatedAt: nowISO() };
  await database.editions.update(editionId, patch);
  return { ...existing, ...patch };
}

/** 同じ source+externalId の sourceRef が既にあれば何もしない（重複を作らない）。 */
export async function addSourceRef(
  editionId: string,
  ref: SourceRef,
  database: TriptychDB = defaultDB,
): Promise<Edition> {
  return database.transaction('rw', database.editions, async () => {
    const existing = await database.editions.get(editionId);
    if (!existing) throw new Error(`Edition not found: ${editionId}`);
    if (existing.sourceRefs.some((r) => sameSourceRef(r, ref))) return existing;
    const patch: Partial<Edition> = {
      sourceRefs: [...existing.sourceRefs, ref],
      updatedAt: nowISO(),
    };
    await database.editions.update(editionId, patch);
    return { ...existing, ...patch };
  });
}

export async function removeSourceRef(
  editionId: string,
  ref: Pick<SourceRef, 'source' | 'externalId'>,
  database: TriptychDB = defaultDB,
): Promise<Edition> {
  return database.transaction('rw', database.editions, async () => {
    const existing = await database.editions.get(editionId);
    if (!existing) throw new Error(`Edition not found: ${editionId}`);
    const sourceRefs = existing.sourceRefs.filter((r) => !sameSourceRef(r, ref));
    if (sourceRefs.length === existing.sourceRefs.length) return existing;
    const patch: Partial<Edition> = { sourceRefs, updatedAt: nowISO() };
    await database.editions.update(editionId, patch);
    return { ...existing, ...patch };
  });
}

function sameSourceRef(
  a: Pick<SourceRef, 'source' | 'externalId'>,
  b: Pick<SourceRef, 'source' | 'externalId'>,
): boolean {
  return a.source === b.source && a.externalId === b.externalId;
}

/** null は削除、undefined は変更なし。 */
export type EditionSyncHintsPatch = { [K in keyof EditionSyncHints]?: EditionSyncHints[K] | null };

export async function updateEditionSyncHints(
  editionId: string,
  hints: EditionSyncHintsPatch,
  database: TriptychDB = defaultDB,
): Promise<Edition> {
  return database.transaction('rw', database.editions, async () => {
    const existing = await database.editions.get(editionId);
    if (!existing) throw new Error(`Edition not found: ${editionId}`);
    const next: Record<string, unknown> = { ...existing.syncHints };
    for (const [key, value] of Object.entries(hints)) {
      if (value === null) delete next[key];
      else if (value !== undefined) next[key] = value;
    }
    const syncHints = Object.keys(next).length > 0 ? (next as EditionSyncHints) : undefined;
    if (JSON.stringify(syncHints) === JSON.stringify(existing.syncHints)) return existing;
    const patch: Partial<Edition> = { syncHints, updatedAt: nowISO() };
    await database.editions.update(editionId, patch);
    return { ...existing, ...patch };
  });
}

export async function setEditionCompleted(
  editionId: string,
  completed: boolean,
  database: TriptychDB = defaultDB,
): Promise<Edition> {
  const existing = await database.editions.get(editionId);
  if (!existing) throw new Error(`Edition not found: ${editionId}`);
  if (existing.completed === completed) return existing;
  const patch: Partial<Edition> = { completed, updatedAt: nowISO() };
  await database.editions.update(editionId, patch);
  return { ...existing, ...patch };
}

// ---------------------------------------------------------------------------
// Release
// ---------------------------------------------------------------------------
export interface CreateReleaseInput {
  editionId: string;
  kind: Release['kind'];
  label: string;
  date: string;
  precision?: DatePrecision;
  time?: string;
  externalId?: string;
  origin?: Release['origin'];
  source: Release['source'];
  seenAt?: string;
  rolling?: boolean;
}

/** month/year 精度の date は月初・年初に揃える。 */
function canonicalDate(date: string, precision: DatePrecision): string {
  return precision === 'day' || !isValidYmd(date) ? date : precisionStart(date, precision);
}

function assertValidDate(date: string): void {
  if (!isValidYmd(date)) throw new RangeError(`Invalid release date (expected YYYY-MM-DD): ${date}`);
}

function buildRelease(input: CreateReleaseInput, ts: string): Release {
  const precision = input.precision ?? 'day';
  const release: Release = {
    id: nanoid(),
    editionId: input.editionId,
    kind: input.kind,
    label: input.label,
    date: canonicalDate(input.date, precision),
    precision,
    origin: input.origin ?? (input.source === 'manual' ? 'manual' : 'sync'),
    dateHistory: [],
    source: input.source,
    seenAt: input.seenAt,
    createdAt: ts,
    updatedAt: ts,
  };
  if (input.time !== undefined) release.time = input.time;
  if (input.externalId !== undefined) release.externalId = input.externalId;
  if (input.rolling) release.rolling = true;
  return release;
}

export async function createRelease(
  input: CreateReleaseInput,
  database: TriptychDB = defaultDB,
): Promise<Release> {
  const release = buildRelease(input, nowISO());
  await database.releases.add(release);
  return release;
}

export interface CreateManualReleaseInput {
  editionId: string;
  kind: Release['kind'];
  label: string;
  date: string;
  precision?: DatePrecision;
  time?: string;
  externalId?: string;
}

/** 手動 Release（origin='manual'）。本人が足したものなので新着にはしない。 */
export async function createManualRelease(
  input: CreateManualReleaseInput,
  database: TriptychDB = defaultDB,
): Promise<Release> {
  assertValidDate(input.date);
  return database.transaction('rw', database.editions, database.releases, async () => {
    if (!(await database.editions.get(input.editionId))) {
      throw new Error(`Edition not found: ${input.editionId}`);
    }
    const ts = nowISO();
    const release = buildRelease(
      { ...input, source: 'manual', origin: 'manual', seenAt: ts },
      ts,
    );
    await database.releases.add(release);
    return release;
  });
}

export async function getRelease(
  id: string,
  database: TriptychDB = defaultDB,
): Promise<Release | undefined> {
  return database.releases.get(id);
}

export async function listReleases(database: TriptychDB = defaultDB): Promise<Release[]> {
  return database.releases.toArray();
}

export async function listReleasesByEdition(
  editionId: string,
  database: TriptychDB = defaultDB,
): Promise<Release[]> {
  return database.releases.where('editionId').equals(editionId).toArray();
}

export type UpdateReleaseInput = Partial<
  Pick<
    Release,
    'label' | 'date' | 'precision' | 'time' | 'externalId' | 'seenAt' | 'kind' | 'source' | 'origin'
  >
>;

const CLEARABLE_RELEASE_KEYS = new Set(['time', 'externalId', 'seenAt']);

/** 内容（label/date/precision/time/externalId/kind）が実際に変わると origin='manual' にする。seenAt だけなら変えない。 */
export async function updateRelease(
  id: string,
  changes: UpdateReleaseInput,
  database: TriptychDB = defaultDB,
): Promise<Release> {
  return database.transaction('rw', database.releases, async () => {
    const existing = await database.releases.get(id);
    if (!existing) throw new Error(`Release not found: ${id}`);

    const patch: Partial<Release> = {};
    for (const [key, value] of Object.entries(changes)) {
      if (value !== undefined || CLEARABLE_RELEASE_KEYS.has(key)) {
        (patch as Record<string, unknown>)[key] = value;
      }
    }

    const precision = changes.precision ?? existing.precision ?? 'day';
    if ('date' in patch || 'precision' in patch) {
      const date = changes.date ?? existing.date;
      assertValidDate(date);
      patch.date = canonicalDate(date, precision);
      patch.precision = precision;
    }

    const edited =
      (patch.label !== undefined && patch.label !== existing.label) ||
      (patch.kind !== undefined && patch.kind !== existing.kind) ||
      (patch.date !== undefined && patch.date !== existing.date) ||
      (patch.precision !== undefined && patch.precision !== (existing.precision ?? 'day')) ||
      ('time' in patch && patch.time !== existing.time) ||
      ('externalId' in patch && patch.externalId !== existing.externalId);
    if (changes.origin) patch.origin = changes.origin;
    else if (edited) patch.origin = 'manual';

    patch.updatedAt = nowISO();
    await database.releases.update(id, patch);
    return { ...existing, ...patch };
  });
}

export async function deleteRelease(id: string, database: TriptychDB = defaultDB): Promise<void> {
  await database.releases.delete(id);
}

export interface ReleaseMergeInput {
  editionId: string;
  kind: Release['kind'];
  label: string;
  date: string;
  precision?: DatePrecision;
  time?: string;
  externalId?: string;
  source: Release['source'];
}

export interface MergeReleaseOptions {
  /** 新規作成する Release の日付が過去なら seenAt を作成時刻で埋める（既定 true）。 */
  markPastSeen?: boolean;
  /** 「今日」と作成時刻の基準（テスト用）。 */
  now?: Date;
}

const PRECISION_RANK: Record<DatePrecision, number> = { day: 0, month: 1, year: 2 };

interface DateWithPrecision {
  date: string;
  precision: DatePrecision;
}

/** 精度の細かい側の日付が、粗い側の期間の外に出たときだけ延期（精緻化・同範囲内の粗化は延期ではない）。 */
function isPostponement(prev: DateWithPrecision, next: DateWithPrecision): boolean {
  if (prev.precision === next.precision) return prev.date !== next.date;
  const [fine, coarse] =
    PRECISION_RANK[next.precision] < PRECISION_RANK[prev.precision] ? [next, prev] : [prev, next];
  return (
    compareYmd(fine.date, precisionStart(coarse.date, coarse.precision)) < 0 ||
    compareYmd(fine.date, precisionEnd(coarse.date, coarse.precision)) > 0
  );
}

/** externalId があれば (editionId, kind, externalId)、無ければ (editionId, kind, label) で照合。manual は触らない。 */
export async function mergeRelease(
  input: ReleaseMergeInput,
  database: TriptychDB = defaultDB,
  options: MergeReleaseOptions = {},
): Promise<Release> {
  assertValidDate(input.date);
  const precision = input.precision ?? 'day';
  const date = canonicalDate(input.date, precision);
  const now = options.now ?? new Date();

  return database.transaction('rw', database.releases, async () => {
    const matches = input.externalId
      ? await database.releases
          .where('[editionId+kind+externalId]')
          .equals([input.editionId, input.kind, input.externalId])
          .toArray()
      : (await database.releases.where('editionId').equals(input.editionId).toArray()).filter(
          (r) => r.kind === input.kind && r.label === input.label,
        );

    const existing = matches.find((r) => r.origin !== 'manual');
    if (!existing) {
      if (matches.length > 0) return matches[0];
      const ts = nowISO(now);
      const seen =
        options.markPastSeen !== false && isPastRelease({ date, precision }, todayJst(now));
      const release = buildRelease(
        { ...input, date, precision, seenAt: seen ? ts : undefined },
        ts,
      );
      await database.releases.add(release);
      return release;
    }

    const prev: DateWithPrecision = { date: existing.date, precision: existing.precision ?? 'day' };
    const changed =
      prev.date !== date ||
      prev.precision !== precision ||
      existing.time !== input.time ||
      existing.label !== input.label;
    if (!changed) return existing;

    const ts = nowISO(now);
    const patch: Partial<Release> = {
      label: input.label,
      date,
      precision,
      time: input.time,
      source: input.source,
      updatedAt: ts,
    };
    if (isPostponement(prev, { date, precision })) {
      patch.dateHistory = [...existing.dateHistory, { date: existing.date, changedAt: ts }];
    }
    await database.releases.update(existing.id, patch);
    return { ...existing, ...patch };
  });
}

// ---------------------------------------------------------------------------
// DetectionEvent
// ---------------------------------------------------------------------------

export interface CreateDetectionEventInput {
  workId: string;
  candidate: DetectionCandidate;
  matchScore: number;
  state?: DetectionEvent['state'];
}

export async function createDetectionEvent(
  input: CreateDetectionEventInput,
  database: TriptychDB = defaultDB,
): Promise<DetectionEvent> {
  const event: DetectionEvent = {
    id: nanoid(),
    workId: input.workId,
    candidate: input.candidate,
    matchScore: input.matchScore,
    state: input.state ?? 'pending',
    createdAt: nowISO(),
  };
  await database.detections.add(event);
  return event;
}

export async function getDetectionEvent(
  id: string,
  database: TriptychDB = defaultDB,
): Promise<DetectionEvent | undefined> {
  return database.detections.get(id);
}

export async function listDetectionEvents(
  database: TriptychDB = defaultDB,
): Promise<DetectionEvent[]> {
  return database.detections.toArray();
}

export async function listDetectionEventsByWork(
  workId: string,
  database: TriptychDB = defaultDB,
): Promise<DetectionEvent[]> {
  return database.detections.where('workId').equals(workId).toArray();
}

export async function updateDetectionEventState(
  id: string,
  state: DetectionEvent['state'],
  database: TriptychDB = defaultDB,
): Promise<DetectionEvent> {
  const existing = await database.detections.get(id);
  if (!existing) throw new Error(`DetectionEvent not found: ${id}`);
  const patch: Partial<DetectionEvent> = { state };
  await database.detections.update(id, patch);
  return { ...existing, ...patch };
}

export async function deleteDetectionEvent(
  id: string,
  database: TriptychDB = defaultDB,
): Promise<void> {
  await database.detections.delete(id);
}

// ---------------------------------------------------------------------------
// 設定画面: 新着をまとめて既読に・却下した検知・Library を空に
// ---------------------------------------------------------------------------

/** まだ見ていない Release だけに seenAt を入れる（本人の編集ではないので origin は変えない）。入れた件数を返す。 */
export async function markReleasesSeen(
  ids: readonly string[],
  database: TriptychDB = defaultDB,
  now: Date = new Date(),
): Promise<number> {
  if (ids.length === 0) return 0;
  const ts = nowISO(now);
  return database.transaction('rw', database.releases, async () => {
    let marked = 0;
    for (const release of await database.releases.bulkGet([...ids])) {
      if (!release || release.seenAt) continue;
      await database.releases.update(release.id, { seenAt: ts, updatedAt: ts });
      marked += 1;
    }
    return marked;
  });
}

/** 却下した時刻（rejectedAt）付きの検知。この項目ができる前に却下したものには無い。 */
export type RejectedDetection = DetectionEvent & { rejectedAt?: string };

/** 検知を却下し、却下した時刻を残す。 */
export async function rejectDetectionEvent(
  id: string,
  database: TriptychDB = defaultDB,
  now: Date = new Date(),
): Promise<RejectedDetection> {
  const existing = await database.detections.get(id);
  if (!existing) throw new Error(`DetectionEvent not found: ${id}`);
  const patch = { state: 'rejected' as const, rejectedAt: nowISO(now) };
  await database.detections.update(id, patch as Partial<DetectionEvent>);
  return { ...existing, ...patch };
}

/** 却下した検知を、新しく却下した順に（時刻の無い古い却下は検知日で比べる）。 */
export async function listRejectedDetections(database: TriptychDB = defaultDB): Promise<RejectedDetection[]> {
  const rows = (await database.detections.where('state').equals('rejected').toArray()) as RejectedDetection[];
  const at = (d: RejectedDetection) => d.rejectedAt ?? d.createdAt;
  return rows.sort((a, b) => at(b).localeCompare(at(a)));
}

/** 却下を取り消して保留中（候補）に戻す。再検知は同じ候補を作り直さないので、戻せば Home にまた出る。 */
export async function reopenDetectionEvent(
  id: string,
  database: TriptychDB = defaultDB,
): Promise<RejectedDetection> {
  return database.transaction('rw', database.detections, async () => {
    const existing = (await database.detections.get(id)) as RejectedDetection | undefined;
    if (!existing) throw new Error(`DetectionEvent not found: ${id}`);
    if (existing.state !== 'rejected') throw new Error(`DetectionEvent is not rejected: ${id}`);
    const { rejectedAt: _dropped, ...rest } = existing;
    const reopened: DetectionEvent = { ...rest, state: 'pending' };
    await database.detections.put(reopened);
    return reopened;
  });
}

export interface ClearedLibrary {
  works: number;
  editions: number;
  releases: number;
  detections: number;
}

/** 作品・版・Release・検知を全部消す。最終エクスポートと同期の記録は残し、作品ごとの検知の間隔だけ消す。 */
export async function clearLibrary(database: TriptychDB = defaultDB): Promise<ClearedLibrary> {
  const tables = [database.works, database.editions, database.releases, database.detections, database.meta];
  return database.transaction('rw', tables, async () => {
    const counts: ClearedLibrary = {
      works: await database.works.count(),
      editions: await database.editions.count(),
      releases: await database.releases.count(),
      detections: await database.detections.count(),
    };
    await Promise.all([
      database.works.clear(),
      database.editions.clear(),
      database.releases.clear(),
      database.detections.clear(),
      database.meta.delete('detectionThrottle'),
    ]);
    return counts;
  });
}
