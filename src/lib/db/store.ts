import { nanoid } from 'nanoid';
import { db as defaultDB, type TriptychDB } from './schema';
import type {
  Work,
  Edition,
  EditionProgress,
  Release,
  ReleaseDateHistoryEntry,
  DetectionEvent,
  DetectionCandidate,
  Status,
} from './schema';
import { normalizeTitle } from '../normalize';

function nowISO(): string {
  return new Date().toISOString();
}

// ---------------------------------------------------------------------------
// Work
// ---------------------------------------------------------------------------

export interface CreateWorkInput {
  title: string;
  aliases?: string[];
  coverEditionId?: string;
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

export type UpdateWorkInput = Partial<Pick<Work, 'title' | 'aliases' | 'coverEditionId'>>;

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

// ---------------------------------------------------------------------------
// Release
// ---------------------------------------------------------------------------

export interface CreateReleaseInput {
  editionId: string;
  kind: Release['kind'];
  label: string;
  date: string;
  source: Release['source'];
  seenAt?: string;
}

export async function createRelease(
  input: CreateReleaseInput,
  database: TriptychDB = defaultDB,
): Promise<Release> {
  const ts = nowISO();
  const release: Release = {
    id: nanoid(),
    editionId: input.editionId,
    kind: input.kind,
    label: input.label,
    date: input.date,
    dateHistory: [],
    source: input.source,
    seenAt: input.seenAt,
    createdAt: ts,
    updatedAt: ts,
  };
  await database.releases.add(release);
  return release;
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
  Pick<Release, 'label' | 'date' | 'seenAt' | 'kind' | 'source'>
>;

export async function updateRelease(
  id: string,
  changes: UpdateReleaseInput,
  database: TriptychDB = defaultDB,
): Promise<Release> {
  const existing = await database.releases.get(id);
  if (!existing) throw new Error(`Release not found: ${id}`);
  const patch: Partial<Release> = { ...changes, updatedAt: nowISO() };
  await database.releases.update(id, patch);
  return { ...existing, ...patch };
}

export async function deleteRelease(id: string, database: TriptychDB = defaultDB): Promise<void> {
  await database.releases.delete(id);
}

export interface ReleaseMergeInput {
  editionId: string;
  kind: Release['kind'];
  label: string;
  date: string;
  source: Release['source'];
}

/**
 * Release 差分マージ: 同一 Edition の同一 kind+label の Release が既にあれば、
 * date が変わっている場合のみ旧 date を dateHistory に追記して上書きする。
 * 一致する既存 Release が無ければ新規追加する。
 */
export async function mergeRelease(
  input: ReleaseMergeInput,
  database: TriptychDB = defaultDB,
): Promise<Release> {
  return database.transaction('rw', database.releases, async () => {
    const candidates = await database.releases
      .where('editionId')
      .equals(input.editionId)
      .toArray();
    const existing = candidates.find(
      (r) => r.kind === input.kind && r.label === input.label,
    );

    if (!existing) {
      return createRelease(
        {
          editionId: input.editionId,
          kind: input.kind,
          label: input.label,
          date: input.date,
          source: input.source,
        },
        database,
      );
    }

    if (existing.date === input.date) {
      // 変化なし: そのまま返す
      return existing;
    }

    const ts = nowISO();
    const historyEntry: ReleaseDateHistoryEntry = {
      date: existing.date,
      changedAt: ts,
    };
    const patch: Partial<Release> = {
      date: input.date,
      dateHistory: [...existing.dateHistory, historyEntry],
      source: input.source,
      updatedAt: ts,
    };
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
