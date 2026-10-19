import Dexie from 'dexie';
import { beforeEach, describe, expect, it } from 'vitest';
import { createDB, type Release, type TriptychDB } from './schema';
import { mergeRelease } from './store';

let dbName: string;
let dbCounter = 0;
let migrated: TriptychDB | undefined;

beforeEach(async () => {
  await migrated?.close();
  migrated = undefined;
  dbCounter += 1;
  dbName = `triptych-migration-${dbCounter}-${Date.now()}`;
});

const TS = '2026-01-01T00:00:00.000Z';

function edition(id: string, mediaType: string) {
  return {
    id,
    workId: 'w1',
    mediaType,
    title: `edition-${id}`,
    sourceRefs: [],
    status: 'active',
    linkSource: 'auto',
    createdAt: TS,
    updatedAt: TS,
  };
}

function release(id: string, editionId: string, source: string, label = `label-${id}`) {
  return {
    id,
    editionId,
    kind: 'volume',
    label,
    date: '2026-03-04',
    dateHistory: [],
    source,
    createdAt: TS,
    updatedAt: TS,
  };
}

/** v1 スキーマ（externalId の複合インデックスなし）で DB を作り、v1 時点のデータを入れて閉じる。 */
async function seedV1(name: string): Promise<void> {
  const v1 = new Dexie(name);
  v1.version(1).stores({
    works: 'id, titleNormalized',
    editions: 'id, workId, status, mediaType',
    releases: 'id, editionId, date',
    detections: 'id, workId, state',
    meta: 'key',
  });
  await v1.table('works').add({
    id: 'w1',
    title: '本好きの下剋上',
    titleNormalized: '本好きの下剋上',
    aliases: [],
    createdAt: TS,
    updatedAt: TS,
  });
  await v1
    .table('editions')
    .bulkAdd([
      edition('e-anime', 'anime'),
      edition('e-ln', 'light-novel'),
      edition('e-manga', 'manga'),
      edition('e-web', 'web-novel'),
    ]);
  await v1.table('releases').bulkAdd([
    release('r-anime-anilist', 'e-anime', 'anilist'),
    release('r-ln-anilist', 'e-ln', 'anilist'), // 誤データ（監査 P2）
    release('r-manga-anilist', 'e-manga', 'anilist'), // 誤データ
    release('r-web-anilist', 'e-web', 'anilist'), // 誤データ
    release('r-ln-ndl', 'e-ln', 'ndl', '第3巻'),
    release('r-manga-openbd', 'e-manga', 'openbd'),
    release('r-web-narou', 'e-web', 'narou'),
    release('r-ln-manual', 'e-ln', 'manual'),
    release('r-orphan-anilist', 'e-missing', 'anilist'),
  ]);
  await v1.table('detections').bulkAdd([
    {
      id: 'd1',
      workId: 'w1',
      candidate: {
        source: 'anilist',
        externalId: '1',
        title: 'アニメ版',
        airingStatus: 'NOT_YET_RELEASED',
      },
      matchScore: 0.9,
      state: 'pending',
      createdAt: TS,
    },
  ]);
  await v1.table('meta').add({ key: 'lastSyncAt', value: TS });
  v1.close();
}

async function openMigrated(): Promise<TriptychDB> {
  await seedV1(dbName);
  migrated = createDB(dbName);
  await migrated.open();
  return migrated;
}

describe('Dexie v1 → v2 移行', () => {
  it('anime 以外の Edition に付いた source=anilist の Release だけを削除する', async () => {
    const db = await openMigrated();
    const ids = (await db.releases.toArray()).map((r) => r.id).sort();
    expect(ids).toEqual(
      ['r-anime-anilist', 'r-ln-ndl', 'r-manga-openbd', 'r-web-narou', 'r-ln-manual', 'r-orphan-anilist'].sort(),
    );
  });

  it('既存 Release に precision=day・origin=sync を補う（手で足した source=manual は origin=manual）', async () => {
    const db = await openMigrated();
    const releases = await db.releases.toArray();
    for (const r of releases) {
      expect(r.precision).toBe('day');
      expect(r.origin).toBe(r.source === 'manual' ? 'manual' : 'sync');
      expect(r.time).toBeUndefined();
      expect(r.externalId).toBeUndefined();
    }
    expect(releases.find((r) => r.id === 'r-ln-ndl')?.date).toBe('2026-03-04');
  });

  it('既存 DetectionEvent に via=title を補い、他のフィールドは保つ', async () => {
    const db = await openMigrated();
    const detection = await db.detections.get('d1');
    expect(detection?.via).toBe('title');
    expect(detection?.matchScore).toBe(0.9);
    expect(detection?.state).toBe('pending');
    expect(detection?.candidate.externalId).toBe('1');
  });

  it('works / editions / meta には触れない', async () => {
    const db = await openMigrated();
    expect(await db.works.count()).toBe(1);
    expect(await db.editions.count()).toBe(4);
    expect((await db.meta.get('lastSyncAt'))?.value).toBe(TS);
  });

  it('移行後に複合インデックス [editionId+kind+externalId] が使え、移行済み Release を label で更新できる', async () => {
    const db = await openMigrated();
    const created = await mergeRelease(
      {
        editionId: 'e-ln',
        kind: 'volume',
        label: '第4巻',
        date: '2026-12-10',
        externalId: '9784864727471',
        source: 'ndl',
      },
      db,
    );
    const hit = await db.releases
      .where('[editionId+kind+externalId]')
      .equals(['e-ln', 'volume', '9784864727471'])
      .toArray();
    expect(hit.map((r) => r.id)).toEqual([created.id]);

    // v1 由来の「第3巻」（externalId なし）は従来どおり label で照合される
    const updated = await mergeRelease(
      { editionId: 'e-ln', kind: 'volume', label: '第3巻', date: '2026-03-04', source: 'ndl' },
      db,
    );
    expect(updated.id).toBe('r-ln-ndl');
    expect(updated.dateHistory).toEqual([]);
  });

  it('すでに precision/origin/via を持つ行は上書きしない', async () => {
    await seedV1(dbName);
    // v1 のまま持っていた行に v2 のフィールドが入っていた場合（再移行・インポート混在）を模す
    const v1 = new Dexie(dbName);
    v1.version(1).stores({
      works: 'id, titleNormalized',
      editions: 'id, workId, status, mediaType',
      releases: 'id, editionId, date',
      detections: 'id, workId, state',
      meta: 'key',
    });
    await v1.table('releases').update('r-ln-ndl', { precision: 'month', origin: 'manual' });
    await v1.table('detections').update('d1', { via: 'relation' });
    v1.close();

    migrated = createDB(dbName);
    const kept = (await migrated.releases.get('r-ln-ndl')) as Release;
    expect(kept.precision).toBe('month');
    expect(kept.origin).toBe('manual');
    expect((await migrated.detections.get('d1'))?.via).toBe('relation');
  });
});

describe('Dexie v2 → v3 移行（本・Web 小説に自動で入ったアニメの表紙を外す）', () => {
  const ANIME_COVER = 'https://s4.anilist.co/file/anilistcdn/media/anime/cover/medium/bx1-a.jpg';
  const NOVEL_COVER = 'https://s4.anilist.co/file/anilistcdn/media/manga/cover/medium/bx2-b.jpg';

  function covered(id: string, mediaType: string, coverUrl: string | undefined, syncHints?: Record<string, unknown>) {
    return { ...edition(id, mediaType), coverUrl, syncHints };
  }

  async function seedV2(name: string, rows: ReturnType<typeof covered>[]): Promise<void> {
    const v2 = new Dexie(name);
    v2.version(2).stores({
      works: 'id, titleNormalized',
      editions: 'id, workId, status, mediaType',
      releases: 'id, editionId, date, [editionId+kind+externalId]',
      detections: 'id, workId, state',
      meta: 'key',
    });
    await v2.table('editions').bulkAdd(rows);
    v2.close();
  }

  it('anime 以外で、自動の表紙がアニメの表紙なら URL と自動の印を消す（他の手がかりは残す）', async () => {
    await seedV2(dbName, [
      covered('ln', 'light-novel', ANIME_COVER, { coverFrom: 'catalog', ndlLine: '' }),
      covered('manga', 'manga', ANIME_COVER, { coverFrom: 'anilist' }),
      covered('web', 'web-novel', ANIME_COVER, { coverFrom: 'catalog' }),
    ]);
    migrated = createDB(dbName);
    const ln = await migrated.editions.get('ln');
    expect(ln?.coverUrl).toBeUndefined();
    expect(ln?.syncHints?.coverFrom).toBeUndefined();
    expect(ln?.syncHints?.ndlLine).toBe('');
    for (const id of ['manga', 'web']) {
      const repaired = await migrated.editions.get(id);
      expect(repaired?.coverUrl).toBeUndefined();
      expect(repaired?.syncHints?.coverFrom).toBeUndefined();
    }
  });

  it('手で設定した表紙・アニメの Edition・アニメ以外の表紙には触れない', async () => {
    await seedV2(dbName, [
      covered('manual', 'light-novel', ANIME_COVER, {}),
      covered('manual-no-hints', 'manga', ANIME_COVER),
      covered('anime', 'anime', ANIME_COVER, { coverFrom: 'anilist' }),
      covered('novel', 'light-novel', NOVEL_COVER, { coverFrom: 'catalog' }),
      covered('empty', 'manga', undefined, { coverFrom: 'catalog' }),
    ]);
    migrated = createDB(dbName);
    expect((await migrated.editions.get('manual'))?.coverUrl).toBe(ANIME_COVER);
    expect((await migrated.editions.get('manual-no-hints'))?.coverUrl).toBe(ANIME_COVER);
    expect(await migrated.editions.get('anime')).toMatchObject({ coverUrl: ANIME_COVER, syncHints: { coverFrom: 'anilist' } });
    expect(await migrated.editions.get('novel')).toMatchObject({ coverUrl: NOVEL_COVER, syncHints: { coverFrom: 'catalog' } });
    expect((await migrated.editions.get('empty'))?.syncHints?.coverFrom).toBe('catalog');
  });

  it('v1 からでも v2 → v3 と続けて移行する', async () => {
    const db = await openMigrated();
    expect(db.verno).toBe(3);
    expect(await db.editions.count()).toBe(4);
  });
});
