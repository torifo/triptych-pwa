import { describe, expect, it } from 'vitest';
import type { Release } from '../db/schema';
import type { BroadcastHint } from '../db/schema';
import {
  buildEditionReleaseView,
  isStreamStarted,
  laterReleases,
  nextEpisodeBand,
  releaseDayFor,
  startedStreamServices,
  streamEntries,
  streamServiceName,
  upcomingBroadcasts,
  upcomingBroadcastSlots,
  nextVolumeBand,
  volumeCountdown,
} from './release-history';
import { groupEditionReleases } from './release-view';

const TODAY = '2026-10-13';

function release(over: Partial<Release> & Pick<Release, 'id' | 'date'>): Release {
  return {
    editionId: 'e1',
    kind: 'volume',
    label: over.id,
    precision: 'day',
    dateHistory: [],
    source: 'ndl',
    createdAt: '2026-10-01T00:00:00.000Z',
    updatedAt: '2026-10-01T00:00:00.000Z',
    ...over,
  };
}

const ids = (list: readonly { id: string }[]) => list.map((r) => r.id);

describe('streamServiceName', () => {
  it('collector の「<サービス> 配信」からサービス名を取り出す', () => {
    expect(streamServiceName('Netflix 配信')).toBe('Netflix');
    expect(streamServiceName('ABEMA 配信')).toBe('ABEMA');
    expect(streamServiceName('dアニメストア配信')).toBe('dアニメストア');
  });

  it('接尾辞のない手動ラベルはそのまま、空になる場合は元のラベル', () => {
    expect(streamServiceName('Netflix')).toBe('Netflix');
    expect(streamServiceName('  U-NEXT  ')).toBe('U-NEXT');
    expect(streamServiceName('配信')).toBe('配信');
    expect(streamServiceName('')).toBe('');
  });
});

describe('streamEntries / startedStreamServices', () => {
  const releases = [
    release({ id: 'abema', editionId: 'e1', kind: 'stream-start', label: 'ABEMA 配信', date: '2026-10-17' }),
    release({ id: 'netflix', editionId: 'e1', kind: 'stream-start', label: 'Netflix 配信', date: '2026-10-02' }),
    release({ id: 'prime', editionId: 'e1', kind: 'stream-start', label: 'Prime Video 配信', date: '2026-09-01' }),
    release({ id: 'dup', editionId: 'e1', kind: 'stream-start', label: 'Netflix 配信', date: '2026-10-05' }),
    release({ id: 'other', editionId: 'e2', kind: 'stream-start', label: 'U-NEXT 配信', date: '2026-10-01' }),
    release({ id: 'ep', editionId: 'e1', kind: 'episode', label: '第3話', date: '2026-10-20' }),
  ];

  it('配信開始だけを日付順に並べ、始まっているかを判定する（今日始まるものは予定）', () => {
    const entries = streamEntries(
      [...releases, release({ id: 'today', kind: 'stream-start', label: 'dアニメ 配信', date: TODAY })],
      TODAY,
    );
    expect(entries.map((e) => [e.release.id, e.started])).toEqual([
      ['prime', true],
      ['other', true],
      ['netflix', true],
      ['dup', true],
      ['today', false],
      ['abema', false],
    ]);
  });

  it('始まっているサービスを Edition ごとに重複なしで返す', () => {
    const map = startedStreamServices(releases, TODAY);
    expect(map.get('e1')).toEqual(['Prime Video', 'Netflix']);
    expect(map.get('e2')).toEqual(['U-NEXT']);
    expect(map.has('e3')).toBe(false);
  });

  it('配信中のサービスの「次回の配信」（rolling）は日付が先でも配信中に数える', () => {
    const stream = { editionId: 'e3', kind: 'stream-start' as const };
    const next = release({ ...stream, id: 'next', label: 'ABEMA 配信', date: '2026-10-16', rolling: true });
    const planned = release({ ...stream, id: 'planned', label: 'Netflix 配信', date: '2026-10-20' });

    expect(isStreamStarted(next, TODAY)).toBe(true);
    expect(isStreamStarted(planned, TODAY)).toBe(false);
    expect(streamEntries([next, planned], TODAY).map((e) => [e.release.id, e.started])).toEqual([
      ['next', true],
      ['planned', false],
    ]);
    expect(startedStreamServices([next, planned], TODAY).get('e3')).toEqual(['ABEMA']);
  });
});

describe('buildEditionReleaseView', () => {
  const volumes = [
    release({ id: 'v-new', date: '2026-10-05' }),
    release({ id: 'v-may', date: '2026-05-10' }), // 本好き文庫の最新巻のように 30 日より前
    release({ id: 'v-old', date: '2025-01-10' }),
    release({ id: 'v-oldest', date: '2020-01-10' }),
    release({ id: 'v-next', date: '2026-11-10' }),
    release({ id: 'v-month-past', date: '2025-03-01', precision: 'month' }),
    release({ id: 'v-year-past', date: '2019-01-01', precision: 'year' }),
  ];

  it('30 日より前の過去分を新しい順で older に返す', () => {
    const view = buildEditionReleaseView(volumes, 'light-novel', TODAY);
    expect(ids(view.upcoming)).toEqual(['v-next']);
    expect(ids(view.recent)).toEqual(['v-new']);
    expect(ids(view.older)).toEqual(['v-may', 'v-month-past', 'v-old', 'v-oldest', 'v-year-past']);
    expect(view.nextEpisodes).toEqual([]);
    expect(view.streams).toEqual([]);
  });

  it('older の件数は groupEditionReleases の hiddenCount と一致し、他の一覧と重ならない', () => {
    const view = buildEditionReleaseView(volumes, 'manga', TODAY);
    const groups = groupEditionReleases(volumes, 'manga', TODAY);
    expect(view.older).toHaveLength(groups.hiddenCount);
    const all = [...view.upcoming, ...view.recent, ...view.older];
    expect(new Set(ids(all)).size).toBe(volumes.length);
  });

  it('最新巻が 30 日より前でも、older に残って開ける', () => {
    const only = [release({ id: 'v-may', date: '2026-05-10' })];
    const view = buildEditionReleaseView(only, 'light-novel', TODAY);
    expect([view.upcoming, view.recent]).toEqual([[], []]);
    expect(ids(view.older)).toEqual(['v-may']);
  });

  it('pastDays を変えると畳む境界が動く', () => {
    const view = buildEditionReleaseView(volumes, 'light-novel', TODAY, 200);
    expect(ids(view.recent)).toEqual(['v-new', 'v-may']);
    expect(ids(view.older)).toEqual(['v-month-past', 'v-old', 'v-oldest', 'v-year-past']);
  });

  describe('アニメ面', () => {
    const anime = [
      release({ id: 'ep-next', kind: 'episode', label: '第3話', date: '2026-10-16', time: '25:30' }),
      release({ id: 'stream-new', kind: 'stream-start', label: 'ABEMA 配信', date: '2026-10-17' }),
      release({ id: 'stream-started', kind: 'stream-start', label: 'Netflix 配信', date: '2026-10-02' }),
      release({ id: 'stream-ancient', kind: 'stream-start', label: 'Prime Video 配信', date: '2026-07-01' }),
      release({ id: 'ep-past', kind: 'episode', label: '第2話', date: '2026-10-09' }),
      release({ id: 'ep-old', kind: 'episode', label: '第1話', date: '2026-08-01' }),
      release({ id: 'season', kind: 'season-start', label: '第2期', date: '2026-07-04' }),
    ];

    it('次話を先頭に、配信開始は 30 日より前の分も含めてひとまとめに出す', () => {
      const view = buildEditionReleaseView(anime, 'anime', TODAY);
      expect(ids(view.nextEpisodes)).toEqual(['ep-next']);
      expect(view.streams.map((s) => [s.release.id, s.service, s.started])).toEqual([
        ['stream-ancient', 'Prime Video', true],
        ['stream-started', 'Netflix', true],
        ['stream-new', 'ABEMA', false],
      ]);
    });

    it('配信開始を recent / older / upcoming に二重に出さない', () => {
      const view = buildEditionReleaseView(anime, 'anime', TODAY);
      expect(ids(view.recent)).toEqual(['ep-past']);
      expect(ids(view.older)).toEqual(['ep-old', 'season']);
      expect(ids(view.upcoming)).toEqual([]);
      const listed = [
        ...view.nextEpisodes,
        ...view.streams.map((s) => s.release),
        ...view.upcoming,
        ...view.recent,
        ...view.older,
      ];
      expect(ids(listed).sort()).toEqual(ids(anime).sort());
    });

    it('配信開始が無ければ streams は空で、次話だけが残る', () => {
      const view = buildEditionReleaseView(anime.filter((r) => r.kind !== 'stream-start'), 'anime', TODAY);
      expect(ids(view.nextEpisodes)).toEqual(['ep-next']);
      expect(view.streams).toEqual([]);
    });

    it('日付が壊れた配信開始も消せるよう streams に残す', () => {
      const broken = release({ id: 'broken', kind: 'stream-start', label: 'Netflix 配信', date: 'not-a-date' });
      const view = buildEditionReleaseView([broken], 'anime', TODAY);
      expect(view.streams.map((s) => [s.release.id, s.started])).toEqual([['broken', false]]);
      expect(view.older).toEqual([]);
    });
  });

  it('アニメ以外では stream-start を特別扱いしない', () => {
    const odd = [release({ id: 'stream', kind: 'stream-start', label: 'Netflix 配信', date: '2026-01-01' })];
    const view = buildEditionReleaseView(odd, 'manga', TODAY);
    expect(view.streams).toEqual([]);
    expect(ids(view.older)).toEqual(['stream']);
  });
});

describe('releaseDayFor', () => {
  // 2026-10-14 01:30 JST = 2026-10-13T16:30Z
  const lateNight = new Date('2026-10-13T16:30:00Z');

  it('アニメは放送日（朝5時区切り）、それ以外は暦日', () => {
    expect(releaseDayFor('anime', lateNight)).toBe('2026-10-13');
    expect(releaseDayFor('manga', lateNight)).toBe('2026-10-14');
    expect(releaseDayFor('web-novel', lateNight)).toBe('2026-10-14');
  });

  it('深夜 0 時を過ぎても 25:30 の回は次話に残る', () => {
    const releases = [release({ id: 'ep', kind: 'episode', date: '2026-10-13', time: '25:30' })];
    const view = buildEditionReleaseView(releases, 'anime', releaseDayFor('anime', lateNight));
    expect(view.nextEpisodes.map((r) => r.id)).toEqual(['ep']);
  });
});

describe('nextEpisodeBand', () => {
  // 2026-10-13 22:30 JST
  const now = new Date('2026-10-13T13:30:00Z');

  it('次話が無ければ帯を出さない', () => {
    expect(nextEpisodeBand([], TODAY, now)).toBeUndefined();
  });

  it('今日の回はランプを点け、24 時間以内ならカウントダウンを付ける', () => {
    const ep = release({ id: 'ep', kind: 'episode', date: TODAY, time: '25:30' });
    expect(nextEpisodeBand([ep], TODAY, now)).toEqual({ release: ep, onAirToday: true, countdown: 'あと 3:00' });
  });

  it('先の回はランプを点けず、24 時間より先ならカウントダウンも無い', () => {
    const ep = release({ id: 'ep', kind: 'episode', date: '2026-10-20', time: '25:30' });
    expect(nextEpisodeBand([ep], TODAY, now)).toEqual({ release: ep, onAirToday: false, countdown: undefined });
  });

  it('月・年精度の回は今日扱いにしない', () => {
    const ep = release({ id: 'ep', kind: 'episode', date: TODAY, precision: 'month' });
    expect(nextEpisodeBand([ep], TODAY, now)?.onAirToday).toBe(false);
  });
});

describe('laterReleases', () => {
  it('帯に出した次話を除き、残りの放送と予定を日付順にまとめる', () => {
    const next1 = release({ id: 'n1', kind: 'episode', date: '2026-10-14' });
    const next2 = release({ id: 'n2', kind: 'episode', date: '2026-10-21' });
    const season = release({ id: 's', kind: 'season-start', date: '2026-10-18' });
    expect(laterReleases({ nextEpisodes: [next1, next2], upcoming: [season] }).map((r) => r.id)).toEqual(['s', 'n2']);
    expect(laterReleases({ nextEpisodes: [], upcoming: [season] }).map((r) => r.id)).toEqual(['s']);
  });
});

describe('upcomingBroadcasts', () => {
  // 2026-10-13 22:30 JST（放送日 10/13）
  const now = new Date('2026-10-13T13:30:00Z');

  function hint(over: Partial<BroadcastHint> & Pick<BroadcastHint, 'channel' | 'date' | 'time'>): BroadcastHint {
    return { isStreaming: false, ...over };
  }

  it('まだ始まっていない番組を日時順に先頭 3 件', () => {
    const list = upcomingBroadcasts(
      [
        hint({ channel: 'BS11', date: '2026-10-13', time: '26:00' }),
        hint({ channel: 'AT-X', date: '2026-10-14', time: '22:00' }),
        hint({ channel: 'TOKYO MX', date: '2026-10-13', time: '25:30', episode: 2 }),
        hint({ channel: 'dアニメストア', date: '2026-10-14', time: '12:00', isStreaming: true }),
        hint({ channel: '過ぎた局', date: '2026-10-13', time: '22:00' }),
        hint({ channel: '昨日', date: '2026-10-12', time: '25:30' }),
      ],
      now,
    );
    expect(list.map((b) => b.channel)).toEqual(['TOKYO MX', 'BS11', 'dアニメストア']);
  });

  it('時刻が読めない番組は日付だけで残し、同じ日の末尾に回す', () => {
    const list = upcomingBroadcasts(
      [
        hint({ channel: '不明', date: '2026-10-14', time: '' }),
        hint({ channel: 'MX', date: '2026-10-14', time: '1:00' }),
        hint({ channel: '壊れた日付', date: '2026-10', time: '1:00' }),
      ],
      now,
      5,
    );
    expect(list.map((b) => b.channel)).toEqual(['MX', '不明']);
  });

  it('番組が無ければ空', () => {
    expect(upcomingBroadcasts(undefined, now)).toEqual([]);
    expect(upcomingBroadcasts([], now)).toEqual([]);
  });
});

describe('upcomingBroadcastSlots', () => {
  // 2026-10-15 14:00 JST（放送日 10/15）
  const now = new Date('2026-10-15T05:00:00Z');

  function hint(over: Partial<BroadcastHint> & Pick<BroadcastHint, 'channel' | 'date' | 'time'>): BroadcastHint {
    return { isStreaming: false, ...over };
  }

  it('同じ日時の局を1行にまとめ、在京キー局を表に出して残りを数える', () => {
    const slots = upcomingBroadcastSlots(
      [
        hint({ channel: 'STV札幌テレビ', date: '2026-10-16', time: '23:00', episode: 51 }),
        hint({ channel: '中京テレビ', date: '2026-10-16', time: '23:00', episode: 51 }),
        hint({ channel: '日本テレビ', date: '2026-10-16', time: '23:00', episode: 51 }),
        hint({ channel: '読売テレビ', date: '2026-10-16', time: '23:00', episode: 51 }),
        hint({ channel: 'BS日テレ', date: '2026-10-18', time: '24:00' }),
      ],
      now,
    );
    expect(slots).toEqual([
      { channel: '日本テレビ', others: 3, date: '2026-10-16', time: '23:00', isStreaming: false },
      { channel: 'BS日テレ', others: 0, date: '2026-10-18', time: '24:00', isStreaming: false },
    ]);
  });

  it('キー局が無ければ局名の順で先頭、配信と放送は別の行', () => {
    const slots = upcomingBroadcastSlots(
      [
        hint({ channel: '中京テレビ', date: '2026-10-16', time: '23:00' }),
        hint({ channel: 'STV札幌テレビ', date: '2026-10-16', time: '23:00' }),
        hint({ channel: 'ABEMA', date: '2026-10-16', time: '23:00', isStreaming: true }),
      ],
      now,
    );
    expect(slots.map((s) => [s.channel, s.others, s.isStreaming])).toEqual([
      ['STV札幌テレビ', 1, false],
      ['ABEMA', 0, true],
    ]);
  });

  it('まとめた後で先頭 limit 件', () => {
    const many = ['2026-10-16', '2026-10-17', '2026-10-18', '2026-10-19'].flatMap((date) => [
      hint({ channel: 'TOKYO MX', date, time: '24:00' }),
      hint({ channel: 'BS11', date, time: '24:00' }),
    ]);
    expect(upcomingBroadcastSlots(many, now).map((s) => s.date)).toEqual(['2026-10-16', '2026-10-17', '2026-10-18']);
  });
});

describe('nextVolumeBand / volumeCountdown', () => {
  const vol = (id: string, date: string, precision: 'day' | 'month' = 'day') =>
    release({ id, date, precision, kind: 'volume', label: id });

  it('本は予定の先頭の巻を帯にし、日が分かれば残り日数を持つ', () => {
    const band = nextVolumeBand([vol('3', '2026-10-20'), vol('4', '2027-01-20')], 'manga', '2026-10-15');
    expect(band?.release.id).toBe('3');
    expect(band?.daysLeft).toBe(5);
  });

  it('月精度の巻は残り日数を持たず、アニメと巻の無い予定には帯を出さない', () => {
    expect(nextVolumeBand([vol('3', '2026-11-01', 'month')], 'light-novel', '2026-10-15')?.daysLeft).toBeUndefined();
    expect(nextVolumeBand([vol('3', '2026-10-20')], 'anime', '2026-10-15')).toBeUndefined();
    expect(nextVolumeBand([], 'manga', '2026-10-15')).toBeUndefined();
  });

  it('残り日数の言い方', () => {
    expect(volumeCountdown(0)).toBe('今日発売');
    expect(volumeCountdown(1)).toBe('あす発売');
    expect(volumeCountdown(12)).toBe('あと 12 日');
    expect(volumeCountdown(undefined)).toBeUndefined();
  });
});
