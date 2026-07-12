import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createDB, type TriptychDB } from '../db/schema';
import { createWork, createEdition, listReleasesByEdition, getEdition } from '../db/store';
import { runSync } from './engine';

let db: TriptychDB;
let dbCounter = 0;

beforeEach(() => {
  dbCounter += 1;
  db = createDB(`triptych-sync-test-${dbCounter}-${Date.now()}`);
  localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

// ---------------------------------------------------------------------------
// fetch mock helpers
// ---------------------------------------------------------------------------

interface MockResponses {
  [urlPrefix: string]: () => { ok: boolean; status?: number; json: () => Promise<unknown> };
}

function installFetchMock(responses: MockResponses): ReturnType<typeof vi.fn> {
  const calls: string[] = [];
  const fetchMock = vi.fn(async (url: string) => {
    calls.push(url);
    const key = Object.keys(responses).find((prefix) => url.startsWith(prefix));
    if (!key) {
      throw new Error(`unexpected fetch: ${url}`);
    }
    const result = responses[key]();
    return {
      ok: result.ok,
      status: result.status ?? (result.ok ? 200 : 500),
      json: result.json,
    } as Response;
  });
  vi.stubGlobal('fetch', fetchMock);
  (fetchMock as unknown as { calls: string[] }).calls = calls;
  return fetchMock;
}

function anilistDto(episode: number, opts?: { seasonDate?: string; episodeDate?: string }) {
  return {
    source: 'anilist',
    externalId: '1',
    mediaType: 'anime',
    title: 'Test Anime',
    url: 'https://anilist.co/anime/1',
    releases: [
      {
        kind: 'season-start',
        label: 'Test Anime',
        date: opts?.seasonDate ?? '2026-01-01',
        source: 'anilist',
      },
      {
        kind: 'episode',
        label: `第${episode}話`,
        date: opts?.episodeDate ?? '2026-01-08',
        source: 'anilist',
      },
    ],
  };
}

function narouDto(generalAllNo: number, generalLastup: string) {
  return {
    source: 'narou',
    ncode: 'n1234a',
    title: 'Test Novel',
    author: 'Author',
    generalAllNo,
    generalLastup,
    completed: false,
    url: 'https://ncode.syosetu.com/n1234a/',
  };
}

// ---------------------------------------------------------------------------
// 1. 追跡中のみ対象
// ---------------------------------------------------------------------------

describe('runSync: target scope', () => {
  it('only syncs editions with status in {plan, active, paused}', async () => {
    const work = await createWork({ title: 'Work' }, db);
    const tracked = await createEdition(
      {
        workId: work.id,
        mediaType: 'anime',
        title: 'Tracked',
        status: 'active',
        sourceRefs: [{ source: 'anilist', externalId: '1', url: 'https://anilist.co/anime/1' }],
      },
      db,
    );
    const done = await createEdition(
      {
        workId: work.id,
        mediaType: 'anime',
        title: 'Done',
        status: 'done',
        sourceRefs: [{ source: 'anilist', externalId: '2', url: 'https://anilist.co/anime/2' }],
      },
      db,
    );
    const dropped = await createEdition(
      {
        workId: work.id,
        mediaType: 'anime',
        title: 'Dropped',
        status: 'dropped',
        sourceRefs: [{ source: 'anilist', externalId: '3', url: 'https://anilist.co/anime/3' }],
      },
      db,
    );

    const fetchMock = installFetchMock({
      '/api/anilist/media/1': () => ({ ok: true, json: async () => anilistDto(5) }),
    });

    const result = await runSync({ database: db });

    expect(result.ran).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect((fetchMock as unknown as { calls: string[] }).calls[0]).toContain('/api/anilist/media/1');

    const trackedReleases = await listReleasesByEdition(tracked.id, db);
    expect(trackedReleases.length).toBeGreaterThan(0);
    const doneReleases = await listReleasesByEdition(done.id, db);
    const droppedReleases = await listReleasesByEdition(dropped.id, db);
    expect(doneReleases).toHaveLength(0);
    expect(droppedReleases).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// 2. 1h スロットルと force
// ---------------------------------------------------------------------------

describe('runSync: throttling', () => {
  it('skips a second run within 1h unless force is passed', async () => {
    const work = await createWork({ title: 'Work' }, db);
    await createEdition(
      {
        workId: work.id,
        mediaType: 'anime',
        title: 'A',
        status: 'active',
        sourceRefs: [{ source: 'anilist', externalId: '1', url: 'https://anilist.co/anime/1' }],
      },
      db,
    );

    installFetchMock({
      '/api/anilist/media/1': () => ({ ok: true, json: async () => anilistDto(5) }),
    });

    let clock = new Date('2026-07-13T00:00:00.000Z');
    const now = () => clock;

    const first = await runSync({ database: db, now });
    expect(first.ran).toBe(true);

    clock = new Date('2026-07-13T00:30:00.000Z'); // 30min later
    const second = await runSync({ database: db, now });
    expect(second.ran).toBe(false);
    expect(second.skipped).toBe('throttled');

    const forced = await runSync({ database: db, now, force: true });
    expect(forced.ran).toBe(true);

    clock = new Date('2026-07-13T02:00:00.000Z'); // >1h later
    const third = await runSync({ database: db, now });
    expect(third.ran).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// 3. 次話ローリング（古い次話が置換される）
// ---------------------------------------------------------------------------

describe('runSync: anime rolling next-episode release', () => {
  it('keeps exactly one episode release, replacing the previous one', async () => {
    const work = await createWork({ title: 'Work' }, db);
    const edition = await createEdition(
      {
        workId: work.id,
        mediaType: 'anime',
        title: 'A',
        status: 'active',
        sourceRefs: [{ source: 'anilist', externalId: '1', url: 'https://anilist.co/anime/1' }],
      },
      db,
    );

    let episode = 5;
    installFetchMock({
      '/api/anilist/media/1': () => ({ ok: true, json: async () => anilistDto(episode) }),
    });

    await runSync({ database: db, force: true });
    let releases = await listReleasesByEdition(edition.id, db);
    let episodeReleases = releases.filter((r) => r.kind === 'episode');
    expect(episodeReleases).toHaveLength(1);
    expect(episodeReleases[0].label).toBe('第5話');

    episode = 6;
    await runSync({ database: db, force: true });
    releases = await listReleasesByEdition(edition.id, db);
    episodeReleases = releases.filter((r) => r.kind === 'episode');
    expect(episodeReleases).toHaveLength(1);
    expect(episodeReleases[0].label).toBe('第6話');
  });
});

// ---------------------------------------------------------------------------
// 4. date 変更 → dateHistory 追記
// ---------------------------------------------------------------------------

describe('runSync: date change tracked as dateHistory', () => {
  it('records the old date in dateHistory when a season-start date is postponed', async () => {
    const work = await createWork({ title: 'Work' }, db);
    const edition = await createEdition(
      {
        workId: work.id,
        mediaType: 'anime',
        title: 'A',
        status: 'active',
        sourceRefs: [{ source: 'anilist', externalId: '1', url: 'https://anilist.co/anime/1' }],
      },
      db,
    );

    let seasonDate = '2026-01-01';
    installFetchMock({
      '/api/anilist/media/1': () => ({
        ok: true,
        json: async () => anilistDto(5, { seasonDate }),
      }),
    });

    await runSync({ database: db, force: true });
    seasonDate = '2026-01-08'; // postponed
    await runSync({ database: db, force: true });

    const releases = await listReleasesByEdition(edition.id, db);
    const seasonStart = releases.find((r) => r.kind === 'season-start');
    expect(seasonStart?.date).toBe('2026-01-08');
    expect(seasonStart?.dateHistory).toEqual([
      expect.objectContaining({ date: '2026-01-01' }),
    ]);
  });
});

// ---------------------------------------------------------------------------
// 5. ソース障害スキップ + エラー記録
// ---------------------------------------------------------------------------

describe('runSync: per-source failure isolation', () => {
  it('skips the failing source only, and records the error', async () => {
    const work = await createWork({ title: 'Work' }, db);
    const animeEdition = await createEdition(
      {
        workId: work.id,
        mediaType: 'anime',
        title: 'Anime',
        status: 'active',
        sourceRefs: [{ source: 'anilist', externalId: '1', url: 'https://anilist.co/anime/1' }],
      },
      db,
    );
    const novelEdition = await createEdition(
      {
        workId: work.id,
        mediaType: 'web-novel',
        title: 'Novel',
        status: 'active',
        sourceRefs: [{ source: 'narou', externalId: 'n1234a', url: 'https://ncode.syosetu.com/n1234a/' }],
      },
      db,
    );

    installFetchMock({
      '/api/anilist/media/1': () => ({ ok: false, status: 502, json: async () => ({ detail: 'boom' }) }),
      '/api/narou/novel/n1234a': () => ({
        ok: true,
        json: async () => narouDto(10, '2026-07-01'),
      }),
    });

    const result = await runSync({ database: db, force: true });

    expect(result.ran).toBe(true);
    expect(result.errors.anilist).toBeDefined();
    expect(result.errors.narou).toBeUndefined();

    const animeReleases = await listReleasesByEdition(animeEdition.id, db);
    expect(animeReleases).toHaveLength(0);

    const novelReleases = await listReleasesByEdition(novelEdition.id, db);
    expect(novelReleases).toHaveLength(1);
    expect(novelReleases[0].label).toBe('第10部分');

    const updatedNovel = await getEdition(novelEdition.id, db);
    expect(updatedNovel?.progress?.total).toBe(10);
  });
});
