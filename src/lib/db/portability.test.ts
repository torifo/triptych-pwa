import { beforeEach, describe, expect, it } from 'vitest';
import { createDB, type TriptychDB } from './schema';
import { createWork, createEdition, createRelease, createDetectionEvent } from './store';
import { exportAll, importAll, getLastExportAt, PortabilityError, EXPORT_VERSION } from './portability';

let db: TriptychDB;
let dbCounter = 0;

beforeEach(() => {
  dbCounter += 1;
  db = createDB(`triptych-portability-test-${dbCounter}-${Date.now()}`);
});

async function seed(database: TriptychDB) {
  const work = await createWork({ title: '転生したらスライムだった件' }, database);
  const edition = await createEdition(
    { workId: work.id, mediaType: 'web-novel', title: '原作' },
    database,
  );
  await createRelease(
    { editionId: edition.id, kind: 'episode', label: '第1話', date: '2026-01-01', source: 'narou' },
    database,
  );
  await createDetectionEvent(
    {
      workId: work.id,
      candidate: {
        source: 'anilist',
        externalId: '12345',
        title: 'アニメ版',
        airingStatus: 'NOT_YET_RELEASED',
      },
      matchScore: 0.9,
    },
    database,
  );
  return { work, edition };
}

describe('exportAll', () => {
  it('exports all tables and records lastExportAt in meta', async () => {
    await seed(db);
    const payload = await exportAll(db);

    expect(payload.version).toBe(EXPORT_VERSION);
    expect(typeof payload.exportedAt).toBe('string');
    expect(payload.tables.works).toHaveLength(1);
    expect(payload.tables.editions).toHaveLength(1);
    expect(payload.tables.releases).toHaveLength(1);
    expect(payload.tables.detections).toHaveLength(1);

    const lastExportEntry = payload.tables.meta.find((m) => m.key === 'lastExportAt');
    expect(lastExportEntry?.value).toBe(payload.exportedAt);

    const lastExportAt = await getLastExportAt(db);
    expect(lastExportAt).toBe(payload.exportedAt);
  });

  it('returns undefined lastExportAt before any export', async () => {
    expect(await getLastExportAt(db)).toBeUndefined();
  });
});

describe('exportAll -> importAll roundtrip', () => {
  it('restores an empty database from an export snapshot', async () => {
    const { work, edition } = await seed(db);
    const payload = await exportAll(db);
    const json = JSON.stringify(payload);

    const target = createDB(`triptych-portability-target-${dbCounter}-${Date.now()}`);
    await importAll(json, 'replace', target);

    const works = await target.works.toArray();
    const editions = await target.editions.toArray();
    const releases = await target.releases.toArray();
    const detections = await target.detections.toArray();

    expect(works).toHaveLength(1);
    expect(works[0].id).toBe(work.id);
    expect(editions).toHaveLength(1);
    expect(editions[0].id).toBe(edition.id);
    expect(releases).toHaveLength(1);
    expect(detections).toHaveLength(1);
  });

  it('replaces existing data in the target database (mode replace)', async () => {
    await seed(db);
    const payload = await exportAll(db);
    const json = JSON.stringify(payload);

    // インポート先には別データが既にある想定
    await createWork({ title: '既存の別作品' }, db);
    expect(await db.works.count()).toBe(2);

    await importAll(json, 'replace', db);

    const works = await db.works.toArray();
    expect(works).toHaveLength(1);
    expect(works[0].title).toBe('転生したらスライムだった件');
  });

  it('古いバックアップで本に自動で入っていたアニメの表紙は外して戻す（手で設定した表紙はそのまま）', async () => {
    const anime = 'https://s4.anilist.co/file/anilistcdn/media/anime/cover/medium/bx1-a.jpg';
    const work = await createWork({ title: '薬屋のひとりごと' }, db);
    const auto = await createEdition({ workId: work.id, mediaType: 'light-novel', title: '文庫', coverUrl: anime }, db);
    const manual = await createEdition({ workId: work.id, mediaType: 'manga', title: '漫画', coverUrl: anime }, db);
    await db.editions.update(auto.id, { syncHints: { coverFrom: 'catalog', ndlLine: '' } });
    const json = JSON.stringify(await exportAll(db));

    const target = createDB(`triptych-portability-cover-${dbCounter}-${Date.now()}`);
    await importAll(json, 'replace', target);

    const restored = await target.editions.get(auto.id);
    expect(restored?.coverUrl).toBeUndefined();
    expect(restored?.syncHints).toEqual({ ndlLine: '' });
    expect((await target.editions.get(manual.id))?.coverUrl).toBe(anime);
  });
});

describe('importAll validation', () => {
  it('rejects malformed JSON', async () => {
    await expect(importAll('{ not valid json', 'replace', db)).rejects.toThrow(PortabilityError);
  });

  it('rejects a version mismatch', async () => {
    const payload = {
      version: 2,
      exportedAt: new Date().toISOString(),
      tables: { works: [], editions: [], releases: [], detections: [], meta: [] },
    };
    await expect(importAll(JSON.stringify(payload), 'replace', db)).rejects.toThrow(
      PortabilityError,
    );
  });

  it('rejects a payload missing the tables object', async () => {
    const payload = { version: EXPORT_VERSION, exportedAt: new Date().toISOString() };
    await expect(importAll(JSON.stringify(payload), 'replace', db)).rejects.toThrow(
      PortabilityError,
    );
  });

  it('rejects a table that is not an array', async () => {
    const payload = {
      version: EXPORT_VERSION,
      exportedAt: new Date().toISOString(),
      tables: { works: 'not-an-array', editions: [], releases: [], detections: [], meta: [] },
    };
    await expect(importAll(JSON.stringify(payload), 'replace', db)).rejects.toThrow(
      PortabilityError,
    );
  });

  it('rejects rows missing the expected id field', async () => {
    const payload = {
      version: EXPORT_VERSION,
      exportedAt: new Date().toISOString(),
      tables: {
        works: [{ title: 'id無し' }],
        editions: [],
        releases: [],
        detections: [],
        meta: [],
      },
    };
    await expect(importAll(JSON.stringify(payload), 'replace', db)).rejects.toThrow(
      PortabilityError,
    );
  });

  it('leaves existing data untouched when the import is rejected', async () => {
    const { work } = await seed(db);
    await expect(importAll('not json at all', 'replace', db)).rejects.toThrow(PortabilityError);
    const works = await db.works.toArray();
    expect(works).toHaveLength(1);
    expect(works[0].id).toBe(work.id);
  });
});
