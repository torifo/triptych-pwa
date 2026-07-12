import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createDB, type TriptychDB } from '../db/schema';
import {
  createWork,
  createEdition,
  createDetectionEvent,
  getEdition,
  listDetectionEventsByWork,
  listReleasesByEdition,
  updateDetectionEventState,
} from '../db/store';
import { runDetection, acceptDetection, rejectDetection, DETECTION_MATCH_THRESHOLD } from './detect';

let db: TriptychDB;
let dbCounter = 0;

beforeEach(() => {
  dbCounter += 1;
  db = createDB(`triptych-detect-test-${dbCounter}-${Date.now()}`);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

// ---------------------------------------------------------------------------
// fetch mock helper
// ---------------------------------------------------------------------------

function installFetchMock(handler: (url: string) => unknown[]): ReturnType<typeof vi.fn> {
  const calls: string[] = [];
  const fetchMock = vi.fn(async (url: string) => {
    calls.push(url);
    return {
      ok: true,
      status: 200,
      json: async () => handler(url),
    } as Response;
  });
  vi.stubGlobal('fetch', fetchMock);
  (fetchMock as unknown as { calls: string[] }).calls = calls;
  return fetchMock;
}

function candidate(overrides: Partial<{
  externalId: string;
  title: string;
  airingStatus: 'NOT_YET_RELEASED' | 'RELEASING' | 'FINISHED';
  startDate: string | null;
  matchScore: number;
}> = {}) {
  const startDate = 'startDate' in overrides ? overrides.startDate : '2026-04-01';
  return {
    source: 'anilist' as const,
    externalId: overrides.externalId ?? '100',
    title: overrides.title ?? 'Test Anime',
    airingStatus: overrides.airingStatus ?? 'NOT_YET_RELEASED',
    startDate: startDate ?? undefined,
    matchScore: overrides.matchScore ?? 0.95,
  };
}

// ---------------------------------------------------------------------------
// 1. 原作系のみ対象
// ---------------------------------------------------------------------------

describe('runDetection: target scope', () => {
  it('only checks works with tracked original-media editions (web-novel/light-novel/manga)', async () => {
    const novelWork = await createWork({ title: 'Novel Work' }, db);
    await createEdition(
      { workId: novelWork.id, mediaType: 'web-novel', title: 'Novel', status: 'active' },
      db,
    );

    const animeOnlyWork = await createWork({ title: 'Anime Only Work' }, db);
    await createEdition(
      { workId: animeOnlyWork.id, mediaType: 'anime', title: 'Anime', status: 'active' },
      db,
    );

    const fetchMock = installFetchMock(() => [candidate()]);

    const result = await runDetection({ database: db });

    expect(result.checked).toBe(1);
    const calls = (fetchMock as unknown as { calls: string[] }).calls;
    expect(calls.some((u) => u.includes('Novel+Work') || u.includes('Novel%20Work'))).toBe(true);
    expect(
      calls.some((u) => u.includes('Anime+Only+Work') || u.includes('Anime%20Only%20Work')),
    ).toBe(false);
  });

  it('does not check original editions that are done/dropped (not tracked)', async () => {
    const work = await createWork({ title: 'Finished Work' }, db);
    await createEdition(
      { workId: work.id, mediaType: 'manga', title: 'Manga', status: 'done' },
      db,
    );

    const fetchMock = installFetchMock(() => [candidate()]);
    const result = await runDetection({ database: db });

    expect(result.checked).toBe(0);
    expect((fetchMock as unknown as { calls: string[] }).calls.length).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// 2. 24h スロットル
// ---------------------------------------------------------------------------

describe('runDetection: 24h throttle', () => {
  it('skips a work checked less than 24h ago', async () => {
    const work = await createWork({ title: 'Throttled Work' }, db);
    await createEdition(
      { workId: work.id, mediaType: 'web-novel', title: 'Novel', status: 'plan' },
      db,
    );

    installFetchMock(() => [candidate()]);
    const t0 = new Date('2026-01-01T00:00:00Z');
    const first = await runDetection({ database: db, now: () => t0 });
    expect(first.checked).toBe(1);

    const fetchMock2 = installFetchMock(() => [candidate()]);
    const t1 = new Date('2026-01-01T10:00:00Z'); // 10h later, < 24h
    const second = await runDetection({ database: db, now: () => t1 });
    expect(second.checked).toBe(0);
    expect((fetchMock2 as unknown as { calls: string[] }).calls.length).toBe(0);
  });

  it('re-checks a work after 24h has elapsed', async () => {
    const work = await createWork({ title: 'Elapsed Work' }, db);
    await createEdition(
      { workId: work.id, mediaType: 'web-novel', title: 'Novel', status: 'plan' },
      db,
    );

    installFetchMock(() => [candidate({ externalId: '1' })]);
    const t0 = new Date('2026-01-01T00:00:00Z');
    await runDetection({ database: db, now: () => t0 });

    installFetchMock(() => [candidate({ externalId: '2' })]);
    const t1 = new Date('2026-01-02T01:00:00Z'); // 25h later
    const second = await runDetection({ database: db, now: () => t1 });
    expect(second.checked).toBe(1);
  });
});

// ---------------------------------------------------------------------------
// 3. rejected 除外
// ---------------------------------------------------------------------------

describe('runDetection: rejected exclusion', () => {
  it('does not re-create a DetectionEvent for a candidate already rejected', async () => {
    const work = await createWork({ title: 'Rejected Work' }, db);
    await createEdition(
      { workId: work.id, mediaType: 'manga', title: 'Manga', status: 'active' },
      db,
    );
    const event = await createDetectionEvent(
      { workId: work.id, candidate: candidate({ externalId: '100' }), matchScore: 0.95 },
      db,
    );
    await updateDetectionEventState(event.id, 'rejected', db);

    installFetchMock(() => [candidate({ externalId: '100' })]);
    const result = await runDetection({ database: db, force: true });

    expect(result.created).toHaveLength(0);
    const events = await listDetectionEventsByWork(work.id, db);
    expect(events).toHaveLength(1);
    expect(events[0].state).toBe('rejected');
  });
});

// ---------------------------------------------------------------------------
// 4. 既存アニメ Edition 除外
// ---------------------------------------------------------------------------

describe('runDetection: existing anime edition exclusion', () => {
  it('does not create a DetectionEvent when the work already has an anime edition for that externalId', async () => {
    const work = await createWork({ title: 'Already Linked Work' }, db);
    await createEdition(
      { workId: work.id, mediaType: 'web-novel', title: 'Novel', status: 'active' },
      db,
    );
    await createEdition(
      {
        workId: work.id,
        mediaType: 'anime',
        title: 'Anime',
        status: 'plan',
        sourceRefs: [{ source: 'anilist', externalId: '100', url: 'https://anilist.co/anime/100' }],
      },
      db,
    );

    installFetchMock(() => [candidate({ externalId: '100' })]);
    const result = await runDetection({ database: db, force: true });

    expect(result.created).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// 5. accept で Edition + Release 生成
// ---------------------------------------------------------------------------

describe('acceptDetection', () => {
  it('creates an auto-linked anime edition and a season-start release, and marks the event accepted', async () => {
    const work = await createWork({ title: 'Accept Work' }, db);
    const event = await createDetectionEvent(
      {
        workId: work.id,
        candidate: candidate({ externalId: '200', title: 'New Anime', startDate: '2026-07-01' }),
        matchScore: 0.9,
      },
      db,
    );

    const { edition, event: updated } = await acceptDetection(event.id, db);

    expect(edition.workId).toBe(work.id);
    expect(edition.mediaType).toBe('anime');
    expect(edition.linkSource).toBe('auto');
    expect(edition.sourceRefs).toEqual([
      { source: 'anilist', externalId: '200', url: 'https://anilist.co/anime/200' },
    ]);
    expect(updated.state).toBe('accepted');

    const fetched = await getEdition(edition.id, db);
    expect(fetched).toBeDefined();

    const releases = await listReleasesByEdition(edition.id, db);
    expect(releases).toHaveLength(1);
    expect(releases[0]).toMatchObject({ kind: 'season-start', date: '2026-07-01' });
  });

  it('does not create a release when the candidate has no startDate', async () => {
    const work = await createWork({ title: 'No Date Work' }, db);
    const event = await createDetectionEvent(
      {
        workId: work.id,
        candidate: candidate({ externalId: '201', startDate: null }),
        matchScore: 0.9,
      },
      db,
    );

    const { edition } = await acceptDetection(event.id, db);
    const releases = await listReleasesByEdition(edition.id, db);
    expect(releases).toHaveLength(0);
  });
});

describe('rejectDetection', () => {
  it('marks the event as rejected', async () => {
    const work = await createWork({ title: 'Reject Work' }, db);
    const event = await createDetectionEvent(
      { workId: work.id, candidate: candidate({ externalId: '300' }), matchScore: 0.9 },
      db,
    );

    const updated = await rejectDetection(event.id, db);
    expect(updated.state).toBe('rejected');
  });
});

// ---------------------------------------------------------------------------
// 6. 閾値未満は無視
// ---------------------------------------------------------------------------

describe('runDetection: below-threshold candidates are ignored', () => {
  it('does not create a DetectionEvent when matchScore is below the threshold', async () => {
    const work = await createWork({ title: 'Low Score Work' }, db);
    await createEdition(
      { workId: work.id, mediaType: 'light-novel', title: 'LN', status: 'active' },
      db,
    );

    installFetchMock(() => [candidate({ matchScore: DETECTION_MATCH_THRESHOLD - 0.01 })]);
    const result = await runDetection({ database: db });

    expect(result.created).toHaveLength(0);
  });

  it('creates a DetectionEvent when matchScore is exactly at the threshold', async () => {
    const work = await createWork({ title: 'Exact Score Work' }, db);
    await createEdition(
      { workId: work.id, mediaType: 'light-novel', title: 'LN', status: 'active' },
      db,
    );

    installFetchMock(() => [candidate({ matchScore: DETECTION_MATCH_THRESHOLD })]);
    const result = await runDetection({ database: db });

    expect(result.created).toHaveLength(1);
  });
});
