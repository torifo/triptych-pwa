import { describe, expect, it } from 'vitest';
import type { Edition, Release, Work } from '../db/schema';
import {
  ICS_DAYS,
  buildIcs,
  escapeIcsText,
  foldIcsLine,
  icsEventsFor,
  icsFilename,
  releaseToIcsEvent,
} from './ics';

function release(over: Partial<Release> & Pick<Release, 'id' | 'editionId' | 'date'>): Release {
  return {
    kind: 'episode',
    label: '第1話',
    dateHistory: [],
    source: 'anilist',
    createdAt: '2026-10-01T00:00:00.000Z',
    updatedAt: '2026-10-01T00:00:00.000Z',
    ...over,
  };
}

function edition(over: Partial<Edition> & Pick<Edition, 'id' | 'workId'>): Edition {
  return {
    mediaType: 'anime',
    title: '',
    sourceRefs: [],
    status: 'active',
    linkSource: 'manual',
    createdAt: '2026-10-01T00:00:00.000Z',
    updatedAt: '2026-10-01T00:00:00.000Z',
    ...over,
  };
}

const encoder = new TextEncoder();
const octets = (s: string) => encoder.encode(s).length;
const NOW = new Date('2026-10-15T03:04:05.000Z');

describe('escapeIcsText', () => {
  it('バックスラッシュ・セミコロン・カンマ・改行を逃がす', () => {
    expect(escapeIcsText('a,b;c\\d')).toBe('a\\,b\\;c\\\\d');
    expect(escapeIcsText('1行目\r\n2行目\n3行目\r4行目')).toBe('1行目\\n2行目\\n3行目\\n4行目');
    expect(escapeIcsText('ふつうの題名')).toBe('ふつうの題名');
  });
});

describe('foldIcsLine', () => {
  it('75 オクテットちょうどは折らず、76 で折る（続きは空白1つで始める）', () => {
    const exact = `X:${'a'.repeat(73)}`;
    expect(octets(exact)).toBe(75);
    expect(foldIcsLine(exact)).toBe(exact);

    const over = `X:${'a'.repeat(74)}`;
    expect(foldIcsLine(over)).toBe(`X:${'a'.repeat(73)}\r\n a`);
  });

  it('マルチバイト文字の途中では折らない', () => {
    const fits = `X:${'a'.repeat(70)}あ`; // 2 + 70 + 3 = 75
    expect(foldIcsLine(fits)).toBe(fits);

    const spills = `X:${'a'.repeat(71)}あ`; // 76: 'あ' ごと次の行へ
    expect(foldIcsLine(spills)).toBe(`X:${'a'.repeat(71)}\r\n あ`);
  });

  it('長い日本語: どの行も 75 オクテット以内で、つなぎ直すと元に戻る', () => {
    const line = `SUMMARY:${'薬屋のひとりごと'.repeat(12)}`;
    const folded = foldIcsLine(line);
    const physical = folded.split('\r\n');
    expect(physical.length).toBeGreaterThan(1);
    for (const [i, part] of physical.entries()) {
      expect(octets(part)).toBeLessThanOrEqual(75);
      if (i > 0) expect(part.startsWith(' ')).toBe(true);
    }
    expect(physical[0]).toBe(`SUMMARY:${'薬屋のひとりごと'.repeat(2)}${'薬屋のひとり'}`); // 8 + 22 字 × 3 = 74
    expect(folded.replace(/\r\n /g, '')).toBe(line);
  });
});

describe('releaseToIcsEvent', () => {
  it('25:30 は翌日の 01:30 から 30 分', () => {
    const event = releaseToIcsEvent(release({ id: 'r1', editionId: 'e', date: '2026-10-16', time: '25:30' }), '作品 第1話');
    expect(event.when).toEqual({ allDay: false, start: '20261017T013000', end: '20261017T020000' });
    expect(event.uid).toBe('r1@triptych.local');
  });

  it('日をまたぐ枠の終わりは翌日', () => {
    const event = releaseToIcsEvent(release({ id: 'r1', editionId: 'e', date: '2026-10-16', time: '23:45' }), 's');
    expect(event.when).toEqual({ allDay: false, start: '20261016T234500', end: '20261017T001500' });
  });

  it('時刻が無い・読めない時は終日', () => {
    expect(releaseToIcsEvent(release({ id: 'r1', editionId: 'e', date: '2026-10-20', kind: 'volume' }), 's').when).toEqual({
      allDay: true,
      date: '20261020',
      end: '20261021',
    });
    expect(releaseToIcsEvent(release({ id: 'r1', editionId: 'e', date: '2026-12-31', time: 'soon' }), 's').when).toEqual({
      allDay: true,
      date: '20261231',
      end: '20270101',
    });
  });
});

describe('icsEventsFor', () => {
  const works = new Map<string, Pick<Work, 'title'>>([
    ['w1', { title: '薬屋のひとりごと' }],
    ['w2', { title: '本好きの下剋上' }],
  ]);
  const editions = new Map<string, Edition>([
    [
      'anime',
      edition({
        id: 'anime',
        workId: 'w1',
        syncHints: {
          broadcasts: [
            { channel: '日本テレビ', date: '2026-10-16', time: '25:30', isStreaming: false },
            { channel: 'BS日テレ', date: '2026-10-16', time: '25:30', isStreaming: false },
            { channel: 'ABEMA', date: '2026-10-16', time: '25:30', isStreaming: true },
            { channel: '日本テレビ', date: '2026-10-23', time: '25:30', isStreaming: false },
          ],
        },
      }),
    ],
    ['book', edition({ id: 'book', workId: 'w2', mediaType: 'light-novel' })],
    ['done', edition({ id: 'done', workId: 'w2', mediaType: 'manga', status: 'done' })],
  ]);
  const today = '2026-10-15';

  const releases = [
    release({ id: 'ep', editionId: 'anime', date: '2026-10-16', time: '25:30', label: '第2期 第3話' }),
    release({ id: 'vol', editionId: 'book', date: '2026-10-20', kind: 'volume', label: '5', source: 'ndl' }),
    release({ id: 'netflix', editionId: 'anime', date: '2026-10-01', kind: 'stream-start', label: 'Netflix 配信' }),
    release({ id: 'past', editionId: 'book', date: '2026-10-14', kind: 'volume', label: '4' }),
    release({ id: 'last', editionId: 'book', date: '2026-12-13', kind: 'volume', label: '6' }), // 今日 + 59
    release({ id: 'beyond', editionId: 'book', date: '2026-12-14', kind: 'volume', label: '7' }), // 今日 + 60
    release({ id: 'month', editionId: 'book', date: '2026-11-01', kind: 'volume', label: '8', precision: 'month' }),
    release({ id: 'untracked', editionId: 'done', date: '2026-10-20', kind: 'volume', label: '1' }),
    release({
      id: 'rolling',
      editionId: 'anime',
      date: '2026-10-17',
      kind: 'stream-start',
      label: 'Netflix 配信',
      rolling: true,
    }),
  ];

  it('追跡中・日精度・今日から60日分だけ、日時順に', () => {
    const events = icsEventsFor({ releases, editions, works, today });
    expect(ICS_DAYS).toBe(60);
    expect(events.map((e) => e.uid)).toEqual(['ep@triptych.local', 'vol@triptych.local', 'last@triptych.local']);
  });

  it('SUMMARY は作品名とラベル（巻は「巻」を足す）、DESCRIPTION に局名と配信', () => {
    const [ep, vol] = icsEventsFor({ releases, editions, works, today });
    expect(ep.summary).toBe('薬屋のひとりごと 第2期 第3話');
    expect(ep.description).toBe('放送: 日本テレビ・BS日テレ\n配信: Netflix・ABEMA');
    expect(vol.summary).toBe('本好きの下剋上 5巻');
    expect(vol.description).toBeUndefined();
  });
});

describe('buildIcs', () => {
  const events = [
    releaseToIcsEvent(release({ id: 'ep', editionId: 'e', date: '2026-10-16', time: '25:30' }), '薬屋, 第3話; 特別編', '放送: 日本テレビ\n配信: Netflix'),
    releaseToIcsEvent(release({ id: 'vol', editionId: 'e', date: '2026-10-20', kind: 'volume' }), '本好きの下剋上 5巻'),
  ];
  const text = buildIcs(events, NOW);
  const lines = text.split('\r\n');

  it('改行は CRLF だけで、最後も CRLF で終わる', () => {
    expect(text.endsWith('\r\n')).toBe(true);
    expect(text.replace(/\r\n/g, '')).not.toMatch(/[\r\n]/);
  });

  it('VCALENDAR の中に Asia/Tokyo の VTIMEZONE と VEVENT', () => {
    expect(lines[0]).toBe('BEGIN:VCALENDAR');
    expect(lines).toContain('VERSION:2.0');
    expect(lines.some((l) => l.startsWith('PRODID:'))).toBe(true);
    const tz = lines.indexOf('BEGIN:VTIMEZONE');
    expect(tz).toBeGreaterThan(0);
    expect(lines[tz + 1]).toBe('TZID:Asia/Tokyo');
    expect(lines).toContain('TZOFFSETTO:+0900');
    expect(lines.filter((l) => l === 'BEGIN:VEVENT')).toHaveLength(2);
    expect(lines[lines.length - 2]).toBe('END:VCALENDAR');
  });

  it('時刻ありは TZID 付き、終日は VALUE=DATE。UID と DTSTAMP（UTC）', () => {
    expect(lines).toContain('UID:ep@triptych.local');
    expect(lines).toContain('DTSTAMP:20261015T030405Z');
    expect(lines).toContain('DTSTART;TZID=Asia/Tokyo:20261017T013000');
    expect(lines).toContain('DTEND;TZID=Asia/Tokyo:20261017T020000');
    expect(lines).toContain('DTSTART;VALUE=DATE:20261020');
    expect(lines).toContain('DTEND;VALUE=DATE:20261021');
  });

  it('SUMMARY と DESCRIPTION を逃がす', () => {
    expect(lines).toContain('SUMMARY:薬屋\\, 第3話\\; 特別編');
    expect(lines).toContain('DESCRIPTION:放送: 日本テレビ\\n配信: Netflix');
  });

  it('どの行も 75 オクテット以内', () => {
    const long = buildIcs([releaseToIcsEvent(release({ id: 'x', editionId: 'e', date: '2026-10-16' }), 'あ'.repeat(80))], NOW);
    for (const line of long.split('\r\n')) expect(octets(line)).toBeLessThanOrEqual(75);
  });
});

describe('icsFilename', () => {
  it('triptych-YYYYMMDD.ics', () => {
    expect(icsFilename('2026-10-15')).toBe('triptych-20261015.ics');
  });
});
