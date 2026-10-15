import { beforeEach, describe, expect, it } from 'vitest';
import { createDB, type TriptychDB } from './schema';
import {
  clearLibrary,
  createDetectionEvent,
  createEdition,
  createRelease,
  createWork,
  getDetectionEvent,
  getRelease,
  listRejectedDetections,
  markReleasesSeen,
  rejectDetectionEvent,
  reopenDetectionEvent,
  updateDetectionEventState,
} from './store';

let db: TriptychDB;
let dbCounter = 0;

beforeEach(() => {
  dbCounter += 1;
  db = createDB(`triptych-settings-test-${dbCounter}-${Date.now()}`);
});

async function release(editionId: string, label: string, seenAt?: string) {
  return createRelease(
    { editionId, kind: 'volume', label, date: '2026-10-20', source: 'ndl', seenAt },
    db,
  );
}

async function detection(workId: string, externalId: string, title: string) {
  return createDetectionEvent(
    { workId, candidate: { source: 'anilist', externalId, title, airingStatus: 'NOT_YET_RELEASED' }, matchScore: 1 },
    db,
  );
}

describe('markReleasesSeen', () => {
  it('まだ見ていない Release だけに seenAt を入れ、入れた件数を返す', async () => {
    const work = await createWork({ title: 'W' }, db);
    const edition = await createEdition({ workId: work.id, mediaType: 'manga', title: 'M' }, db);
    const a = await release(edition.id, '1');
    const b = await release(edition.id, '2');
    const seen = await release(edition.id, '3', '2026-10-01T00:00:00.000Z');
    const now = new Date('2026-10-15T03:00:00.000Z');

    expect(await markReleasesSeen([a.id, b.id, seen.id, 'missing'], db, now)).toBe(2);

    expect((await getRelease(a.id, db))?.seenAt).toBe(now.toISOString());
    expect((await getRelease(b.id, db))?.seenAt).toBe(now.toISOString());
    // 見たことのある Release の時刻は動かさない
    expect((await getRelease(seen.id, db))?.seenAt).toBe('2026-10-01T00:00:00.000Z');
  });

  it('既読にしても、本人が編集した扱い（origin = manual）にはしない', async () => {
    const work = await createWork({ title: 'W' }, db);
    const edition = await createEdition({ workId: work.id, mediaType: 'manga', title: 'M' }, db);
    const a = await release(edition.id, '1');

    await markReleasesSeen([a.id], db);

    expect((await getRelease(a.id, db))?.origin).toBe('sync');
  });

  it('空なら何もしない', async () => {
    expect(await markReleasesSeen([], db)).toBe(0);
  });
});

describe('却下した検知', () => {
  it('却下すると rejected と却下した時刻を残す', async () => {
    const work = await createWork({ title: 'W' }, db);
    const event = await detection(work.id, '1', 'A');
    const now = new Date('2026-10-14T12:00:00.000Z');

    const rejected = await rejectDetectionEvent(event.id, db, now);

    expect(rejected).toMatchObject({ state: 'rejected', rejectedAt: now.toISOString() });
    expect(await getDetectionEvent(event.id, db)).toMatchObject({ state: 'rejected', rejectedAt: now.toISOString() });
  });

  it('無い検知を却下しようとしたら投げる', async () => {
    await expect(rejectDetectionEvent('missing', db)).rejects.toThrow('DetectionEvent not found');
  });

  it('却下したものだけを、新しく却下した順に返す（時刻の無い古い却下は検知日で並べる）', async () => {
    const work = await createWork({ title: 'W' }, db);
    const old = await detection(work.id, '1', '古い却下');
    await updateDetectionEventState(old.id, 'rejected', db); // 却下時刻を残さなかった頃の却下
    const first = await detection(work.id, '2', '先に却下');
    const second = await detection(work.id, '3', '後で却下');
    await detection(work.id, '4', '保留中');
    const accepted = await detection(work.id, '5', '承認済み');
    await updateDetectionEventState(accepted.id, 'accepted', db);

    await rejectDetectionEvent(first.id, db, new Date('2999-01-01T00:00:00.000Z'));
    await rejectDetectionEvent(second.id, db, new Date('2999-01-02T00:00:00.000Z'));

    const list = await listRejectedDetections(db);
    expect(list.map((d) => d.candidate.title)).toEqual(['後で却下', '先に却下', '古い却下']);
    expect(list[2].rejectedAt).toBeUndefined();
  });

  it('取り消すと保留中（候補）に戻り、却下の時刻を消す', async () => {
    const work = await createWork({ title: 'W' }, db);
    const event = await detection(work.id, '1', 'A');
    await rejectDetectionEvent(event.id, db);

    const reopened = await reopenDetectionEvent(event.id, db);

    expect(reopened.state).toBe('pending');
    expect(reopened.rejectedAt).toBeUndefined();
    const stored = await getDetectionEvent(event.id, db);
    expect(stored?.state).toBe('pending');
    expect(stored && 'rejectedAt' in stored).toBe(false);
    expect(await listRejectedDetections(db)).toEqual([]);
  });

  it('却下していない検知は取り消せない', async () => {
    const work = await createWork({ title: 'W' }, db);
    const event = await detection(work.id, '1', 'A');
    await updateDetectionEventState(event.id, 'accepted', db);

    await expect(reopenDetectionEvent(event.id, db)).rejects.toThrow('not rejected');
    await expect(reopenDetectionEvent('missing', db)).rejects.toThrow('DetectionEvent not found');
    expect((await getDetectionEvent(event.id, db))?.state).toBe('accepted');
  });
});

describe('clearLibrary', () => {
  it('作品・版・Release・検知を消し、消した件数を返す', async () => {
    const work = await createWork({ title: 'W' }, db);
    const edition = await createEdition({ workId: work.id, mediaType: 'manga', title: 'M' }, db);
    await release(edition.id, '1');
    await release(edition.id, '2');
    await detection(work.id, '1', 'A');
    await createWork({ title: 'W2' }, db);

    expect(await clearLibrary(db)).toEqual({ works: 2, editions: 1, releases: 2, detections: 1 });

    expect(await db.works.count()).toBe(0);
    expect(await db.editions.count()).toBe(0);
    expect(await db.releases.count()).toBe(0);
    expect(await db.detections.count()).toBe(0);
  });

  it('最終エクスポートと同期の記録は残し、作品ごとの検知の間隔は消す', async () => {
    await createWork({ title: 'W' }, db);
    await db.meta.bulkPut([
      { key: 'lastExportAt', value: '2026-10-01T00:00:00.000Z' },
      { key: 'sync', value: { lastSyncAt: '2026-10-15T00:00:00.000Z' } },
      { key: 'detectionThrottle', value: { someWork: '2026-10-15T00:00:00.000Z' } },
    ]);

    await clearLibrary(db);

    expect((await db.meta.get('lastExportAt'))?.value).toBe('2026-10-01T00:00:00.000Z');
    expect(await db.meta.get('sync')).toBeDefined();
    expect(await db.meta.get('detectionThrottle')).toBeUndefined();
  });
});
