import { beforeEach, describe, expect, it } from 'vitest';
import { createDB, type TriptychDB } from './schema';
import {
  createWork,
  getWork,
  listWorks,
  updateWork,
  deleteWork,
  createEdition,
  getEdition,
  listEditionsByWork,
  updateEdition,
  reassignEditionWork,
  updateEditionStatus,
  updateEditionProgress,
  deleteEdition,
  createRelease,
  listReleasesByEdition,
  updateRelease,
  deleteRelease,
  mergeRelease,
  createDetectionEvent,
  getDetectionEvent,
  listDetectionEventsByWork,
  updateDetectionEventState,
  deleteDetectionEvent,
} from './store';

let db: TriptychDB;
let dbCounter = 0;

beforeEach(() => {
  dbCounter += 1;
  db = createDB(`triptych-test-${dbCounter}-${Date.now()}`);
});

describe('Work CRUD', () => {
  it('creates a work with a normalized title', async () => {
    const work = await createWork({ title: '転生したら スライムだった件！' }, db);
    expect(work.id).toBeTruthy();
    expect(work.titleNormalized).toBe('転生したらスライムだった件');
    expect(work.aliases).toEqual([]);

    const fetched = await getWork(work.id, db);
    expect(fetched).toEqual(work);
  });

  it('lists works', async () => {
    await createWork({ title: 'A' }, db);
    await createWork({ title: 'B' }, db);
    const works = await listWorks(db);
    expect(works).toHaveLength(2);
  });

  it('updates a work and recomputes titleNormalized when title changes', async () => {
    const work = await createWork({ title: 'Old Title' }, db);
    const updated = await updateWork(work.id, { title: 'New Title!' }, db);
    expect(updated.title).toBe('New Title!');
    expect(updated.titleNormalized).toBe('newtitle');
    expect(typeof updated.updatedAt).toBe('string');
  });

  it('deletes a work and cascades editions/releases/detections', async () => {
    const work = await createWork({ title: 'ToDelete' }, db);
    const edition = await createEdition(
      { workId: work.id, mediaType: 'manga', title: 'Edition' },
      db,
    );
    await createRelease(
      { editionId: edition.id, kind: 'volume', label: '第1巻', date: '2026-01-01', source: 'manual' },
      db,
    );
    await createDetectionEvent(
      {
        workId: work.id,
        candidate: {
          source: 'anilist',
          externalId: '1',
          title: 'Anime',
          airingStatus: 'NOT_YET_RELEASED',
        },
        matchScore: 0.9,
      },
      db,
    );

    await deleteWork(work.id, db);

    expect(await getWork(work.id, db)).toBeUndefined();
    expect(await listEditionsByWork(work.id, db)).toHaveLength(0);
    expect(await listReleasesByEdition(edition.id, db)).toHaveLength(0);
    expect(await listDetectionEventsByWork(work.id, db)).toHaveLength(0);
  });
});

describe('Edition CRUD', () => {
  it('creates an edition with defaults', async () => {
    const work = await createWork({ title: 'Work' }, db);
    const edition = await createEdition(
      { workId: work.id, mediaType: 'web-novel', title: 'WN Edition' },
      db,
    );
    expect(edition.status).toBe('plan');
    expect(edition.linkSource).toBe('manual');
    expect(edition.sourceRefs).toEqual([]);

    const fetched = await getEdition(edition.id, db);
    expect(fetched).toEqual(edition);
  });

  it('updates edition fields', async () => {
    const work = await createWork({ title: 'Work' }, db);
    const edition = await createEdition(
      { workId: work.id, mediaType: 'manga', title: 'Manga' },
      db,
    );
    const updated = await updateEdition(edition.id, { title: 'Manga v2', note: 'note' }, db);
    expect(updated.title).toBe('Manga v2');
    expect(updated.note).toBe('note');
  });

  it('reassigns the workId of an edition (relink)', async () => {
    const workA = await createWork({ title: 'A' }, db);
    const workB = await createWork({ title: 'B' }, db);
    const edition = await createEdition(
      { workId: workA.id, mediaType: 'anime', title: 'Anime' },
      db,
    );
    const release = await createRelease(
      { editionId: edition.id, kind: 'episode', label: '第1話', date: '2026-01-01', source: 'anilist' },
      db,
    );

    const relinked = await reassignEditionWork(edition.id, workB.id, db);
    expect(relinked.workId).toBe(workB.id);

    const editionsUnderA = await listEditionsByWork(workA.id, db);
    const editionsUnderB = await listEditionsByWork(workB.id, db);
    expect(editionsUnderA).toHaveLength(0);
    expect(editionsUnderB).toHaveLength(1);

    // Release follows via editionId reference, unaffected by relink
    const releases = await listReleasesByEdition(edition.id, db);
    expect(releases).toHaveLength(1);
    expect(releases[0]?.id).toBe(release.id);
  });

  it('updates status and progress independently', async () => {
    const work = await createWork({ title: 'Work' }, db);
    const edition = await createEdition(
      { workId: work.id, mediaType: 'light-novel', title: 'LN' },
      db,
    );

    const withStatus = await updateEditionStatus(edition.id, 'active', db);
    expect(withStatus.status).toBe('active');

    const withProgress = await updateEditionProgress(
      edition.id,
      { unit: 'volume', current: 3, total: 10 },
      db,
    );
    expect(withProgress.progress).toEqual({ unit: 'volume', current: 3, total: 10 });
    expect(withProgress.status).toBe('active');
  });

  it('deletes an edition and its releases', async () => {
    const work = await createWork({ title: 'Work' }, db);
    const edition = await createEdition(
      { workId: work.id, mediaType: 'manga', title: 'Manga' },
      db,
    );
    await createRelease(
      { editionId: edition.id, kind: 'volume', label: '第1巻', date: '2026-01-01', source: 'manual' },
      db,
    );

    await deleteEdition(edition.id, db);
    expect(await getEdition(edition.id, db)).toBeUndefined();
    expect(await listReleasesByEdition(edition.id, db)).toHaveLength(0);
  });
});

describe('Release CRUD', () => {
  it('creates and updates a release', async () => {
    const work = await createWork({ title: 'Work' }, db);
    const edition = await createEdition(
      { workId: work.id, mediaType: 'manga', title: 'Manga' },
      db,
    );
    const release = await createRelease(
      { editionId: edition.id, kind: 'volume', label: '第1巻', date: '2026-01-01', source: 'manual' },
      db,
    );
    expect(release.dateHistory).toEqual([]);

    const updated = await updateRelease(release.id, { seenAt: '2026-01-02T00:00:00.000Z' }, db);
    expect(updated.seenAt).toBe('2026-01-02T00:00:00.000Z');
  });

  it('deletes a release', async () => {
    const work = await createWork({ title: 'Work' }, db);
    const edition = await createEdition(
      { workId: work.id, mediaType: 'manga', title: 'Manga' },
      db,
    );
    const release = await createRelease(
      { editionId: edition.id, kind: 'volume', label: '第1巻', date: '2026-01-01', source: 'manual' },
      db,
    );
    await deleteRelease(release.id, db);
    expect(await listReleasesByEdition(edition.id, db)).toHaveLength(0);
  });
});

describe('mergeRelease', () => {
  it('creates a new release when no matching kind+label exists', async () => {
    const work = await createWork({ title: 'Work' }, db);
    const edition = await createEdition(
      { workId: work.id, mediaType: 'manga', title: 'Manga' },
      db,
    );

    const merged = await mergeRelease(
      { editionId: edition.id, kind: 'volume', label: '第1巻', date: '2026-01-01', source: 'openbd' },
      db,
    );
    expect(merged.date).toBe('2026-01-01');
    expect(merged.dateHistory).toEqual([]);

    const releases = await listReleasesByEdition(edition.id, db);
    expect(releases).toHaveLength(1);
  });

  it('appends the old date to dateHistory and overwrites when the date changes', async () => {
    const work = await createWork({ title: 'Work' }, db);
    const edition = await createEdition(
      { workId: work.id, mediaType: 'manga', title: 'Manga' },
      db,
    );

    await mergeRelease(
      { editionId: edition.id, kind: 'volume', label: '第1巻', date: '2026-01-01', source: 'openbd' },
      db,
    );

    const merged = await mergeRelease(
      { editionId: edition.id, kind: 'volume', label: '第1巻', date: '2026-01-15', source: 'openbd' },
      db,
    );

    expect(merged.date).toBe('2026-01-15');
    expect(merged.dateHistory).toHaveLength(1);
    expect(merged.dateHistory[0]?.date).toBe('2026-01-01');

    const releases = await listReleasesByEdition(edition.id, db);
    expect(releases).toHaveLength(1);
  });

  it('does not add a dateHistory entry when the date is unchanged', async () => {
    const work = await createWork({ title: 'Work' }, db);
    const edition = await createEdition(
      { workId: work.id, mediaType: 'manga', title: 'Manga' },
      db,
    );

    await mergeRelease(
      { editionId: edition.id, kind: 'volume', label: '第1巻', date: '2026-01-01', source: 'openbd' },
      db,
    );
    const merged = await mergeRelease(
      { editionId: edition.id, kind: 'volume', label: '第1巻', date: '2026-01-01', source: 'openbd' },
      db,
    );

    expect(merged.dateHistory).toEqual([]);
    const releases = await listReleasesByEdition(edition.id, db);
    expect(releases).toHaveLength(1);
  });

  it('accumulates multiple date changes in dateHistory', async () => {
    const work = await createWork({ title: 'Work' }, db);
    const edition = await createEdition(
      { workId: work.id, mediaType: 'manga', title: 'Manga' },
      db,
    );

    await mergeRelease(
      { editionId: edition.id, kind: 'volume', label: '第1巻', date: '2026-01-01', source: 'openbd' },
      db,
    );
    await mergeRelease(
      { editionId: edition.id, kind: 'volume', label: '第1巻', date: '2026-01-15', source: 'openbd' },
      db,
    );
    const merged = await mergeRelease(
      { editionId: edition.id, kind: 'volume', label: '第1巻', date: '2026-02-01', source: 'openbd' },
      db,
    );

    expect(merged.date).toBe('2026-02-01');
    expect(merged.dateHistory.map((h) => h.date)).toEqual(['2026-01-01', '2026-01-15']);
  });

  it('treats different kind+label combinations as separate releases', async () => {
    const work = await createWork({ title: 'Work' }, db);
    const edition = await createEdition(
      { workId: work.id, mediaType: 'anime', title: 'Anime' },
      db,
    );

    await mergeRelease(
      { editionId: edition.id, kind: 'episode', label: '第1話', date: '2026-01-01', source: 'anilist' },
      db,
    );
    await mergeRelease(
      { editionId: edition.id, kind: 'episode', label: '第2話', date: '2026-01-08', source: 'anilist' },
      db,
    );

    const releases = await listReleasesByEdition(edition.id, db);
    expect(releases).toHaveLength(2);
  });
});

describe('DetectionEvent CRUD', () => {
  it('creates a detection event with pending state by default', async () => {
    const work = await createWork({ title: 'Work' }, db);
    const event = await createDetectionEvent(
      {
        workId: work.id,
        candidate: {
          source: 'anilist',
          externalId: '123',
          title: 'Anime Title',
          airingStatus: 'NOT_YET_RELEASED',
        },
        matchScore: 0.85,
      },
      db,
    );
    expect(event.state).toBe('pending');

    const fetched = await getDetectionEvent(event.id, db);
    expect(fetched).toEqual(event);
  });

  it('transitions state to accepted/rejected', async () => {
    const work = await createWork({ title: 'Work' }, db);
    const event = await createDetectionEvent(
      {
        workId: work.id,
        candidate: {
          source: 'anilist',
          externalId: '123',
          title: 'Anime Title',
          airingStatus: 'RELEASING',
        },
        matchScore: 0.85,
      },
      db,
    );

    const accepted = await updateDetectionEventState(event.id, 'accepted', db);
    expect(accepted.state).toBe('accepted');

    const rejected = await updateDetectionEventState(event.id, 'rejected', db);
    expect(rejected.state).toBe('rejected');
  });

  it('deletes a detection event', async () => {
    const work = await createWork({ title: 'Work' }, db);
    const event = await createDetectionEvent(
      {
        workId: work.id,
        candidate: {
          source: 'anilist',
          externalId: '123',
          title: 'Anime Title',
          airingStatus: 'FINISHED',
        },
        matchScore: 0.5,
      },
      db,
    );
    await deleteDetectionEvent(event.id, db);
    expect(await getDetectionEvent(event.id, db)).toBeUndefined();
  });
});
