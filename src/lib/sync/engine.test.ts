import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createDB, type Edition, type TriptychDB } from '../db/schema';
import {
  createEdition,
  createManualRelease,
  createRelease,
  createWork,
  getEdition,
  listReleasesByEdition,
  updateEditionSyncHints,
  type CreateEditionInput,
} from '../db/store';
import { errorMessage, runSync } from './engine';
import { ApiError } from '../api';

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
// fetch mock
// ---------------------------------------------------------------------------

class HttpFailure {
  constructor(public status: number) {}
}

const fail = (status = 502) => new HttpFailure(status);

type Handler = (url: URL) => unknown;

/** 検知が同期のあとに走るので、検知の照会は既定で空を返す。 */
const DEFAULT_ROUTES: Record<string, Handler> = {
  '/api/anilist/relations/': () => ({ externalId: '0', type: 'ANIME', title: '', relations: [] }),
  '/api/anilist/resolve-source': () => [],
  '/api/anilist/detect': () => [],
  '/api/syoboi/title': () => ({ status: 'queued' }),
};

function mockFetch(routes: Record<string, Handler>): URL[] {
  const all = { ...DEFAULT_ROUTES, ...routes };
  const calls: URL[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: string) => {
      const url = new URL(input, 'http://collector.test');
      calls.push(url);
      const key = Object.keys(all)
        .filter((prefix) => url.pathname.startsWith(prefix))
        .sort((a, b) => b.length - a.length)[0];
      if (!key) throw new Error(`unexpected fetch: ${input}`);
      const reply = all[key](url);
      const status = reply instanceof HttpFailure ? reply.status : 200;
      return { ok: status < 400, status, json: async () => reply } as Response;
    }),
  );
  return calls;
}

const callsTo = (calls: URL[], prefix: string) => calls.filter((u) => u.pathname.startsWith(prefix));

// ---------------------------------------------------------------------------
// fixtures
// ---------------------------------------------------------------------------

const NOW = new Date('2026-10-13T03:00:00.000Z'); // JST 2026-10-13 12:00
const at = (iso: string) => () => new Date(iso);

function mediaDto(
  id: string,
  opts: {
    seasonDate?: string;
    episode?: number;
    episodeDate?: string;
    time?: string;
    status?: string;
    cover?: string | null;
  } = {},
) {
  const releases: unknown[] = [
    {
      kind: 'season-start',
      label: 'テストアニメ',
      date: opts.seasonDate ?? '2026-10-02',
      precision: 'day',
      source: 'anilist',
      externalId: id,
    },
  ];
  if (opts.episode !== undefined) {
    releases.push({
      kind: 'episode',
      label: `第${opts.episode}話`,
      date: opts.episodeDate ?? '2026-10-16',
      precision: 'day',
      time: opts.time ?? '25:30',
      source: 'anilist',
      externalId: `${id}:${opts.episode}`,
    });
  }
  return {
    source: 'anilist',
    externalId: id,
    mediaType: 'anime',
    title: 'テストアニメ',
    url: `https://anilist.co/anime/${id}`,
    ...(opts.status ? { status: opts.status } : {}),
    ...(opts.cover !== undefined ? { cover: opts.cover } : {}),
    releases,
  };
}

function volume(isbn: string, label: string, date: string, precision = 'day') {
  return { kind: 'volume', label, date, precision, source: 'ndl', externalId: isbn };
}

function ndlDto(releases: unknown[], line = '') {
  return { lines: [{ key: line, label: line || '本編', count: releases.length }], line, releases };
}

function webNovelDto(
  source: 'narou' | 'kakuyomu',
  id: string,
  episodeCount: number,
  lastUpdate: string | null,
  completed = false,
) {
  return {
    source,
    externalId: id,
    title: 'テスト小説',
    url: `https://example.test/${id}`,
    episodeCount,
    completed,
    isShortStory: false,
    lastUpdate,
  };
}

async function setup(
  workTitle: string,
  edition: Omit<CreateEditionInput, 'workId'>,
): Promise<Edition> {
  const work = await createWork({ title: workTitle }, db);
  return createEdition({ status: 'active', ...edition, workId: work.id }, db);
}

const anilistRef = (id: string) => ({
  source: 'anilist' as const,
  externalId: id,
  url: `https://anilist.co/anime/${id}`,
});

// ---------------------------------------------------------------------------
// ルーティング
// ---------------------------------------------------------------------------

describe('runSync: mediaType ごとのルーティング', () => {
  it('anime は AniList としょぼかる、文庫・漫画は Work タイトルで NDL、Web 小説はなろう・カクヨムを引く', async () => {
    const anime = await setup('薬屋のひとりごと', {
      mediaType: 'anime',
      title: '薬屋のひとりごと 第3期',
      sourceRefs: [anilistRef('195516')],
    });
    const ln = await setup('本好きの下剋上', {
      mediaType: 'light-novel',
      title: '本好きの下剋上（TOブックス）',
      // 検索から登録した文庫は AniList の NOVEL id だけを持つ（監査 P2）
      sourceRefs: [{ source: 'anilist', externalId: '86355', url: 'https://anilist.co/manga/86355' }],
    });
    const manga = await setup('転生したらスライムだった件', {
      mediaType: 'manga',
      title: '転スラ（漫画）',
    });
    await setup('なろう作品', {
      mediaType: 'web-novel',
      title: 'なろう版',
      sourceRefs: [{ source: 'narou', externalId: 'n4830bu', url: 'https://ncode.syosetu.com/n4830bu/' }],
    });
    await setup('カクヨム作品', {
      mediaType: 'web-novel',
      title: 'カクヨム版',
      sourceRefs: [{ source: 'kakuyomu', externalId: '1177354054880238351', url: 'https://kakuyomu.jp/works/1' }],
    });
    await setup('追跡外', {
      mediaType: 'anime',
      title: '見終わった',
      status: 'done',
      sourceRefs: [anilistRef('999')],
    });

    const calls = mockFetch({
      '/api/anilist/media/': () => mediaDto('195516'),
      '/api/ndl/series': () => ndlDto([]),
      '/api/narou/novel/': () => webNovelDto('narou', 'n4830bu', 10, '2026-10-01'),
      '/api/kakuyomu/work/': () => webNovelDto('kakuyomu', '1177354054880238351', 5, '2026-10-01'),
    });

    const result = await runSync({ database: db, now: () => NOW });

    expect(result.ran).toBe(true);
    expect(result.errors).toEqual({});
    expect(callsTo(calls, '/api/anilist/media/').map((u) => u.pathname)).toEqual([
      '/api/anilist/media/195516',
    ]);
    expect(callsTo(calls, '/api/syoboi/title')).toHaveLength(1);
    expect(callsTo(calls, '/api/syoboi/title')[0].searchParams.get('title')).toBe('薬屋のひとりごと 第3期');

    const ndl = callsTo(calls, '/api/ndl/series').map((u) => Object.fromEntries(u.searchParams));
    expect(ndl).toEqual(
      expect.arrayContaining([
        { title: '本好きの下剋上', kind: 'novel' },
        { title: '転生したらスライムだった件', kind: 'manga' },
      ]),
    );
    expect(ndl).toHaveLength(2);
    expect(callsTo(calls, '/api/narou/novel/n4830bu')).toHaveLength(1);
    expect(callsTo(calls, '/api/kakuyomu/work/1177354054880238351')).toHaveLength(1);

    expect((await listReleasesByEdition(anime.id, db)).length).toBeGreaterThan(0);
    // 文庫・漫画に AniList の Release は作らない
    expect(await listReleasesByEdition(ln.id, db)).toHaveLength(0);
    expect(await listReleasesByEdition(manga.id, db)).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// スロットル
// ---------------------------------------------------------------------------

describe('runSync: 1h スロットルと force', () => {
  it('1h 以内の2回目は飛ばし、force なら実行する', async () => {
    await setup('W', { mediaType: 'anime', title: 'A', sourceRefs: [anilistRef('1')] });
    mockFetch({ '/api/anilist/media/': () => mediaDto('1') });

    let clock = new Date('2026-07-13T00:00:00.000Z');
    const now = () => clock;

    expect((await runSync({ database: db, now })).ran).toBe(true);

    clock = new Date('2026-07-13T00:30:00.000Z');
    const second = await runSync({ database: db, now });
    expect(second).toMatchObject({ ran: false, skipped: 'throttled' });

    expect((await runSync({ database: db, now, force: true })).ran).toBe(true);

    clock = new Date('2026-07-13T02:00:00.000Z');
    expect((await runSync({ database: db, now })).ran).toBe(true);
  });

  it('force（手動同期）では同期と検知の全エンドポイントに refresh=1 を付ける', async () => {
    await setup('アニメ', { mediaType: 'anime', title: 'A', sourceRefs: [anilistRef('1')] });
    await setup('文庫', { mediaType: 'light-novel', title: 'L' });
    await setup('なろう', {
      mediaType: 'web-novel',
      title: 'N',
      sourceRefs: [{ source: 'narou', externalId: 'n1', url: 'u' }],
    });
    await setup('カクヨム', {
      mediaType: 'web-novel',
      title: 'K',
      sourceRefs: [{ source: 'kakuyomu', externalId: 'k1', url: 'u' }],
    });

    const calls = mockFetch({
      '/api/anilist/media/': () => mediaDto('1'),
      '/api/ndl/series': () => ndlDto([]),
      '/api/narou/novel/': () => webNovelDto('narou', 'n1', 1, '2026-10-01'),
      '/api/kakuyomu/work/': () => webNovelDto('kakuyomu', 'k1', 1, '2026-10-01'),
    });

    await runSync({ database: db, force: true, now: () => NOW });

    const paths = new Set(calls.map((u) => u.pathname.replace(/\/[^/]+$/, '/')));
    expect(paths).toEqual(
      new Set([
        '/api/anilist/media/',
        '/api/syoboi/',
        '/api/ndl/',
        '/api/narou/novel/',
        '/api/kakuyomu/work/',
        '/api/anilist/relations/',
        '/api/anilist/',
      ]),
    );
    // しょぼかるだけは Crawl-delay 60 のため手動同期でも refresh を付けない（24h キャッシュを使う）
    for (const url of calls) {
      const expected = url.pathname.startsWith('/api/syoboi/') ? null : '1';
      expect(url.searchParams.get('refresh'), url.href).toBe(expected);
    }
  });

  it('自動同期では refresh を付けない', async () => {
    await setup('アニメ', { mediaType: 'anime', title: 'A', sourceRefs: [anilistRef('1')] });
    const calls = mockFetch({ '/api/anilist/media/': () => mediaDto('1') });

    await runSync({ database: db, now: () => NOW });

    expect(calls.length).toBeGreaterThan(0);
    for (const url of calls) expect(url.searchParams.has('refresh')).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// NDL
// ---------------------------------------------------------------------------

describe('runSync: NDL（文庫・漫画）', () => {
  it('ISBN でマージし、同じ巻ラベルの別 ISBN も別の巻として持ち、繰り返しても日付が往復しない', async () => {
    const ln = await setup('本好きの下剋上', { mediaType: 'light-novel', title: '本好き' });
    const releases = [
      volume('9784867940228', '本好きの下剋上 第三部 領主の養女10', '2026-12-10'),
      volume('9784867940211', '本好きの下剋上 第三部 領主の養女10', '2026-08-01', 'month'),
      volume('9784864723428', '本好きの下剋上 第五部 女神の化身12', '2023-12-09'),
    ];
    mockFetch({ '/api/ndl/series': () => ndlDto(releases) });

    for (let i = 0; i < 3; i += 1) await runSync({ database: db, force: true, now: () => NOW });

    const rows = await listReleasesByEdition(ln.id, db);
    expect(rows).toHaveLength(3);
    const byIsbn = new Map(rows.map((r) => [r.externalId, r]));
    expect(byIsbn.get('9784867940228')).toMatchObject({ date: '2026-12-10', precision: 'day', source: 'ndl' });
    expect(byIsbn.get('9784867940211')).toMatchObject({ date: '2026-08-01', precision: 'month' });
    for (const row of rows) expect(row.dateHistory).toEqual([]);
    // 登録時点で過去の巻は既読、未来の巻は新着
    expect(byIsbn.get('9784864723428')?.seenAt).toBeDefined();
    expect(byIsbn.get('9784867940228')?.seenAt).toBeUndefined();
  });

  it('返ってきた line を syncHints に保存し、以後はその line で引く', async () => {
    const ln = await setup('本好きの下剋上', { mediaType: 'light-novel', title: '本好き' });
    const calls = mockFetch({ '/api/ndl/series': () => ndlDto([], '') });

    await runSync({ database: db, force: true, now: () => NOW });
    expect(callsTo(calls, '/api/ndl/series')[0].searchParams.has('line')).toBe(false);
    expect((await getEdition(ln.id, db))?.syncHints?.ndlLine).toBe('');

    await runSync({ database: db, force: true, now: () => NOW });
    const second = callsTo(calls, '/api/ndl/series')[1];
    expect(second.searchParams.get('line')).toBe('');
  });

  it('syncHints.ndlLine があればそれを渡し、上書きしない', async () => {
    const ln = await setup('本好きの下剋上', { mediaType: 'light-novel', title: '本好き' });
    await updateEditionSyncHints(ln.id, { ndlLine: 'TOジュニア文庫' }, db);
    const calls = mockFetch({ '/api/ndl/series': () => ndlDto([], 'TOジュニア文庫') });

    await runSync({ database: db, force: true, now: () => NOW });

    expect(callsTo(calls, '/api/ndl/series')[0].searchParams.get('line')).toBe('TOジュニア文庫');
    expect((await getEdition(ln.id, db))?.syncHints?.ndlLine).toBe('TOジュニア文庫');
  });

  it('検索で選んだ NDL シリーズの line（externalId の2番目）を初期値にする', async () => {
    const ln = await setup('本好きの下剋上', {
      mediaType: 'light-novel',
      title: '本好き（ジュニア）',
      sourceRefs: [{ source: 'ndl', externalId: 'novel:TOジュニア文庫:本好きの下剋上', url: 'u' }],
    });
    const calls = mockFetch({ '/api/ndl/series': () => ndlDto([], 'TOジュニア文庫') });

    await runSync({ database: db, force: true, now: () => NOW });

    expect(callsTo(calls, '/api/ndl/series')[0].searchParams.get('line')).toBe('TOジュニア文庫');
    expect((await getEdition(ln.id, db))?.syncHints?.ndlLine).toBe('TOジュニア文庫');
  });

  it('本編（line が空）の NDL シリーズも line= として渡す', async () => {
    await setup('本好きの下剋上', {
      mediaType: 'light-novel',
      title: '本好き',
      sourceRefs: [{ source: 'ndl', externalId: 'novel::本好きの下剋上', url: 'u' }],
    });
    const calls = mockFetch({ '/api/ndl/series': () => ndlDto([], '') });

    await runSync({ database: db, force: true, now: () => NOW });

    expect(callsTo(calls, '/api/ndl/series')[0].searchParams.get('line')).toBe('');
  });

  it('何も見つからない時は line を保存しない', async () => {
    const ln = await setup('新作', { mediaType: 'manga', title: '新作' });
    mockFetch({ '/api/ndl/series': () => ({ lines: [], line: '', releases: [] }) });

    await runSync({ database: db, force: true, now: () => NOW });

    expect((await getEdition(ln.id, db))?.syncHints).toBeUndefined();
  });

  it('旧ラベル照合時代の行（externalId の無い同期 volume）を消し、手動の巻は残す', async () => {
    const ln = await setup('本好きの下剋上', { mediaType: 'light-novel', title: '本好き' });
    await createRelease(
      { editionId: ln.id, kind: 'volume', label: '本好きの下剋上 10', date: '2026-08-25', source: 'ndl' },
      db,
    );
    await createManualRelease(
      { editionId: ln.id, kind: 'volume', label: '特装版', date: '2026-09-01' },
      db,
    );
    mockFetch({
      '/api/ndl/series': () => ndlDto([volume('9784867940228', '本好きの下剋上 10', '2026-12-10')]),
    });

    await runSync({ database: db, force: true, now: () => NOW });

    const rows = await listReleasesByEdition(ln.id, db);
    expect(rows.map((r) => [r.label, r.externalId, r.origin]).sort()).toEqual(
      [
        ['本好きの下剋上 10', '9784867940228', 'sync'],
        ['特装版', undefined, 'manual'],
      ].sort(),
    );
  });

  it('日付の不正な巻があっても、他の巻は取り込みエラーにもしない', async () => {
    const ln = await setup('本好きの下剋上', { mediaType: 'light-novel', title: '本好き' });
    mockFetch({
      '/api/ndl/series': () =>
        ndlDto([
          volume('9784000000001', '壊れた日付', '2026-02-30'),
          volume('9784867940228', '正しい巻', '2026-12-10'),
        ]),
    });

    const result = await runSync({ database: db, force: true, now: () => NOW });

    expect(result.errors.ndl).toBeUndefined();
    const rows = await listReleasesByEdition(ln.id, db);
    expect(rows.map((r) => r.label)).toEqual(['正しい巻']);
  });
});

// ---------------------------------------------------------------------------
// アニメの次話（ローリング）
// ---------------------------------------------------------------------------

describe('runSync: アニメの次話ローリング', () => {
  it('次話は常に1件で、作成時から既読（season-start は未来なら新着）', async () => {
    const anime = await setup('W', { mediaType: 'anime', title: 'A', sourceRefs: [anilistRef('1')] });
    let episode = 2;
    let episodeDate = '2026-10-16';
    mockFetch({
      '/api/anilist/media/': () =>
        mediaDto('1', { seasonDate: '2026-10-30', episode, episodeDate, time: '25:30' }),
    });

    await runSync({ database: db, force: true, now: () => NOW });
    let episodes = (await listReleasesByEdition(anime.id, db)).filter((r) => r.kind === 'episode');
    expect(episodes).toHaveLength(1);
    expect(episodes[0]).toMatchObject({
      label: '第2話',
      externalId: '1:2',
      date: '2026-10-16',
      time: '25:30',
      origin: 'sync',
    });
    expect(episodes[0].seenAt).toBeDefined();

    const seasonStart = (await listReleasesByEdition(anime.id, db)).find((r) => r.kind === 'season-start');
    expect(seasonStart).toMatchObject({ externalId: '1', date: '2026-10-30' });
    expect(seasonStart?.seenAt).toBeUndefined();

    episode = 3;
    episodeDate = '2026-10-23';
    await runSync({ database: db, force: true, now: at('2026-10-17T03:00:00Z') });
    episodes = (await listReleasesByEdition(anime.id, db)).filter((r) => r.kind === 'episode');
    expect(episodes).toHaveLength(1);
    expect(episodes[0]).toMatchObject({ label: '第3話', externalId: '1:3', date: '2026-10-23' });
    expect(episodes[0].seenAt).toBeDefined();
    expect(episodes[0].dateHistory).toEqual([]);
  });

  it('同じ話の放送日が変われば延期として記録する', async () => {
    const anime = await setup('W', { mediaType: 'anime', title: 'A', sourceRefs: [anilistRef('1')] });
    let episodeDate = '2026-10-16';
    mockFetch({ '/api/anilist/media/': () => mediaDto('1', { episode: 2, episodeDate }) });

    await runSync({ database: db, force: true, now: () => NOW });
    episodeDate = '2026-10-23';
    await runSync({ database: db, force: true, now: () => NOW });

    const [ep] = (await listReleasesByEdition(anime.id, db)).filter((r) => r.kind === 'episode');
    expect(ep.date).toBe('2026-10-23');
    expect(ep.dateHistory).toEqual([expect.objectContaining({ date: '2026-10-16' })]);
  });

  it('放送終了（次話なし）で次話の Release を消し、手動の話と旧行は扱いを分ける', async () => {
    const anime = await setup('W', { mediaType: 'anime', title: 'A', sourceRefs: [anilistRef('1')] });
    // 旧エンジンの行（externalId なし）
    await createRelease(
      { editionId: anime.id, kind: 'episode', label: '第11話', date: '2026-10-09', source: 'anilist' },
      db,
    );
    await createManualRelease(
      { editionId: anime.id, kind: 'episode', label: '特別編', date: '2026-12-24' },
      db,
    );
    let airing = true;
    mockFetch({
      '/api/anilist/media/': () => mediaDto('1', airing ? { episode: 12 } : {}),
    });

    await runSync({ database: db, force: true, now: () => NOW });
    let episodes = (await listReleasesByEdition(anime.id, db)).filter((r) => r.kind === 'episode');
    expect(episodes.map((r) => r.label).sort()).toEqual(['特別編', '第12話']);

    airing = false;
    await runSync({ database: db, force: true, now: () => NOW });
    episodes = (await listReleasesByEdition(anime.id, db)).filter((r) => r.kind === 'episode');
    expect(episodes.map((r) => r.label)).toEqual(['特別編']);
  });
});

// ---------------------------------------------------------------------------
// しょぼいカレンダー
// ---------------------------------------------------------------------------

describe('runSync: しょぼいカレンダー', () => {
  const streamStart = (date: string, chid = '232') => ({
    kind: 'stream-start',
    label: 'dアニメストア 配信',
    date,
    precision: 'day',
    time: '24:00',
    source: 'syoboi',
    externalId: `7667:${chid}`,
  });

  it('queued（取得待ち）はエラーにせず黙って飛ばす', async () => {
    const anime = await setup('薬屋のひとりごと', {
      mediaType: 'anime',
      title: '薬屋のひとりごと 第3期',
      sourceRefs: [anilistRef('195516')],
    });
    mockFetch({
      '/api/anilist/media/': () => mediaDto('195516'),
      '/api/syoboi/title': () => ({ status: 'queued', tid: null, programs: [], streamStarts: [] }),
    });

    const result = await runSync({ database: db, force: true, now: () => NOW });

    expect(result.errors).toEqual({});
    const rows = await listReleasesByEdition(anime.id, db);
    expect(rows.filter((r) => r.kind === 'stream-start')).toHaveLength(0);
    expect((await getEdition(anime.id, db))?.syncHints).toBeUndefined();
  });

  it('ok なら配信開始を付け、TID を保存して次回は TID で引く', async () => {
    const anime = await setup('薬屋のひとりごと', {
      mediaType: 'anime',
      title: '薬屋のひとりごと 第3期',
      sourceRefs: [anilistRef('195516')],
    });
    const calls = mockFetch({
      '/api/anilist/media/': () => mediaDto('195516'),
      '/api/syoboi/title': () => ({
        status: 'ok',
        tid: '7667',
        title: '薬屋のひとりごと 第3期',
        programs: [],
        streamStarts: [streamStart('2026-10-17')],
      }),
    });

    await runSync({ database: db, force: true, now: () => NOW });

    const [stream] = (await listReleasesByEdition(anime.id, db)).filter((r) => r.kind === 'stream-start');
    expect(stream).toMatchObject({
      label: 'dアニメストア 配信',
      date: '2026-10-17',
      time: '24:00',
      source: 'syoboi',
      externalId: '7667:232',
    });
    expect((await getEdition(anime.id, db))?.syncHints?.syoboiTid).toBe('7667');

    await runSync({ database: db, force: true, now: () => NOW });
    const second = callsTo(calls, '/api/syoboi/title')[1];
    expect(second.searchParams.get('tid')).toBe('7667');
    expect(second.searchParams.has('title')).toBe(false);
  });

  it('配信が始まった後は、翌週の番組で配信開始の日付を動かさない', async () => {
    const anime = await setup('W', { mediaType: 'anime', title: 'A', sourceRefs: [anilistRef('1')] });
    let date = '2026-10-17';
    mockFetch({
      '/api/anilist/media/': () => mediaDto('1'),
      '/api/syoboi/title': () => ({ status: 'ok', tid: '7667', streamStarts: [streamStart(date)] }),
    });

    await runSync({ database: db, force: true, now: () => NOW });
    date = '2026-10-24';
    await runSync({ database: db, force: true, now: at('2026-10-20T03:00:00Z') });

    const [stream] = (await listReleasesByEdition(anime.id, db)).filter((r) => r.kind === 'stream-start');
    expect(stream.date).toBe('2026-10-17');
    expect(stream.dateHistory).toEqual([]);
  });

  describe('放送状態による扱い', () => {
    const streamRows = async (editionId: string) =>
      (await listReleasesByEdition(editionId, db)).filter((r) => r.kind === 'stream-start');
    const okSyoboi = (streams: unknown[], startedStreams?: string[]) => () => ({
      status: 'ok',
      tid: '7667',
      streamStarts: streams,
      ...(startedStreams ? { startedStreams } : {}),
    });

    it('放送終了（FINISHED）の Edition はしょぼかるを照会しない（AniList は取り込む）', async () => {
      const anime = await setup('W', { mediaType: 'anime', title: 'A', sourceRefs: [anilistRef('1')] });
      const calls = mockFetch({
        '/api/anilist/media/': () => mediaDto('1', { status: 'FINISHED', seasonDate: '2026-04-04' }),
        '/api/syoboi/title': okSyoboi([streamStart('2026-10-17')]),
      });

      const result = await runSync({ database: db, force: true, now: () => NOW });

      expect(result.errors).toEqual({});
      expect(callsTo(calls, '/api/syoboi/title')).toHaveLength(0);
      expect((await listReleasesByEdition(anime.id, db)).map((r) => r.kind)).toEqual(['season-start']);
    });

    it('中止（CANCELLED）も照会せず、AniList が取れなかった・まだ放送中の id が残る Edition は照会する', async () => {
      await setup('中止', { mediaType: 'anime', title: 'C', sourceRefs: [anilistRef('1')] });
      await setup('失敗', { mediaType: 'anime', title: 'F', sourceRefs: [anilistRef('2')] });
      await setup('混在', { mediaType: 'anime', title: 'M', sourceRefs: [anilistRef('3'), anilistRef('4')] });
      const status: Record<string, string> = { '1': 'CANCELLED', '3': 'FINISHED', '4': 'RELEASING' };
      const calls = mockFetch({
        '/api/anilist/media/': (url) => {
          const id = url.pathname.split('/').pop() ?? '';
          return id === '2' ? fail(502) : mediaDto(id, { status: status[id] });
        },
      });

      await runSync({ database: db, force: true, now: () => NOW });

      expect(callsTo(calls, '/api/syoboi/title').map((u) => u.searchParams.get('title')).sort()).toEqual(['F', 'M']);
    });

    it('配信中のサービス（startedStreams）は「次回の配信」1件のローリング: 既読で作り、日付は延期にせず動かす', async () => {
      const anime = await setup('W', { mediaType: 'anime', title: 'A', sourceRefs: [anilistRef('1')] });
      let date = '2026-10-16';
      mockFetch({
        '/api/anilist/media/': () => mediaDto('1', { status: 'RELEASING', episode: 3 }),
        '/api/syoboi/title': () => okSyoboi([streamStart(date)], ['7667:232'])(),
      });

      await runSync({ database: db, force: true, now: () => NOW });
      const [first] = await streamRows(anime.id);
      expect(first).toMatchObject({ date: '2026-10-16', time: '24:00', rolling: true, origin: 'sync' });
      expect(first.seenAt).toBeDefined();

      date = '2026-10-23';
      await runSync({ database: db, force: true, now: at('2026-10-20T03:00:00Z') });
      const rows = await streamRows(anime.id);
      expect(rows).toHaveLength(1);
      expect(rows[0]).toMatchObject({ id: first.id, date: '2026-10-23', rolling: true, origin: 'sync', dateHistory: [] });
      expect(rows[0].seenAt).toBeDefined();
    });

    it('放送中でも、まだ配信の無いサービス（後から始まる配信）は配信開始として新着にする', async () => {
      const anime = await setup('W', { mediaType: 'anime', title: 'A', sourceRefs: [anilistRef('1')] });
      mockFetch({
        '/api/anilist/media/': () => mediaDto('1', { status: 'RELEASING', episode: 3 }),
        '/api/syoboi/title': okSyoboi([streamStart('2026-10-16', '239'), streamStart('2026-10-20', '256')], [
          '7667:239',
        ]),
      });

      await runSync({ database: db, force: true, now: () => NOW });

      const rows = await streamRows(anime.id);
      const byId = new Map(rows.map((r) => [r.externalId, r]));
      expect(byId.get('7667:239')).toMatchObject({ rolling: true });
      expect(byId.get('7667:239')?.seenAt).toBeDefined();
      expect(byId.get('7667:256')?.rolling).toBeUndefined();
      expect(byId.get('7667:256')?.seenAt).toBeUndefined();
    });

    it('startedStreams の無い古い応答では、放送中（RELEASING）なら配信中とみなし、放送前なら配信開始のまま', async () => {
      const releasing = await setup('R', { mediaType: 'anime', title: 'R', sourceRefs: [anilistRef('1')] });
      const upcoming = await setup('U', { mediaType: 'anime', title: 'U', sourceRefs: [anilistRef('2')] });
      mockFetch({
        '/api/anilist/media/': (url) =>
          url.pathname.endsWith('/1')
            ? mediaDto('1', { status: 'RELEASING' })
            : mediaDto('2', { status: 'NOT_YET_RELEASED', seasonDate: '2026-10-30' }),
        '/api/syoboi/title': okSyoboi([streamStart('2026-10-17')]),
      });

      await runSync({ database: db, force: true, now: () => NOW });

      const [r] = await streamRows(releasing.id);
      expect(r).toMatchObject({ rolling: true });
      expect(r.seenAt).toBeDefined();
      const [u] = await streamRows(upcoming.id);
      expect(u.rolling).toBeUndefined();
      expect(u.seenAt).toBeUndefined();
    });

    it('配信開始として作った行（旧同期の新着）は、配信中になったら既読のローリング1件に置き換える', async () => {
      const anime = await setup('W', { mediaType: 'anime', title: 'A', sourceRefs: [anilistRef('1')] });
      await createRelease(
        {
          editionId: anime.id,
          kind: 'stream-start',
          label: 'dアニメストア 配信',
          date: '2026-10-16',
          time: '24:00',
          externalId: '7667:232',
          source: 'syoboi',
        },
        db,
      );
      mockFetch({
        '/api/anilist/media/': () => mediaDto('1', { status: 'RELEASING' }),
        '/api/syoboi/title': okSyoboi([streamStart('2026-10-16')], ['7667:232']),
      });

      await runSync({ database: db, force: true, now: () => NOW });

      const rows = await streamRows(anime.id);
      expect(rows).toHaveLength(1);
      expect(rows[0]).toMatchObject({ externalId: '7667:232', date: '2026-10-16', rolling: true, dateHistory: [] });
      expect(rows[0].seenAt).toBeDefined();
    });

    it('ローリングの行は、同じサービスの別チャンネルになっても1件のまま追い、配信が途切れても延期にしない', async () => {
      const anime = await setup('W', { mediaType: 'anime', title: 'A', sourceRefs: [anilistRef('1')] });
      let reply = okSyoboi([streamStart('2026-10-16', '239')], ['7667:239']);
      mockFetch({
        '/api/anilist/media/': () => mediaDto('1', { status: 'RELEASING' }),
        '/api/syoboi/title': () => reply(),
      });

      await runSync({ database: db, force: true, now: () => NOW });
      reply = okSyoboi([streamStart('2026-10-23', '244')], ['7667:244']);
      await runSync({ database: db, force: true, now: at('2026-10-20T03:00:00Z') });
      // 3週休んで直近14日に配信が無い（startedStreams に入らない）
      reply = okSyoboi([streamStart('2026-11-20', '244')], []);
      await runSync({ database: db, force: true, now: at('2026-11-12T03:00:00Z') });

      const rows = await streamRows(anime.id);
      expect(rows).toHaveLength(1);
      expect(rows[0]).toMatchObject({ externalId: '7667:244', date: '2026-11-20', rolling: true, dateHistory: [] });
      expect(rows[0].seenAt).toBeDefined();
    });

    it('手動の配信行は触らない', async () => {
      const anime = await setup('W', { mediaType: 'anime', title: 'A', sourceRefs: [anilistRef('1')] });
      await createManualRelease(
        { editionId: anime.id, kind: 'stream-start', label: 'dアニメストア 配信', date: '2026-10-02', externalId: '7667:232' },
        db,
      );
      mockFetch({
        '/api/anilist/media/': () => mediaDto('1', { status: 'RELEASING' }),
        '/api/syoboi/title': okSyoboi([streamStart('2026-10-16')], ['7667:232']),
      });

      await runSync({ database: db, force: true, now: () => NOW });

      const rows = await streamRows(anime.id);
      expect(rows.map((r) => [r.origin, r.date, r.rolling])).toEqual([['manual', '2026-10-02', undefined]]);
    });
  });

  it('しょぼかるの失敗は syoboi のエラーとして記録し、AniList 側は取り込む', async () => {
    const anime = await setup('W', { mediaType: 'anime', title: 'A', sourceRefs: [anilistRef('1')] });
    mockFetch({
      '/api/anilist/media/': () => mediaDto('1', { episode: 2 }),
      '/api/syoboi/title': () => fail(502),
    });

    const result = await runSync({ database: db, force: true, now: () => NOW });

    expect(result.errors.syoboi?.message).toContain('相手先が応答しませんでした（502）');
    expect(result.errors.anilist).toBeUndefined();
    expect((await listReleasesByEdition(anime.id, db)).length).toBe(2);
  });
});

// ---------------------------------------------------------------------------
// Web 小説
// ---------------------------------------------------------------------------

describe('runSync: Web 小説（なろう・カクヨム）', () => {
  const narouRef = { source: 'narou' as const, externalId: 'n4830bu', url: 'https://ncode.syosetu.com/n4830bu/' };

  it('初回は current を変えずに progress を作り、最新話を既読の1件で持つ', async () => {
    const novel = await setup('本好きの下剋上', { mediaType: 'web-novel', title: 'なろう版', sourceRefs: [narouRef] });
    mockFetch({ '/api/narou/novel/': () => webNovelDto('narou', 'n4830bu', 677, '2026-10-12', true) });

    await runSync({ database: db, force: true, now: () => NOW });

    const edition = await getEdition(novel.id, db);
    expect(edition?.progress).toEqual({ unit: 'part', current: 0, total: 677 });
    expect(edition?.completed).toBe(true);
    const rows = await listReleasesByEdition(novel.id, db);
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({
      kind: 'episode',
      label: '第677部分',
      date: '2026-10-12',
      externalId: 'narou:n4830bu:latest',
      source: 'narou',
    });
    expect(rows[0].seenAt).toBeDefined();
  });

  it('話数が増えた時だけ、作り直して新着にする（延期として記録しない）', async () => {
    const novel = await setup('W', { mediaType: 'web-novel', title: 'なろう版', sourceRefs: [narouRef] });
    let count = 10;
    let lastUpdate = '2026-10-01';
    mockFetch({ '/api/narou/novel/': () => webNovelDto('narou', 'n4830bu', count, lastUpdate) });

    await runSync({ database: db, force: true, now: () => NOW });
    const first = (await listReleasesByEdition(novel.id, db))[0];

    // 話数が同じで日付だけ変わった: 新着にも延期にもしない
    lastUpdate = '2026-10-05';
    await runSync({ database: db, force: true, now: () => NOW });
    let rows = await listReleasesByEdition(novel.id, db);
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({ id: first.id, date: '2026-10-05', origin: 'sync', dateHistory: [] });
    expect(rows[0].seenAt).toBeDefined();

    count = 11;
    lastUpdate = '2026-10-12';
    await runSync({ database: db, force: true, now: () => NOW });
    rows = await listReleasesByEdition(novel.id, db);
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({ label: '第11部分', date: '2026-10-12', dateHistory: [] });
    expect(rows[0].seenAt).toBeUndefined();
    expect(rows[0].id).not.toBe(first.id);
  });

  it('既存の current と unit は変えず、total だけ更新する', async () => {
    const novel = await setup('W', {
      mediaType: 'web-novel',
      title: 'カクヨム版',
      sourceRefs: [{ source: 'kakuyomu', externalId: '1177354054880238351', url: 'u' }],
      progress: { unit: 'volume', current: 5, total: 10 },
    });
    mockFetch({
      '/api/kakuyomu/work/': () => webNovelDto('kakuyomu', '1177354054880238351', 120, '2026-10-12'),
    });

    await runSync({ database: db, force: true, now: () => NOW });

    expect((await getEdition(novel.id, db))?.progress).toEqual({ unit: 'volume', current: 5, total: 120 });
    const [row] = await listReleasesByEdition(novel.id, db);
    expect(row).toMatchObject({ label: '第120話', externalId: 'kakuyomu:1177354054880238351:latest' });
    expect((await getEdition(novel.id, db))?.completed).toBe(false);
  });

  it('旧エンジンの `第N部分` 行は掃除し、話数が増えていれば新着の1件にまとめる', async () => {
    const novel = await setup('W', {
      mediaType: 'web-novel',
      title: 'なろう版',
      sourceRefs: [narouRef],
      progress: { unit: 'part', current: 9, total: 9 },
    });
    for (const n of [8, 9]) {
      await createRelease(
        { editionId: novel.id, kind: 'episode', label: `第${n}部分`, date: '2026-09-0' + n, source: 'narou' },
        db,
      );
    }
    mockFetch({ '/api/narou/novel/': () => webNovelDto('narou', 'n4830bu', 10, '2026-10-10') });

    await runSync({ database: db, force: true, now: () => NOW });

    const rows = await listReleasesByEdition(novel.id, db);
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({ label: '第10部分', externalId: 'narou:n4830bu:latest' });
    expect(rows[0].seenAt).toBeUndefined();
    expect((await getEdition(novel.id, db))?.progress).toEqual({ unit: 'part', current: 9, total: 10 });
  });

  it('手動の話は触らない', async () => {
    const novel = await setup('W', { mediaType: 'web-novel', title: 'なろう版', sourceRefs: [narouRef] });
    await createManualRelease(
      { editionId: novel.id, kind: 'episode', label: '書籍化記念SS', date: '2026-10-01' },
      db,
    );
    mockFetch({ '/api/narou/novel/': () => webNovelDto('narou', 'n4830bu', 10, '2026-10-10') });

    await runSync({ database: db, force: true, now: () => NOW });

    const labels = (await listReleasesByEdition(novel.id, db)).map((r) => r.label).sort();
    expect(labels).toEqual(['第10部分', '書籍化記念SS'].sort());
  });
});

// ---------------------------------------------------------------------------
// 障害と再試行
// ---------------------------------------------------------------------------

describe('runSync: 障害と再試行', () => {
  it('失敗したソースだけを飛ばして記録する', async () => {
    const anime = await setup('W', { mediaType: 'anime', title: 'A', sourceRefs: [anilistRef('1')] });
    const novel = await setup('N', {
      mediaType: 'web-novel',
      title: 'なろう版',
      sourceRefs: [{ source: 'narou', externalId: 'n1', url: 'u' }],
    });
    mockFetch({
      '/api/anilist/media/': () => fail(502),
      '/api/narou/novel/': () => webNovelDto('narou', 'n1', 10, '2026-07-01'),
    });

    const result = await runSync({ database: db, force: true, now: () => NOW });

    expect(result.errors.anilist?.message).toContain('相手先が応答しませんでした（502）');
    expect(result.errors.narou).toBeUndefined();
    expect(await listReleasesByEdition(anime.id, db)).toHaveLength(0);
    expect(await listReleasesByEdition(novel.id, db)).toHaveLength(1);
    expect(result.lastSyncAt).toBe(NOW.toISOString());
  });

  it('全ソースが失敗したら lastSyncAt を進めず、次の起動で再試行する', async () => {
    await setup('W', { mediaType: 'anime', title: 'A', sourceRefs: [anilistRef('1')] });
    await setup('L', { mediaType: 'light-novel', title: 'L' });
    let down = false;
    mockFetch({
      '/api/anilist/media/': () => (down ? fail(503) : mediaDto('1')),
      '/api/syoboi/title': () => (down ? fail(503) : { status: 'queued' }),
      '/api/ndl/series': () => (down ? fail(503) : ndlDto([])),
    });

    const t0 = '2026-10-13T00:00:00.000Z';
    await runSync({ database: db, now: at(t0) });

    down = true;
    const failed = await runSync({ database: db, now: at('2026-10-13T02:00:00.000Z') });
    expect(failed.ran).toBe(true);
    expect(failed.lastSyncAt).toBe(t0);
    expect(Object.keys(failed.errors)).toEqual(expect.arrayContaining(['anilist', 'syoboi', 'ndl']));
    const meta = (await db.meta.get('sync'))?.value as { lastSyncAt?: string; lastSyncErrors?: object };
    expect(meta.lastSyncAt).toBe(t0);
    expect(meta.lastSyncErrors).toEqual(failed.errors);

    // スロットルに掛からず再試行される
    down = false;
    const retried = await runSync({ database: db, now: at('2026-10-13T02:10:00.000Z') });
    expect(retried.ran).toBe(true);
    expect(retried.lastSyncAt).toBe('2026-10-13T02:10:00.000Z');
  });

  it('検知のエラーを errors.detect に入れ、その Work の検知スロットルは進めない', async () => {
    const ln = await setup('薬屋のひとりごと', { mediaType: 'light-novel', title: '薬屋' });
    mockFetch({
      '/api/ndl/series': () => ndlDto([]),
      '/api/anilist/resolve-source': () => fail(502),
    });

    const result = await runSync({ database: db, force: true, now: () => NOW });

    expect(result.errors.ndl).toBeUndefined();
    expect(result.errors.detect?.message).toContain('薬屋のひとりごと');
    expect(result.errors.detect?.message).toContain('502');
    const throttle = (await db.meta.get('detectionThrottle'))?.value as Record<string, string>;
    expect(throttle[ln.workId]).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// 表紙（AniList）
// ---------------------------------------------------------------------------

describe('runSync: アニメの表紙', () => {
  const COVER = 'https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx1.jpg';
  const NEW_COVER = 'https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx1-v2.jpg';
  const OLD_STAMP = '2000-01-01T00:00:00.000Z';

  const run = () => runSync({ database: db, force: true, now: () => NOW });
  const hints = async (id: string) => (await getEdition(id, db))?.syncHints;
  /** updatedAt を過去に固定して、同期が書き込んだかどうかを見分ける。 */
  const freeze = async (id: string) => {
    await db.editions.update(id, { updatedAt: OLD_STAMP });
  };

  it('表紙の無い Edition に AniList の表紙を入れ、coverFrom に anilist を立てる', async () => {
    const anime = await setup('W', { mediaType: 'anime', title: 'A', sourceRefs: [anilistRef('1')] });
    mockFetch({ '/api/anilist/media/': () => mediaDto('1', { cover: COVER }) });

    const result = await run();

    expect(result.errors).toEqual({});
    const saved = await getEdition(anime.id, db);
    expect(saved?.coverUrl).toBe(COVER);
    expect(saved?.syncHints).toEqual({ coverFrom: 'anilist' });
  });

  it('coverFrom が anilist の表紙は、AniList 側が変われば追従する', async () => {
    const anime = await setup('W', {
      mediaType: 'anime',
      title: 'A',
      sourceRefs: [anilistRef('1')],
      coverUrl: COVER,
    });
    await updateEditionSyncHints(anime.id, { coverFrom: 'anilist' }, db);
    mockFetch({ '/api/anilist/media/': () => mediaDto('1', { cover: NEW_COVER }) });

    await run();

    const saved = await getEdition(anime.id, db);
    expect(saved?.coverUrl).toBe(NEW_COVER);
    expect(saved?.syncHints?.coverFrom).toBe('anilist');
  });

  it('手で設定した表紙（coverFrom が無いまま coverUrl がある）は上書きしない', async () => {
    const anime = await setup('W', {
      mediaType: 'anime',
      title: 'A',
      sourceRefs: [anilistRef('1')],
      coverUrl: 'https://example.test/mine.jpg',
    });
    mockFetch({ '/api/anilist/media/': () => mediaDto('1', { cover: COVER }) });

    await run();

    const saved = await getEdition(anime.id, db);
    expect(saved?.coverUrl).toBe('https://example.test/mine.jpg');
    expect(saved?.syncHints).toBeUndefined();
  });

  it('本人が外した表紙（coverCleared）は入れ直さない', async () => {
    const anime = await setup('W', { mediaType: 'anime', title: 'A', sourceRefs: [anilistRef('1')] });
    await updateEditionSyncHints(anime.id, { coverCleared: true }, db);
    mockFetch({ '/api/anilist/media/': () => mediaDto('1', { cover: COVER }) });

    await run();

    const saved = await getEdition(anime.id, db);
    expect(saved?.coverUrl).toBeUndefined();
    expect(saved?.syncHints).toEqual({ coverCleared: true });
  });

  it('カタログから借りた表紙（coverFrom が catalog）も上書きしない', async () => {
    const anime = await setup('W', {
      mediaType: 'anime',
      title: 'A',
      sourceRefs: [anilistRef('1')],
      coverUrl: 'https://example.test/catalog.jpg',
    });
    await updateEditionSyncHints(anime.id, { coverFrom: 'catalog' }, db);
    mockFetch({ '/api/anilist/media/': () => mediaDto('1', { cover: COVER }) });

    await run();

    const saved = await getEdition(anime.id, db);
    expect(saved?.coverUrl).toBe('https://example.test/catalog.jpg');
    expect(saved?.syncHints?.coverFrom).toBe('catalog');
  });

  it('値が同じなら書き込まない', async () => {
    const anime = await setup('W', { mediaType: 'anime', title: 'A', sourceRefs: [anilistRef('1')] });
    mockFetch({ '/api/anilist/media/': () => mediaDto('1', { cover: COVER }) });
    await run();
    await freeze(anime.id);

    await run();

    expect((await getEdition(anime.id, db))?.updatedAt).toBe(OLD_STAMP);
  });

  it('http(s) 以外の表紙は入れない', async () => {
    const anime = await setup('W', { mediaType: 'anime', title: 'A', sourceRefs: [anilistRef('1')] });
    for (const bad of ['javascript:alert(1)', 'data:image/png;base64,AAAA', 'not a url', '  ']) {
      mockFetch({ '/api/anilist/media/': () => mediaDto('1', { cover: bad }) });
      await run();
      const saved = await getEdition(anime.id, db);
      expect(saved?.coverUrl).toBeUndefined();
      expect(saved?.syncHints).toBeUndefined();
    }
  });

  it('AniList に表紙が無い（null・未指定）時は、入っている表紙を消さない', async () => {
    const anime = await setup('W', {
      mediaType: 'anime',
      title: 'A',
      sourceRefs: [anilistRef('1')],
      coverUrl: COVER,
    });
    await updateEditionSyncHints(anime.id, { coverFrom: 'anilist' }, db);

    mockFetch({ '/api/anilist/media/': () => mediaDto('1', { cover: null }) });
    await run();
    mockFetch({ '/api/anilist/media/': () => mediaDto('1') });
    await run();

    const saved = await getEdition(anime.id, db);
    expect(saved?.coverUrl).toBe(COVER);
    expect(saved?.syncHints?.coverFrom).toBe('anilist');
  });

  it('AniList の id が複数ある Edition は、先頭の id の表紙だけを使う（同期のたびに入れ替わらない）', async () => {
    const anime = await setup('W', {
      mediaType: 'anime',
      title: 'A',
      sourceRefs: [anilistRef('1'), anilistRef('2')],
    });
    mockFetch({
      '/api/anilist/media/': (url) =>
        mediaDto(url.pathname.split('/').pop() ?? '', { cover: url.pathname.endsWith('/1') ? COVER : NEW_COVER }),
    });

    await run();
    await run();

    expect((await getEdition(anime.id, db))?.coverUrl).toBe(COVER);
  });

  it('文庫・漫画・Web 小説は AniList の表紙を取り込まない（アニメだけ）', async () => {
    const manga = await setup('M', {
      mediaType: 'manga',
      title: 'M',
      sourceRefs: [{ source: 'anilist', externalId: '5', url: 'https://anilist.co/manga/5' }],
    });
    mockFetch({
      '/api/anilist/media/': () => mediaDto('5', { cover: COVER }),
      '/api/ndl/series': () => ndlDto([]),
    });

    await run();

    expect((await getEdition(manga.id, db))?.coverUrl).toBeUndefined();
  });

  it('表紙を入れても、TID などほかの syncHints を消さない', async () => {
    const anime = await setup('W', { mediaType: 'anime', title: 'A', sourceRefs: [anilistRef('1')] });
    await updateEditionSyncHints(anime.id, { ndlLine: 'x' }, db);
    mockFetch({
      '/api/anilist/media/': () => mediaDto('1', { cover: COVER }),
      '/api/syoboi/title': () => ({ status: 'ok', tid: '7667', programs: [], streamStarts: [] }),
    });

    await run();

    expect(await hints(anime.id)).toEqual({ ndlLine: 'x', coverFrom: 'anilist', syoboiTid: '7667' });
  });
});

// ---------------------------------------------------------------------------
// 番組表（しょぼかる）
// ---------------------------------------------------------------------------

describe('runSync: しょぼかるの番組表', () => {
  const OLD_STAMP = '2000-01-01T00:00:00.000Z';
  const program = (channel: string, date: string, time: string, extra: Record<string, unknown> = {}) => ({
    channel,
    isStreaming: false,
    date,
    time,
    episodeCount: null,
    ...extra,
  });
  const okPrograms = (programs: unknown[]) => () => ({
    status: 'ok',
    tid: '7667',
    title: 'A',
    programs,
    streamStarts: [],
  });
  const broadcasts = async (id: string) => (await getEdition(id, db))?.syncHints?.broadcasts;
  const run = (now: () => Date = () => NOW) => runSync({ database: db, force: true, now });

  async function anime() {
    return setup('W', { mediaType: 'anime', title: 'A', sourceRefs: [anilistRef('1')] });
  }

  it('今日（JST）以降の番組を日時順に syncHints.broadcasts へ保存する', async () => {
    const edition = await anime();
    mockFetch({
      '/api/anilist/media/': () => mediaDto('1'),
      '/api/syoboi/title': okPrograms([
        program('ABEMA', '2026-10-14', '24:00', { isStreaming: true, episodeCount: 2 }),
        program('TOKYO MX', '2026-10-13', '25:30', { episodeCount: 1 }),
        program('MBS', '2026-10-12', '26:00', { episodeCount: 0 }), // 昨日までは入れない
        program('TOKYO MX', '2026-10-14', '23:00'),
      ]),
    });

    const result = await run();

    expect(result.errors).toEqual({});
    expect(await broadcasts(edition.id)).toEqual([
      { channel: 'TOKYO MX', date: '2026-10-13', time: '25:30', isStreaming: false, episode: 1 },
      { channel: 'TOKYO MX', date: '2026-10-14', time: '23:00', isStreaming: false },
      { channel: 'ABEMA', date: '2026-10-14', time: '24:00', isStreaming: true, episode: 2 },
    ]);
  });

  it('同じ日の番組は時刻の数字順に並べる（9:30 は 25:30 より前）', async () => {
    const edition = await anime();
    mockFetch({
      '/api/anilist/media/': () => mediaDto('1'),
      '/api/syoboi/title': okPrograms([
        program('B', '2026-10-14', '25:30'),
        program('A', '2026-10-14', '9:30'),
      ]),
    });

    await run();

    expect((await broadcasts(edition.id))?.map((b) => b.channel)).toEqual(['A', 'B']);
  });

  it('最大 8 件までにする', async () => {
    const edition = await anime();
    const many = Array.from({ length: 12 }, (_, i) => program(`局${i}`, `2026-10-${14 + i}`, '24:00'));
    mockFetch({ '/api/anilist/media/': () => mediaDto('1'), '/api/syoboi/title': okPrograms(many.reverse()) });

    await run();

    const saved = await broadcasts(edition.id);
    expect(saved).toHaveLength(8);
    expect(saved?.[0].date).toBe('2026-10-14');
    expect(saved?.[7].date).toBe('2026-10-21');
  });

  it('日付や局名が不正な番組は捨てる', async () => {
    const edition = await anime();
    mockFetch({
      '/api/anilist/media/': () => mediaDto('1'),
      '/api/syoboi/title': okPrograms([
        program('', '2026-10-14', '24:00'),
        program('MBS', '2026-13-40', '24:00'),
        program('MBS', '2026-10-14', ''),
        program('TOKYO MX', '2026-10-15', '24:30'),
      ]),
    });

    await run();

    expect(await broadcasts(edition.id)).toEqual([
      { channel: 'TOKYO MX', date: '2026-10-15', time: '24:30', isStreaming: false },
    ]);
  });

  it('中身が同じなら書き込まない', async () => {
    const edition = await anime();
    mockFetch({
      '/api/anilist/media/': () => mediaDto('1'),
      '/api/syoboi/title': okPrograms([program('TOKYO MX', '2026-10-14', '25:30', { episodeCount: 3 })]),
    });
    await run();
    await db.editions.update(edition.id, { updatedAt: OLD_STAMP });

    await run();

    expect((await getEdition(edition.id, db))?.updatedAt).toBe(OLD_STAMP);
  });

  it('次の同期で番組が変われば置き換え、今日より前になったものは落とす', async () => {
    const edition = await anime();
    let programs = [
      program('TOKYO MX', '2026-10-13', '25:30'),
      program('TOKYO MX', '2026-10-20', '25:30'),
    ];
    mockFetch({ '/api/anilist/media/': () => mediaDto('1'), '/api/syoboi/title': () => okPrograms(programs)() });

    await run();
    programs = [program('TOKYO MX', '2026-10-20', '25:30'), program('TOKYO MX', '2026-10-27', '25:30')];
    await run(() => new Date('2026-10-15T03:00:00.000Z'));

    expect((await broadcasts(edition.id))?.map((b) => b.date)).toEqual(['2026-10-20', '2026-10-27']);
  });

  it('今日以降の番組が無い（ok で空）なら broadcasts を消し、ほかの syncHints は残す', async () => {
    const edition = await anime();
    await updateEditionSyncHints(
      edition.id,
      { broadcasts: [{ channel: 'MBS', date: '2026-10-01', time: '24:00', isStreaming: false }] },
      db,
    );
    mockFetch({ '/api/anilist/media/': () => mediaDto('1'), '/api/syoboi/title': okPrograms([]) });

    await run();

    expect((await getEdition(edition.id, db))?.syncHints).toEqual({ syoboiTid: '7667' });
  });

  it('programs が応答に無い（古い collector）時は保存済みの番組を触らない', async () => {
    const edition = await anime();
    const saved = [{ channel: 'MBS', date: '2026-10-20', time: '24:00', isStreaming: false }];
    await updateEditionSyncHints(edition.id, { broadcasts: saved }, db);
    mockFetch({
      '/api/anilist/media/': () => mediaDto('1'),
      '/api/syoboi/title': () => ({ status: 'ok', tid: '7667', streamStarts: [] }),
    });

    await run();

    expect(await broadcasts(edition.id)).toEqual(saved);
  });

  it('放送終了（FINISHED）で照会しない時と、queued の時は、保存済みの番組を消さずそのままにする', async () => {
    const finished = await setup('F', { mediaType: 'anime', title: 'F', sourceRefs: [anilistRef('1')] });
    const queued = await setup('Q', { mediaType: 'anime', title: 'Q', sourceRefs: [anilistRef('2')] });
    const saved = [{ channel: 'MBS', date: '2026-10-20', time: '24:00', isStreaming: false }];
    await updateEditionSyncHints(finished.id, { broadcasts: saved }, db);
    await updateEditionSyncHints(queued.id, { broadcasts: saved }, db);
    const calls = mockFetch({
      '/api/anilist/media/': (url) =>
        url.pathname.endsWith('/1') ? mediaDto('1', { status: 'FINISHED' }) : mediaDto('2', { status: 'RELEASING' }),
      '/api/syoboi/title': () => ({ status: 'queued', programs: [], streamStarts: [] }),
    });

    await run();

    expect(callsTo(calls, '/api/syoboi/title').map((u) => u.searchParams.get('title'))).toEqual(['Q']);
    expect(await broadcasts(finished.id)).toEqual(saved);
    expect(await broadcasts(queued.id)).toEqual(saved);
  });
});

// ---------------------------------------------------------------------------
// 多重実行防止
// ---------------------------------------------------------------------------

describe('runSync: 同期するソースと検知の設定', () => {
  /** アニメ・文庫・なろう・カクヨムを1件ずつ。 */
  async function setupAll() {
    await setup('アニメ', { mediaType: 'anime', title: 'A', sourceRefs: [anilistRef('1')] });
    await setup('文庫', { mediaType: 'light-novel', title: 'L' });
    await setup('なろう', {
      mediaType: 'web-novel',
      title: 'N',
      sourceRefs: [{ source: 'narou', externalId: 'n1', url: 'u' }],
    });
    await setup('カクヨム', {
      mediaType: 'web-novel',
      title: 'K',
      sourceRefs: [{ source: 'kakuyomu', externalId: 'k1', url: 'u' }],
    });
    return mockFetch({
      '/api/anilist/media/': () => mediaDto('1'),
      '/api/ndl/series': () => ndlDto([]),
      '/api/narou/novel/': () => webNovelDto('narou', 'n1', 1, '2026-10-01'),
      '/api/kakuyomu/work/': () => webNovelDto('kakuyomu', 'k1', 1, '2026-10-01'),
    });
  }

  it('OFF のソースは照会せず、エラーにもしない。ON のソースは今までどおり', async () => {
    const calls = await setupAll();

    const result = await runSync({
      database: db,
      now: () => NOW,
      sources: { syoboi: false, ndl: false, kakuyomu: false },
    });

    expect(result.ran).toBe(true);
    expect(result.errors).toEqual({});
    expect(result.lastSyncAt).toBe(NOW.toISOString());
    expect(callsTo(calls, '/api/anilist/media/')).toHaveLength(1);
    expect(callsTo(calls, '/api/narou/novel/')).toHaveLength(1);
    expect(callsTo(calls, '/api/syoboi/')).toHaveLength(0);
    expect(callsTo(calls, '/api/ndl/')).toHaveLength(0);
    expect(callsTo(calls, '/api/kakuyomu/')).toHaveLength(0);
  });

  it('指定が無ければ、保存した設定（triptych.pref.syncSources）に従う', async () => {
    localStorage.setItem('triptych.pref.syncSources', JSON.stringify({ anilist: false, narou: false }));
    const calls = await setupAll();

    await runSync({ database: db, now: () => NOW });

    expect(callsTo(calls, '/api/anilist/media/')).toHaveLength(0);
    expect(callsTo(calls, '/api/narou/')).toHaveLength(0);
    // AniList を止めても、しょぼかるは放送状態が分からないものとして照会する
    expect(callsTo(calls, '/api/syoboi/title')).toHaveLength(1);
    expect(callsTo(calls, '/api/ndl/series')).toHaveLength(1);
    expect(callsTo(calls, '/api/kakuyomu/work/')).toHaveLength(1);
  });

  it('全部 OFF でもエラーにせず、同期した時刻を進める', async () => {
    const calls = await setupAll();
    const off = { anilist: false, syoboi: false, ndl: false, narou: false, kakuyomu: false };

    const result = await runSync({ database: db, now: () => NOW, sources: off, detect: false });

    expect(calls).toHaveLength(0);
    expect(result).toMatchObject({ ran: true, errors: {}, lastSyncAt: NOW.toISOString() });
  });

  it('先頭の Web 小説ソースが OFF なら、次の ON のソースで進捗と完結を決める', async () => {
    const edition = await setup('両方', {
      mediaType: 'web-novel',
      title: 'W',
      sourceRefs: [
        { source: 'narou', externalId: 'n1', url: 'u' },
        { source: 'kakuyomu', externalId: 'k1', url: 'u' },
      ],
    });
    mockFetch({ '/api/kakuyomu/work/': () => webNovelDto('kakuyomu', 'k1', 7, '2026-10-01', true) });

    await runSync({ database: db, now: () => NOW, sources: { narou: false } });

    const after = await getEdition(edition.id, db);
    expect(after?.progress).toEqual({ unit: 'episode', current: 0, total: 7 });
    expect(after?.completed).toBe(true);
  });

  it('検知が OFF なら検知の照会をせず、検知のエラーも出ない', async () => {
    await setup('薬屋のひとりごと', { mediaType: 'light-novel', title: '薬屋' });
    const calls = mockFetch({
      '/api/ndl/series': () => ndlDto([]),
      '/api/anilist/resolve-source': () => fail(502),
    });

    const result = await runSync({ database: db, force: true, now: () => NOW, detect: false });

    expect(result.errors.detect).toBeUndefined();
    expect(callsTo(calls, '/api/anilist/')).toHaveLength(0);
    expect(await db.meta.get('detectionThrottle')).toBeUndefined();
  });

  it('検知の指定が無ければ、保存した設定（triptych.pref.detection）に従う', async () => {
    await setup('薬屋のひとりごと', { mediaType: 'light-novel', title: '薬屋' });
    const calls = mockFetch({ '/api/ndl/series': () => ndlDto([]) });

    localStorage.setItem('triptych.pref.detection', 'false');
    await runSync({ database: db, force: true, now: () => NOW });
    expect(callsTo(calls, '/api/anilist/')).toHaveLength(0);

    localStorage.setItem('triptych.pref.detection', 'true');
    await runSync({ database: db, force: true, now: () => NOW });
    expect(callsTo(calls, '/api/anilist/').length).toBeGreaterThan(0);
  });
});

describe('runSync: 多重実行防止', () => {
  it('実行中に呼ばれたら同じ Promise を返し、照会は1回だけ', async () => {
    await setup('W', { mediaType: 'anime', title: 'A', sourceRefs: [anilistRef('1')] });
    const calls = mockFetch({ '/api/anilist/media/': () => mediaDto('1') });

    const first = runSync({ database: db, force: true, now: () => NOW });
    const second = runSync({ database: db, force: true, now: () => NOW });

    expect(second).toBe(first);
    await Promise.all([first, second]);
    expect(callsTo(calls, '/api/anilist/media/')).toHaveLength(1);

    // 終わった後は新しく実行できる
    await runSync({ database: db, force: true, now: () => NOW });
    expect(callsTo(calls, '/api/anilist/media/')).toHaveLength(2);
  });
});

describe('errorMessage', () => {
  it('collector の状態コードを言葉にし、つながらない時はそう書く', () => {
    expect(errorMessage(new ApiError(502, '/api/ndl/series: 502'))).toBe('相手先が応答しませんでした（502）');
    expect(errorMessage(new ApiError(429, '/api/anilist/media/1: 429'))).toBe('問い合わせが多すぎます（429）');
    expect(errorMessage(new ApiError(500, '/x: 500'))).toBe('相手先でエラーが起きました（500）');
    expect(errorMessage(new TypeError('Failed to fetch'))).toBe('collector に接続できませんでした');
    expect(errorMessage(new Error('壊れた日付'))).toBe('壊れた日付');
  });
});
