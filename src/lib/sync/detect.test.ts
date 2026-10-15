import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createDB, type Edition, type TriptychDB } from '../db/schema';
import {
  createDetectionEvent,
  createEdition,
  createWork,
  getEdition,
  listDetectionEventsByWork,
  listReleasesByEdition,
  updateDetectionEventState,
  updateEditionSyncHints,
  type CreateEditionInput,
} from '../db/store';
import {
  acceptDetection,
  DETECTION_MATCH_THRESHOLD,
  isSameSeries,
  rejectDetection,
  RELATION_EXPAND_LIMIT,
  runDetection,
} from './detect';

let db: TriptychDB;
let dbCounter = 0;

beforeEach(() => {
  dbCounter += 1;
  db = createDB(`triptych-detect-test-${dbCounter}-${Date.now()}`);
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

type Handler = (url: URL) => unknown;

function mockFetch(routes: Record<string, Handler>): URL[] {
  const calls: URL[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: string) => {
      const url = new URL(input, 'http://collector.test');
      calls.push(url);
      const key = Object.keys(routes)
        .filter((prefix) => url.pathname.startsWith(prefix))
        .sort((a, b) => b.length - a.length)[0];
      if (!key) throw new Error(`unexpected fetch: ${input}`);
      const reply = routes[key](url);
      const status = reply instanceof HttpFailure ? reply.status : 200;
      return { ok: status < 400, status, json: async () => reply } as Response;
    }),
  );
  return calls;
}

const callsTo = (calls: URL[], prefix: string) => calls.filter((u) => u.pathname.startsWith(prefix));

// ---------------------------------------------------------------------------
// fixtures（backend/tests/fixtures/anilist_media_*.json を RelationsDTO に正規化したもの）
// ---------------------------------------------------------------------------

type Edge = {
  relationType: string;
  externalId: string;
  type: 'ANIME' | 'MANGA';
  format: string | null;
  title: string;
  status: string | null;
  startDate: string | null;
  startPrecision: 'day' | 'month' | 'year' | null;
  isAdult: false;
};

function edge(
  relationType: string,
  externalId: string,
  type: 'ANIME' | 'MANGA',
  format: string,
  title: string,
  status: string | null = null,
  startDate: string | null = null,
  startPrecision: Edge['startPrecision'] = startDate ? 'day' : null,
): Edge {
  return { relationType, externalId, type, format, title, status, startDate, startPrecision, isAdult: false };
}

function relations(externalId: string, type: 'ANIME' | 'MANGA', format: string, title: string, edges: Edge[]) {
  return { externalId, type, format, title, relations: edges };
}

const KUSURIYA: Record<string, ReturnType<typeof relations>> = {
  // 原作小説
  '99026': relations('99026', 'MANGA', 'NOVEL', '薬屋のひとりごと', [
    edge('ADAPTATION', '99022', 'MANGA', 'MANGA', '薬屋のひとりごと', 'RELEASING', '2017-05-25'),
    edge('ADAPTATION', '113322', 'MANGA', 'MANGA', '薬屋のひとりごと 猫猫の後宮謎解き手帳', 'RELEASING', '2017-08-19'),
    edge('ADAPTATION', '161645', 'ANIME', 'TV', '薬屋のひとりごと', 'FINISHED', '2023-10-22'),
    edge('ADAPTATION', '176301', 'ANIME', 'TV', '薬屋のひとりごと 第2期', 'FINISHED', '2025-01-10'),
    edge('SIDE_STORY', '188727', 'MANGA', 'MANGA', '薬屋のひとりごと外伝　小蘭回想録', 'RELEASING', '2025-03-23'),
    edge('ADAPTATION', '195516', 'ANIME', 'TV', '薬屋のひとりごと 第3期', 'RELEASING', '2026-10-02'),
    edge('ADAPTATION', '200927', 'ANIME', 'TV', '薬屋のひとりごと 第3期 第2クール', 'NOT_YET_RELEASED', '2027-04-01', 'month'),
  ]),
  // アニメ1期
  '161645': relations('161645', 'ANIME', 'TV', '薬屋のひとりごと', [
    edge('ADAPTATION', '99026', 'MANGA', 'NOVEL', '薬屋のひとりごと', 'RELEASING', '2014-08-29'),
    edge('ALTERNATIVE', '99022', 'MANGA', 'MANGA', '薬屋のひとりごと', 'RELEASING', '2017-05-25'),
    edge('SIDE_STORY', '170508', 'ANIME', 'ONA', '猫猫のひとりごと', 'FINISHED', '2023-10-23'),
    edge('SEQUEL', '176301', 'ANIME', 'TV', '薬屋のひとりごと 第2期', 'FINISHED', '2025-01-10'),
  ]),
  '176301': relations('176301', 'ANIME', 'TV', '薬屋のひとりごと 第2期', [
    edge('ADAPTATION', '99026', 'MANGA', 'NOVEL', '薬屋のひとりごと', 'RELEASING', '2014-08-29'),
    edge('PREQUEL', '161645', 'ANIME', 'TV', '薬屋のひとりごと', 'FINISHED', '2023-10-22'),
    edge('SIDE_STORY', '186148', 'ANIME', 'ONA', '猫猫のひとりごと 第2期', 'FINISHED', '2025-01-13'),
    edge('SEQUEL', '195516', 'ANIME', 'TV', '薬屋のひとりごと 第3期', 'RELEASING', '2026-10-02'),
  ]),
  '195516': relations('195516', 'ANIME', 'TV', '薬屋のひとりごと 第3期', [
    edge('ADAPTATION', '99026', 'MANGA', 'NOVEL', '薬屋のひとりごと', 'RELEASING', '2014-08-29'),
    edge('PREQUEL', '176301', 'ANIME', 'TV', '薬屋のひとりごと 第2期', 'FINISHED', '2025-01-10'),
    edge('SEQUEL', '200927', 'ANIME', 'TV', '薬屋のひとりごと 第3期 第2クール', 'NOT_YET_RELEASED', '2027-04-01', 'month'),
    edge('SIDE_STORY', '200929', 'ANIME', 'MOVIE', '薬屋のひとりごと 亡妃の秘宝', 'NOT_YET_RELEASED', '2026-12-11'),
  ]),
  '200927': relations('200927', 'ANIME', 'TV', '薬屋のひとりごと 第3期 第2クール', [
    edge('PREQUEL', '195516', 'ANIME', 'TV', '薬屋のひとりごと 第3期', 'RELEASING', '2026-10-02'),
  ]),
};

const MUSHOKU = relations('85470', 'MANGA', 'NOVEL', '無職転生 ～異世界行ったら本気だす～', [
  edge('ADAPTATION', '85564', 'MANGA', 'MANGA', '無職転生 ～異世界行ったら本気だす～', 'RELEASING', '2014-05-02'),
  edge('SPIN_OFF', '104724', 'MANGA', 'MANGA', '無職転生 ~ロキシーだって本気です~', 'FINISHED', '2017-12-21'),
  edge('ADAPTATION', '108465', 'ANIME', 'TV', '無職転生 ～異世界行ったら本気だす～', 'FINISHED', '2021-01-11'),
  edge('ADAPTATION', '127720', 'ANIME', 'TV', '無職転生 ～異世界行ったら本気だす～ 第2クール', 'FINISHED', '2021-10-04'),
  edge('ADAPTATION', '141534', 'ANIME', 'SPECIAL', '無職転生 ～異世界行ったら本気だす～ 第2クール エリスのゴブリン討伐', 'FINISHED', '2022-03-16'),
  edge('ADAPTATION', '146065', 'ANIME', 'TV', '無職転生Ⅱ ～異世界行ったら本気だす～', 'FINISHED', '2023-07-03'),
  edge('SIDE_STORY', '165131', 'MANGA', 'NOVEL', '無職転生 ～蛇足編～', 'RELEASING', '2023-06-23'),
  edge('ADAPTATION', '166873', 'ANIME', 'TV', '無職転生Ⅱ ～異世界行ったら本気だす～ 第2クール', 'FINISHED', '2024-04-08'),
  edge('ADAPTATION', '178789', 'ANIME', 'TV', '無職転生Ⅲ ～異世界行ったら本気だす～', 'FINISHED', '2026-07-04'),
  edge('ADAPTATION', '217434', 'ANIME', 'TV', '無職転生Ⅲ ～異世界行ったら本気だす～ 第2クール', 'NOT_YET_RELEASED', '2027-01-01', 'year'),
]);
// 放送済み（FINISHED）の期は提案しない。原作 NOVEL から出るのは放送前の Ⅲ 第2クールだけ
const MUSHOKU_ANIME_IDS = ['217434'];

// 本好き（catalog.db の relations 2026-10-14）: 原作は部ごとの NOVEL で、第四部アニメ 217712 は第一部 NOVEL から見えない。
const HONZUKI_PART = {
  '87383': '本好きの下剋上 ~司書になるためには手段を選んでいられません~ 第一部「兵士の娘」',
  '110800': '本好きの下剋上~司書になるためには手段を選んでいられません~ 第二部「神殿の巫女見習い」',
  '110801': '本好きの下剋上~司書になるためには手段を選んでいられません~第三部「領主の養女」',
  '110802': '本好きの下剋上~司書になるためには手段を選んでいられません~第四部「貴族院の自称図書委員」',
  '113869': '本好きの下剋上～司書になるためには手段を選んでいられません～第五部「女神の化身」',
};
const HONZUKI_SHORTS = '本好きの下剋上~司書になるためには手段を選んでいられません~ 短編集';
const HONZUKI: Record<string, ReturnType<typeof relations>> = {
  '87383': relations('87383', 'MANGA', 'NOVEL', HONZUKI_PART['87383'], [
    edge('ADAPTATION', '108268', 'ANIME', 'TV', '本好きの下剋上 司書になるためには手段を選んでいられません', 'FINISHED', '2019-10-03'),
    edge('ADAPTATION', '113811', 'ANIME', 'OVA', '本好きの下剋上 司書になるためには手段を選んでいられません OVA', 'FINISHED', '2020-03-10'),
    edge('ADAPTATION', '96775', 'MANGA', 'MANGA', '本好きの下剋上~司書になるためには手段を選んでいられません~第一部 「本がないなら作ればいい」', 'FINISHED', '2015-10-30'),
    edge('SEQUEL', '110800', 'MANGA', 'NOVEL', HONZUKI_PART['110800'], 'FINISHED', '2015-09-25'),
    edge('SIDE_STORY', '113868', 'MANGA', 'NOVEL', HONZUKI_SHORTS, 'RELEASING', '2019-10-10'),
    edge('SPIN_OFF', '129614', 'MANGA', 'MANGA', '本好きの下剋上～司書になるためには手段を選んでいられません～ 公式コミックアンソロジー', 'RELEASING', '2019-07-07'),
  ]),
  '110800': relations('110800', 'MANGA', 'NOVEL', HONZUKI_PART['110800'], [
    edge('ADAPTATION', '110804', 'MANGA', 'MANGA', '本好きの下剋上~司書になるためには手段を選んでいられません~ 第二部 「本のためなら巫女になる!」', 'FINISHED', '2018-09-24'),
    edge('ADAPTATION', '113693', 'ANIME', 'TV', '本好きの下剋上 司書になるためには手段を選んでいられません 第2期', 'FINISHED', '2020-04-05'),
    edge('ADAPTATION', '121176', 'ANIME', 'TV', '本好きの下剋上 司書になるためには手段を選んでいられません 第3期', 'FINISHED', '2022-04-12'),
    edge('PREQUEL', '87383', 'MANGA', 'NOVEL', HONZUKI_PART['87383'], 'FINISHED', '2015-01-25'),
    edge('SEQUEL', '110801', 'MANGA', 'NOVEL', HONZUKI_PART['110801'], 'FINISHED', '2016-09-10'),
    edge('SIDE_STORY', '113868', 'MANGA', 'NOVEL', HONZUKI_SHORTS, 'RELEASING', '2019-10-10'),
  ]),
  '110801': relations('110801', 'MANGA', 'NOVEL', HONZUKI_PART['110801'], [
    edge('ADAPTATION', '110805', 'MANGA', 'MANGA', '本好きの下剋上~司書になるためには手段を選んでいられません~ 第三部 「領地に本を広げよう!」', 'RELEASING', '2018-04-30'),
    edge('ADAPTATION', '113811', 'ANIME', 'OVA', '本好きの下剋上 司書になるためには手段を選んでいられません OVA', 'FINISHED', '2020-03-10'),
    edge('ADAPTATION', '171110', 'ANIME', 'TV', '本好きの下剋上 領主の養女', 'FINISHED', '2026-04-04'),
    edge('PREQUEL', '110800', 'MANGA', 'NOVEL', HONZUKI_PART['110800'], 'FINISHED', '2015-09-25'),
    edge('SEQUEL', '110802', 'MANGA', 'NOVEL', HONZUKI_PART['110802'], 'FINISHED', '2017-12-09'),
    edge('SIDE_STORY', '113868', 'MANGA', 'NOVEL', HONZUKI_SHORTS, 'RELEASING', '2019-10-10'),
  ]),
  '110802': relations('110802', 'MANGA', 'NOVEL', HONZUKI_PART['110802'], [
    edge('ADAPTATION', '127249', 'MANGA', 'MANGA', '本好きの下剋上~司書になるためには手段を選んでいられません~ 第四部 「貴族院の図書館を救いたい！」', 'RELEASING', '2020-12-24'),
    edge('ADAPTATION', '217712', 'ANIME', 'TV', '本好きの下剋上 貴族院の自称図書委員', 'NOT_YET_RELEASED'),
    edge('PREQUEL', '110801', 'MANGA', 'NOVEL', HONZUKI_PART['110801'], 'FINISHED', '2016-09-10'),
    edge('SEQUEL', '113869', 'MANGA', 'NOVEL', HONZUKI_PART['113869'], 'FINISHED', '2020-03-10'),
    edge('SIDE_STORY', '110803', 'MANGA', 'NOVEL', '本好きの下剋上~司書になるためには手段を選んでいられません~貴族院外伝 一年生', 'FINISHED', '2018-10-10'),
  ]),
  '171110': relations('171110', 'ANIME', 'TV', '本好きの下剋上 領主の養女', [
    edge('ALTERNATIVE', '110805', 'MANGA', 'MANGA', '本好きの下剋上 第三部', 'RELEASING', '2018-04-30'),
    edge('PREQUEL', '121176', 'ANIME', 'TV', '本好きの下剋上 司書になるためには手段を選んでいられません 第3期', 'FINISHED', '2022-04-12'),
    edge('SEQUEL', '217712', 'ANIME', 'TV', '本好きの下剋上 貴族院の自称図書委員', 'NOT_YET_RELEASED'),
    edge('SOURCE', '110801', 'MANGA', 'NOVEL', HONZUKI_PART['110801'], 'FINISHED', '2016-09-10'),
  ]),
  '217712': relations('217712', 'ANIME', 'TV', '本好きの下剋上 貴族院の自称図書委員', [
    edge('PREQUEL', '171110', 'ANIME', 'TV', '本好きの下剋上 領主の養女', 'FINISHED', '2026-04-04'),
    edge('SOURCE', '110802', 'MANGA', 'NOVEL', HONZUKI_PART['110802'], 'FINISHED', '2017-12-09'),
  ]),
};

// かぐや様（同上）: 完結編 205081 は原作の ADAPTATION に無く、最新の特番 194884 の SEQUEL にだけある。
const KAGUYA: Record<string, ReturnType<typeof relations>> = {
  '86635': relations('86635', 'MANGA', 'MANGA', 'かぐや様は告らせたい～天才たちの恋愛頭脳戦～', [
    edge('ADAPTATION', '101921', 'ANIME', 'TV', 'かぐや様は告らせたい～天才たちの恋愛頭脳戦～', 'FINISHED', '2019-01-12'),
    edge('ADAPTATION', '112641', 'ANIME', 'TV', 'かぐや様は告らせたい？～天才たちの恋愛頭脳戦～', 'FINISHED', '2020-04-11'),
    edge('ADAPTATION', '125367', 'ANIME', 'TV', 'かぐや様は告らせたい-ウルトラロマンティック-', 'FINISHED', '2022-04-09'),
    edge('ADAPTATION', '151384', 'ANIME', 'TV', 'かぐや様は告らせたい -ファーストキッスは終わらない-', 'FINISHED', '2023-04-01'),
    edge('ADAPTATION', '194884', 'ANIME', 'SPECIAL', 'かぐや様は告らせたい 大人への階段', 'FINISHED', '2025-12-31'),
  ]),
  '194884': relations('194884', 'ANIME', 'SPECIAL', 'かぐや様は告らせたい 大人への階段', [
    edge('PREQUEL', '151384', 'ANIME', 'TV', 'かぐや様は告らせたい -ファーストキッスは終わらない-', 'FINISHED', '2023-04-01'),
    edge('SEQUEL', '205081', 'ANIME', 'MOVIE', 'かぐや様は告らせたい 完結編', 'NOT_YET_RELEASED'),
    edge('SOURCE', '86635', 'MANGA', 'MANGA', 'かぐや様は告らせたい～天才たちの恋愛頭脳戦～', 'FINISHED', '2015-05-19'),
  ]),
  '205081': relations('205081', 'ANIME', 'MOVIE', 'かぐや様は告らせたい 完結編', [
    edge('PREQUEL', '194884', 'ANIME', 'SPECIAL', 'かぐや様は告らせたい 大人への階段', 'FINISHED', '2025-12-31'),
  ]),
};

function relationsRoute(table: Record<string, unknown>): Handler {
  return (url) => {
    const id = url.pathname.split('/').pop() ?? '';
    return table[id] ?? relations(id, 'ANIME', 'TV', '', []);
  };
}

function searchResult(externalId: string, title: string, mediaType = 'light-novel') {
  return { source: 'anilist', externalId, mediaType, title, url: `https://anilist.co/manga/${externalId}` };
}

function titleCandidate(overrides: Partial<{ externalId: string; title: string; matchScore: number; format: string }> = {}) {
  return {
    source: 'anilist',
    externalId: overrides.externalId ?? '500',
    title: overrides.title ?? 'タイトル照合アニメ',
    airingStatus: 'NOT_YET_RELEASED',
    format: overrides.format ?? 'TV',
    startDate: '2027-01-01',
    startPrecision: 'year',
    matchScore: overrides.matchScore ?? 0.95,
    via: 'title',
  };
}

async function setup(workTitle: string, editions: Array<Omit<CreateEditionInput, 'workId'>>) {
  const work = await createWork({ title: workTitle }, db);
  const created: Edition[] = [];
  for (const e of editions) created.push(await createEdition({ status: 'active', ...e, workId: work.id }, db));
  return { work, editions: created };
}

const anilist = (id: string) => ({ source: 'anilist' as const, externalId: id, url: `https://anilist.co/anime/${id}` });

const T0 = new Date('2026-10-13T00:00:00Z');

// ---------------------------------------------------------------------------
// sequel
// ---------------------------------------------------------------------------

describe('runDetection: sequel', () => {
  it('薬屋のアニメ1期から SEQUEL を3段辿り、放送済みの2期は通り抜けて3期・3期第2クールを検知する', async () => {
    const { work } = await setup('薬屋のひとりごと', [
      { mediaType: 'anime', title: '薬屋のひとりごと', sourceRefs: [anilist('161645')] },
    ]);
    const calls = mockFetch({ '/api/anilist/relations/': relationsRoute(KUSURIYA) });

    const result = await runDetection({ database: db, now: () => T0 });

    expect(result.checked).toBe(1);
    expect(result.errors).toEqual({});
    const events = await listDetectionEventsByWork(work.id, db);
    // 2期（FINISHED）は通り抜けるが提案しない
    expect(events.map((e) => e.candidate.externalId).sort()).toEqual(['195516', '200927']);
    for (const e of events) {
      expect(e.via).toBe('sequel');
      expect(e.matchScore).toBe(1);
      expect(e.state).toBe('pending');
    }
    const third = events.find((e) => e.candidate.externalId === '195516');
    expect(third?.candidate).toEqual({
      source: 'anilist',
      externalId: '195516',
      title: '薬屋のひとりごと 第3期',
      airingStatus: 'RELEASING',
      format: 'TV',
      startDate: '2026-10-02',
      startPrecision: 'day',
    });
    expect(events.find((e) => e.candidate.externalId === '200927')?.candidate).toMatchObject({
      startDate: '2027-04-01',
      startPrecision: 'month',
    });
    // 3段（1期→2期→3期→3期第2クール）で止まる
    expect(callsTo(calls, '/api/anilist/relations/').map((u) => u.pathname.split('/').pop())).toEqual([
      '161645',
      '176301',
      '195516',
    ]);
    // ID が決まった Work は title 照合しない
    expect(callsTo(calls, '/api/anilist/detect')).toHaveLength(0);

    const throttle = (await db.meta.get('detectionThrottle'))?.value as Record<string, string>;
    expect(throttle[work.id]).toBe(T0.toISOString());
  });

  it('登録済みの期は提案せず、その先の期は探す', async () => {
    const { work } = await setup('薬屋のひとりごと', [
      { mediaType: 'anime', title: '1期', sourceRefs: [anilist('161645')] },
      { mediaType: 'anime', title: '2期', status: 'done', sourceRefs: [anilist('176301')] },
    ]);
    mockFetch({ '/api/anilist/relations/': relationsRoute(KUSURIYA) });

    await runDetection({ database: db, now: () => T0 });

    const ids = (await listDetectionEventsByWork(work.id, db)).map((e) => e.candidate.externalId).sort();
    expect(ids).toEqual(['195516', '200927']);
  });

  it('format が MUSIC のものは提案しない', async () => {
    const { work } = await setup('W', [{ mediaType: 'anime', title: 'A', sourceRefs: [anilist('1')] }]);
    mockFetch({
      '/api/anilist/relations/': relationsRoute({
        '1': relations('1', 'ANIME', 'TV', 'A', [
          edge('SEQUEL', '2', 'ANIME', 'MUSIC', 'A 主題歌'),
          edge('SEQUEL', '3', 'ANIME', 'TV', 'A 第2期'),
        ]),
      }),
    });

    await runDetection({ database: db, now: () => T0 });

    expect((await listDetectionEventsByWork(work.id, db)).map((e) => e.candidate.externalId)).toEqual(['3']);
  });
});

// ---------------------------------------------------------------------------
// relation
// ---------------------------------------------------------------------------

describe('runDetection: relation', () => {
  it('無職転生の原作 NOVEL（sourceRefs の anilist）から ADAPTATION のアニメを検知する', async () => {
    const { work } = await setup('無職転生 ～異世界行ったら本気だす～', [
      {
        mediaType: 'light-novel',
        title: '無職転生（MFブックス）',
        sourceRefs: [{ source: 'anilist', externalId: '85470', url: 'https://anilist.co/manga/85470' }],
      },
    ]);
    const calls = mockFetch({ '/api/anilist/relations/': relationsRoute({ '85470': MUSHOKU }) });

    await runDetection({ database: db, now: () => T0 });

    const events = await listDetectionEventsByWork(work.id, db);
    expect(events.map((e) => e.candidate.externalId).sort()).toEqual([...MUSHOKU_ANIME_IDS].sort());
    for (const e of events) {
      expect(e.via).toBe('relation');
      expect(e.matchScore).toBe(1);
    }
    expect(events.find((e) => e.candidate.externalId === '217434')?.candidate).toMatchObject({
      title: '無職転生Ⅲ ～異世界行ったら本気だす～ 第2クール',
      format: 'TV',
      airingStatus: 'NOT_YET_RELEASED',
      startDate: '2027-01-01',
      startPrecision: 'year',
    });
    expect(callsTo(calls, '/api/anilist/resolve-source')).toHaveLength(0);
    expect(callsTo(calls, '/api/anilist/detect')).toHaveLength(0);
  });

  it('原作が未紐づけなら resolve-source の先頭で紐づけ、anilistSourceId に保存して次回は使い回す', async () => {
    const { work, editions } = await setup('薬屋のひとりごと', [
      { mediaType: 'light-novel', title: '薬屋のひとりごと（ヒーロー文庫）' },
    ]);
    const calls = mockFetch({
      '/api/anilist/resolve-source': () => [
        searchResult('99026', '薬屋のひとりごと'),
        searchResult('188727', '薬屋のひとりごと外伝'),
      ],
      '/api/anilist/relations/': relationsRoute(KUSURIYA),
    });

    await runDetection({ database: db, now: () => T0 });

    const resolve = callsTo(calls, '/api/anilist/resolve-source');
    expect(resolve).toHaveLength(1);
    expect(Object.fromEntries(resolve[0].searchParams)).toEqual({ title: '薬屋のひとりごと', kind: 'novel' });
    expect((await getEdition(editions[0].id, db))?.syncHints?.anilistSourceId).toBe('99026');

    const events = await listDetectionEventsByWork(work.id, db);
    expect(events.map((e) => e.candidate.externalId).sort()).toEqual(['195516', '200927']);
    expect(events.every((e) => e.via === 'relation')).toBe(true);

    await runDetection({ database: db, force: true, now: () => T0 });
    expect(callsTo(calls, '/api/anilist/resolve-source')).toHaveLength(1);
    expect(callsTo(calls, '/api/anilist/relations/99026')).toHaveLength(2);
    // 2回目は既存の DetectionEvent と重複しない
    expect(await listDetectionEventsByWork(work.id, db)).toHaveLength(2);
  });

  it('漫画は kind=manga、Web 小説は kind=novel で resolve-source を引く', async () => {
    await setup('漫画作品', [{ mediaType: 'manga', title: '漫画' }]);
    await setup('Web作品', [{ mediaType: 'web-novel', title: 'なろう' }]);
    const calls = mockFetch({
      '/api/anilist/resolve-source': () => [],
      '/api/anilist/detect': () => [],
    });

    await runDetection({ database: db, now: () => T0 });

    const kinds = callsTo(calls, '/api/anilist/resolve-source').map((u) => [
      u.searchParams.get('title'),
      u.searchParams.get('kind'),
    ]);
    expect(kinds.sort()).toEqual([
      ['Web作品', 'novel'],
      ['漫画作品', 'manga'],
    ]);
  });

  it('syncHints.anilistSourceId があれば resolve-source を引かない', async () => {
    const { editions } = await setup('薬屋のひとりごと', [{ mediaType: 'manga', title: '薬屋（漫画）' }]);
    await updateEditionSyncHints(editions[0].id, { anilistSourceId: '99026' }, db);
    const calls = mockFetch({ '/api/anilist/relations/': relationsRoute(KUSURIYA) });

    await runDetection({ database: db, now: () => T0 });

    expect(callsTo(calls, '/api/anilist/resolve-source')).toHaveLength(0);
    expect(callsTo(calls, '/api/anilist/relations/99026')).toHaveLength(1);
  });
});

// ---------------------------------------------------------------------------
// relation の広げ方（原作の続きの部・原作アニメの続編）
// ---------------------------------------------------------------------------

describe('runDetection: relation（続きの部と続編）', () => {
  const relationIds = (calls: URL[]) =>
    callsTo(calls, '/api/anilist/relations/').map((u) => u.pathname.split('/').pop() ?? '');

  it('本好き: 第一部の NOVEL から SEQUEL の部を辿り、第四部 NOVEL の ADAPTATION（第四部アニメ）を検知する', async () => {
    const { work } = await setup('本好きの下剋上', [
      { mediaType: 'light-novel', title: '本好きの下剋上（TOブックス）' },
    ]);
    const calls = mockFetch({
      '/api/anilist/resolve-source': () => [searchResult('87383', HONZUKI_PART['87383'])],
      '/api/anilist/relations/': relationsRoute(HONZUKI),
    });

    const result = await runDetection({ database: db, now: () => T0 });

    expect(result.errors).toEqual({});
    const events = await listDetectionEventsByWork(work.id, db);
    // 放送済みの各期（108268 / 113693 / 121176 / 171110 / OVA）は提案しない
    expect(events.map((e) => [e.candidate.externalId, e.via])).toEqual([['217712', 'relation']]);
    expect(events[0].candidate).toMatchObject({
      title: '本好きの下剋上 貴族院の自称図書委員',
      airingStatus: 'NOT_YET_RELEASED',
      format: 'TV',
    });
    const ids = relationIds(calls);
    // 部は3段（第二部・第三部・第四部）まで。第五部と短編集（SIDE_STORY）は照会しない
    expect(ids).toEqual(expect.arrayContaining(['87383', '110800', '110801', '110802']));
    expect(ids).not.toContain('113869');
    expect(ids).not.toContain('113868');
  });

  it('副題を除いた題名が違う SEQUEL（別作品の続編）の NOVEL は辿らない', async () => {
    const { work } = await setup('ある物語', [
      { mediaType: 'light-novel', title: 'ある物語', sourceRefs: [{ source: 'anilist', externalId: '1', url: 'u' }] },
    ]);
    const calls = mockFetch({
      '/api/anilist/relations/': relationsRoute({
        '1': relations('1', 'MANGA', 'NOVEL', 'ある物語 第一部', [
          edge('SEQUEL', '2', 'MANGA', 'NOVEL', 'まったく別の物語', 'RELEASING'),
          edge('SEQUEL', '4', 'MANGA', 'MANGA', 'ある物語 第二部（漫画）', 'RELEASING'),
        ]),
        '2': relations('2', 'MANGA', 'NOVEL', 'まったく別の物語', [
          edge('ADAPTATION', '3', 'ANIME', 'TV', 'まったく別の物語', 'NOT_YET_RELEASED'),
        ]),
        '4': relations('4', 'MANGA', 'MANGA', 'ある物語 第二部（漫画）', [
          edge('ADAPTATION', '5', 'ANIME', 'TV', 'ある物語 第二部', 'NOT_YET_RELEASED'),
        ]),
      }),
    });

    await runDetection({ database: db, now: () => T0 });

    expect(await listDetectionEventsByWork(work.id, db)).toEqual([]);
    // 題名の違う NOVEL と、形式の違う（NOVEL → MANGA）SEQUEL は照会もしない
    expect(relationIds(calls)).not.toContain('2');
    expect(relationIds(calls)).not.toContain('4');
  });

  it('かぐや様: 原作から見つけたアニメのうち新しいものの SEQUEL を辿り、ADAPTATION に無い完結編を via=sequel で検知する', async () => {
    const { work } = await setup('かぐや様は告らせたい', [
      {
        mediaType: 'manga',
        title: 'かぐや様は告らせたい（漫画）',
        sourceRefs: [{ source: 'anilist', externalId: '86635', url: 'https://anilist.co/manga/86635' }],
      },
    ]);
    const calls = mockFetch({ '/api/anilist/relations/': relationsRoute(KAGUYA) });

    await runDetection({ database: db, now: () => T0 });

    const events = await listDetectionEventsByWork(work.id, db);
    expect(events.map((e) => [e.candidate.externalId, e.via])).toEqual([['205081', 'sequel']]);
    expect(events[0].candidate).toMatchObject({ title: 'かぐや様は告らせたい 完結編', format: 'MOVIE' });
    // 古い期（2019 年の1期など）からは辿らない
    expect(relationIds(calls)).not.toContain('101921');
  });

  it('原作アニメの SEQUEL でも、副題を除いた題名が違うもの（別作品）は提案しない', async () => {
    const { work } = await setup('ある漫画', [
      { mediaType: 'manga', title: 'ある漫画', sourceRefs: [{ source: 'anilist', externalId: '10', url: 'u' }] },
    ]);
    mockFetch({
      '/api/anilist/relations/': relationsRoute({
        '10': relations('10', 'MANGA', 'MANGA', 'ある漫画', [
          edge('ADAPTATION', '11', 'ANIME', 'TV', 'ある漫画 -アニメ-', 'FINISHED', '2024-01-05'),
        ]),
        '11': relations('11', 'ANIME', 'TV', 'ある漫画 -アニメ-', [
          edge('SEQUEL', '12', 'ANIME', 'TV', '次の世代の物語', 'NOT_YET_RELEASED'),
          edge('SEQUEL', '13', 'ANIME', 'TV', 'ある漫画 第2期', 'NOT_YET_RELEASED'),
        ]),
      }),
    });

    await runDetection({ database: db, now: () => T0 });

    const ids = (await listDetectionEventsByWork(work.id, db)).map((e) => e.candidate.externalId);
    expect(ids).toEqual(['13']);
  });

  it(`relations の照会は原作のほか Work ごとに ${RELATION_EXPAND_LIMIT} 回まで（部が長く続いても止まる）`, async () => {
    const { work } = await setup('長い物語', [
      { mediaType: 'light-novel', title: '長い物語', sourceRefs: [{ source: 'anilist', externalId: '1', url: 'u' }] },
    ]);
    // NOVEL 1 → 2 → 3 → … と SEQUEL が続き、各部に放送前のアニメが1つずつある。アニメにも SEQUEL が続く
    const table: Record<string, ReturnType<typeof relations>> = {};
    for (let n = 1; n <= 20; n += 1) {
      table[String(n)] = relations(String(n), 'MANGA', 'NOVEL', `長い物語 第${n}部`, [
        edge('SEQUEL', String(n + 1), 'MANGA', 'NOVEL', `長い物語 第${n + 1}部`, 'FINISHED'),
        edge('ADAPTATION', String(100 + n), 'ANIME', 'TV', `長い物語 第${n}期`, 'NOT_YET_RELEASED'),
      ]);
      table[String(100 + n)] = relations(String(100 + n), 'ANIME', 'TV', `長い物語 第${n}期`, [
        edge('SEQUEL', String(101 + n), 'ANIME', 'TV', `長い物語 第${n + 1}期`, 'NOT_YET_RELEASED'),
      ]);
    }
    const calls = mockFetch({ '/api/anilist/relations/': relationsRoute(table) });

    const result = await runDetection({ database: db, now: () => T0 });

    expect(result.errors).toEqual({});
    expect(callsTo(calls, '/api/anilist/relations/').length).toBeLessThanOrEqual(1 + RELATION_EXPAND_LIMIT);
    expect((await listDetectionEventsByWork(work.id, db)).length).toBeGreaterThan(0);
  });

  it('既存の薬屋（部の分かれていない原作）では照会が増えすぎない', async () => {
    await setup('薬屋のひとりごと', [
      { mediaType: 'light-novel', title: '原作', sourceRefs: [{ source: 'anilist', externalId: '99026', url: 'u' }] },
    ]);
    const calls = mockFetch({ '/api/anilist/relations/': relationsRoute(KUSURIYA) });

    await runDetection({ database: db, now: () => T0 });

    // 原作 + 新しいアニメ2つ（3期第2クール・3期）
    expect(relationIds(calls).sort()).toEqual(['195516', '200927', '99026']);
  });
});

describe('isSameSeries', () => {
  it.each([
    ['本好きの下剋上 ~司書になるためには手段を選んでいられません~ 第一部「兵士の娘」', '本好きの下剋上~司書になるためには手段を選んでいられません~第四部「貴族院の自称図書委員」', true],
    ['本好きの下剋上 領主の養女', '本好きの下剋上 貴族院の自称図書委員', true],
    ['無職転生 ～異世界行ったら本気だす～', '無職転生Ⅲ ～異世界行ったら本気だす～ 第2クール', true],
    ['かぐや様は告らせたい？～天才たちの恋愛頭脳戦～', 'かぐや様は告らせたい 完結編', true],
    ['Re:ゼロから始める異世界生活', 'Re:ゼロから始める異世界生活 3rd season', true],
    ['NARUTO -ナルト- 疾風伝', 'BORUTO-ボルト- NARUTO NEXT GENERATIONS', false],
    ['Re:ゼロから始める異世界生活', 'Re:Monster', false],
    ['ある物語 第一部', 'まったく別の物語', false],
  ])('%s / %s → %s', (a, b, expected) => {
    expect(isSameSeries(a, b)).toBe(expected);
  });
});

// ---------------------------------------------------------------------------
// title フォールバック
// ---------------------------------------------------------------------------

describe('runDetection: title フォールバック', () => {
  it('どの経路でも ID が決まらない Work だけ /detect を引き、閾値以上を via=title で記録する', async () => {
    const { work } = await setup('マイナー作品', [{ mediaType: 'web-novel', title: 'なろう版' }]);
    const calls = mockFetch({
      '/api/anilist/resolve-source': () => [],
      '/api/anilist/detect': () => [
        titleCandidate({ externalId: '501', matchScore: DETECTION_MATCH_THRESHOLD }),
        titleCandidate({ externalId: '502', matchScore: DETECTION_MATCH_THRESHOLD - 0.01 }),
        titleCandidate({ externalId: '503', matchScore: 0.99, format: 'MUSIC' }),
      ],
    });

    await runDetection({ database: db, now: () => T0 });

    expect(callsTo(calls, '/api/anilist/detect')[0].searchParams.get('title')).toBe('マイナー作品');
    const events = await listDetectionEventsByWork(work.id, db);
    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({ via: 'title', matchScore: DETECTION_MATCH_THRESHOLD });
    expect(events[0].candidate).toMatchObject({
      externalId: '501',
      format: 'TV',
      startDate: '2027-01-01',
      startPrecision: 'year',
    });
  });
});

// ---------------------------------------------------------------------------
// 除外
// ---------------------------------------------------------------------------

describe('runDetection: 除外', () => {
  it('他の Work に登録済み（追跡外を含む）のアニメは提案しない', async () => {
    const { work } = await setup('薬屋のひとりごと', [
      { mediaType: 'light-novel', title: '原作', sourceRefs: [{ source: 'anilist', externalId: '99026', url: 'u' }] },
    ]);
    await setup('薬屋（別登録）', [
      { mediaType: 'anime', title: '3期', status: 'dropped', sourceRefs: [anilist('195516')] },
    ]);
    mockFetch({ '/api/anilist/relations/': relationsRoute(KUSURIYA) });

    await runDetection({ database: db, now: () => T0 });

    const ids = (await listDetectionEventsByWork(work.id, db)).map((e) => e.candidate.externalId).sort();
    expect(ids).toEqual(['200927']);
  });

  it('同じ (workId, externalId) の DetectionEvent があれば状態を問わず作らない', async () => {
    const { work } = await setup('薬屋のひとりごと', [
      { mediaType: 'light-novel', title: '原作', sourceRefs: [{ source: 'anilist', externalId: '99026', url: 'u' }] },
    ]);
    const base = { source: 'anilist' as const, title: 'x', airingStatus: 'FINISHED' as const };
    const rejected = await createDetectionEvent(
      { workId: work.id, candidate: { ...base, externalId: '161645' }, matchScore: 0.9 },
      db,
    );
    await updateDetectionEventState(rejected.id, 'rejected', db);
    const accepted = await createDetectionEvent(
      { workId: work.id, candidate: { ...base, externalId: '176301' }, matchScore: 0.9 },
      db,
    );
    await updateDetectionEventState(accepted.id, 'accepted', db);
    await createDetectionEvent({ workId: work.id, candidate: { ...base, externalId: '195516' }, matchScore: 0.9 }, db);
    mockFetch({ '/api/anilist/relations/': relationsRoute(KUSURIYA) });

    const result = await runDetection({ database: db, now: () => T0 });

    expect(result.created.map((e) => e.candidate.externalId)).toEqual(['200927']);
    expect(await listDetectionEventsByWork(work.id, db)).toHaveLength(4);
  });
});

// ---------------------------------------------------------------------------
// 対象・スロットル・失敗
// ---------------------------------------------------------------------------

describe('runDetection: 対象とスロットル', () => {
  it('追跡外（done / dropped）の Edition しかない Work は照会しない', async () => {
    await setup('W', [
      { mediaType: 'manga', title: 'M', status: 'done' },
      { mediaType: 'anime', title: 'A', status: 'dropped', sourceRefs: [anilist('1')] },
    ]);
    const calls = mockFetch({});

    const result = await runDetection({ database: db, now: () => T0 });

    expect(result.checked).toBe(0);
    expect(calls).toHaveLength(0);
  });

  it('24h 以内は飛ばし、過ぎたら照会する', async () => {
    await setup('W', [{ mediaType: 'anime', title: 'A', sourceRefs: [anilist('161645')] }]);
    const calls = mockFetch({ '/api/anilist/relations/': relationsRoute(KUSURIYA) });

    await runDetection({ database: db, now: () => T0 });
    const before = calls.length;

    const skipped = await runDetection({ database: db, now: () => new Date('2026-10-13T10:00:00Z') });
    expect(skipped.checked).toBe(0);
    expect(calls).toHaveLength(before);

    const later = await runDetection({ database: db, now: () => new Date('2026-10-14T01:00:00Z') });
    expect(later.checked).toBe(1);
  });

  it('照会に失敗した Work はエラーを返してスロットルを記録せず、次の実行で再試行する', async () => {
    const { work } = await setup('薬屋のひとりごと', [
      { mediaType: 'anime', title: 'A', sourceRefs: [anilist('161645')] },
    ]);
    let down = true;
    mockFetch({
      '/api/anilist/relations/': (url) =>
        down && url.pathname.endsWith('/176301') ? new HttpFailure(502) : relationsRoute(KUSURIYA)(url),
    });

    const failed = await runDetection({ database: db, now: () => T0 });

    expect(failed.errors[work.id]).toContain('薬屋のひとりごと');
    expect(failed.errors[work.id]).toContain('502');
    const throttle = (await db.meta.get('detectionThrottle'))?.value as Record<string, string>;
    expect(throttle[work.id]).toBeUndefined();
    // 失敗前に辿れた分は残す
    // 失敗前に辿れたのは放送済みの2期だけなので、提案は無い
    expect((await listDetectionEventsByWork(work.id, db)).map((e) => e.candidate.externalId)).toEqual([]);

    down = false;
    const retried = await runDetection({ database: db, now: () => new Date('2026-10-13T01:00:00Z') });
    expect(retried.checked).toBe(1);
    expect(retried.errors).toEqual({});
    expect((await listDetectionEventsByWork(work.id, db)).map((e) => e.candidate.externalId).sort()).toEqual([
      '195516',
      '200927',
    ]);
  });

  it('resolve-source が失敗した Work は title 照合で代用しない', async () => {
    const { work } = await setup('W', [{ mediaType: 'light-novel', title: 'L' }]);
    const calls = mockFetch({ '/api/anilist/resolve-source': () => new HttpFailure(503) });

    const result = await runDetection({ database: db, now: () => T0 });

    expect(result.errors[work.id]).toContain('503');
    expect(callsTo(calls, '/api/anilist/detect')).toHaveLength(0);
  });

  it('refresh なら照会に refresh=1 を付ける', async () => {
    await setup('W', [{ mediaType: 'light-novel', title: 'L' }]);
    const calls = mockFetch({
      '/api/anilist/resolve-source': () => [],
      '/api/anilist/detect': () => [],
    });

    await runDetection({ database: db, refresh: true, now: () => T0 });

    expect(calls).toHaveLength(2);
    for (const url of calls) expect(url.searchParams.get('refresh')).toBe('1');
  });
});

// ---------------------------------------------------------------------------
// accept / reject
// ---------------------------------------------------------------------------

describe('acceptDetection', () => {
  it('アニメ Edition を作り、精度つきの season-start を既読で付けて accepted にする', async () => {
    const work = await createWork({ title: '薬屋のひとりごと' }, db);
    const event = await createDetectionEvent(
      {
        workId: work.id,
        candidate: {
          source: 'anilist',
          externalId: '200927',
          title: '薬屋のひとりごと 第3期 第2クール',
          airingStatus: 'NOT_YET_RELEASED',
          format: 'TV',
          startDate: '2027-04-01',
          startPrecision: 'month',
        },
        matchScore: 1,
      },
      db,
    );

    const { edition, event: updated } = await acceptDetection(event.id, db);

    expect(edition).toMatchObject({ workId: work.id, mediaType: 'anime', linkSource: 'auto', status: 'plan' });
    expect(edition.sourceRefs).toEqual([
      { source: 'anilist', externalId: '200927', url: 'https://anilist.co/anime/200927' },
    ]);
    expect(updated.state).toBe('accepted');

    const releases = await listReleasesByEdition(edition.id, db);
    expect(releases).toHaveLength(1);
    expect(releases[0]).toMatchObject({
      kind: 'season-start',
      date: '2027-04-01',
      precision: 'month',
      externalId: '200927',
      source: 'anilist',
      origin: 'sync',
    });
    expect(releases[0].seenAt).toBeDefined();
  });

  it('startDate が無ければ Release を作らない', async () => {
    const work = await createWork({ title: 'W' }, db);
    const event = await createDetectionEvent(
      {
        workId: work.id,
        candidate: { source: 'anilist', externalId: '201', title: 'A', airingStatus: 'NOT_YET_RELEASED' },
        matchScore: 1,
      },
      db,
    );

    const { edition } = await acceptDetection(event.id, db);

    expect(await listReleasesByEdition(edition.id, db)).toHaveLength(0);
  });
});

describe('rejectDetection', () => {
  it('rejected にする', async () => {
    const work = await createWork({ title: 'W' }, db);
    const event = await createDetectionEvent(
      {
        workId: work.id,
        candidate: { source: 'anilist', externalId: '300', title: 'A', airingStatus: 'FINISHED' },
        matchScore: 1,
      },
      db,
    );

    expect((await rejectDetection(event.id, db)).state).toBe('rejected');
  });
});
