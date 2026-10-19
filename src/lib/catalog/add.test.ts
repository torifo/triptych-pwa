import { beforeEach, describe, expect, it } from 'vitest';
import { createDB, type TriptychDB } from '../db/schema';
import { createEdition, createWork, listEditions, listEditionsByWork, listWorks } from '../db/store';
import { addEntriesToLibrary } from './add';
import { makeEntry } from './fixtures.test-util';

let db: TriptychDB;
let n = 0;

beforeEach(() => {
  n += 1;
  db = createDB(`triptych-catalog-add-${n}-${Date.now()}`);
});

const anime = makeEntry({ source: 'anilist', externalId: '101', kind: 'anime', title: '薬屋のひとりごと', url: 'https://anilist.co/anime/101' });
const novel = makeEntry({ source: 'anilist', externalId: '102', kind: 'novel', title: '薬屋のひとりごと（小説）', url: 'https://anilist.co/manga/102' });
const bookVol = (id: string) =>
  makeEntry({
    source: 'ndl',
    externalId: id,
    kind: 'book',
    title: `薬屋のひとりごと ${id}`,
    extra: { kindHint: 'novel', line: '', series: '薬屋のひとりごと' },
  });

describe('addEntriesToLibrary', () => {
  it('creates a work, merging the book series ref into the same-type edition', async () => {
    const result = await addEntriesToLibrary({ title: '薬屋のひとりごと', entries: [anime, novel, bookVol('1')] }, db);

    expect(result.added).toHaveLength(2);
    const works = await listWorks(db);
    expect(works).toHaveLength(1);
    expect(result.workId).toBe(works[0].id);
    expect(works[0].title).toBe('薬屋のひとりごと');

    const editions = await listEditionsByWork(works[0].id, db);
    expect(editions.map((e) => e.mediaType).sort()).toEqual(['anime', 'light-novel']);
    const ln = editions.find((e) => e.mediaType === 'light-novel');
    expect(ln?.sourceRefs.map((r) => r.source).sort()).toEqual(['anilist', 'ndl']);
    const refs = editions.flatMap((e) => e.sourceRefs).map((r) => `${r.source}:${r.externalId}`);
    expect(refs.sort()).toEqual(['anilist:101', 'anilist:102', 'ndl:novel::薬屋のひとりごと']);
  });

  it('attaches a book series ref to an existing same-type edition of the target work', async () => {
    const first = await addEntriesToLibrary({ title: '薬屋のひとりごと', entries: [novel] }, db);
    const later = await addEntriesToLibrary(
      { title: '薬屋のひとりごと', entries: [bookVol('1')], workId: first.workId },
      db,
    );
    expect(later.added).toHaveLength(1);
    const editions = await listEditionsByWork(first.workId!, db);
    expect(editions).toHaveLength(1);
    expect(editions[0].sourceRefs.map((r) => `${r.source}:${r.externalId}`).sort()).toEqual([
      'anilist:102',
      'ndl:novel::薬屋のひとりごと',
    ]);
  });

  it('keeps a manga book separate from a light-novel edition', async () => {
    const mangaBook = makeEntry({
      source: 'ndl',
      externalId: 'm1',
      kind: 'book',
      title: '薬屋のひとりごと 1',
      extra: { kindHint: 'manga', line: '', series: '薬屋のひとりごと' },
    });
    const result = await addEntriesToLibrary({ title: '薬屋のひとりごと', entries: [novel, mangaBook] }, db);
    const editions = await listEditionsByWork(result.workId!, db);
    expect(editions.map((e) => e.mediaType).sort()).toEqual(['light-novel', 'manga']);
  });

  it('adds to an existing work when asked', async () => {
    const existing = await createWork({ title: '薬屋のひとりごと' }, db);
    const result = await addEntriesToLibrary({ title: '薬屋のひとりごと', entries: [anime], workId: existing.id }, db);
    expect(result.workId).toBe(existing.id);
    expect(await listWorks(db)).toHaveLength(1);
    expect(await listEditionsByWork(existing.id, db)).toHaveLength(1);
  });

  it('skips rows that are already in the library and does not create an empty work', async () => {
    const first = await addEntriesToLibrary({ title: '薬屋のひとりごと', entries: [anime] }, db);
    const again = await addEntriesToLibrary({ title: '薬屋のひとりごと', entries: [anime] }, db);
    expect(again.added).toEqual([]);
    expect(again.skipped).toEqual([anime]);
    expect(again.workId).toBeUndefined();
    expect(await listWorks(db)).toHaveLength(1);
    expect(await listEditions(db)).toHaveLength(1);

    const more = await addEntriesToLibrary({ title: '薬屋のひとりごと', entries: [anime, novel], workId: first.workId }, db);
    expect(more.added).toHaveLength(1);
    expect(more.skipped).toEqual([anime]);
  });

  it('registers a book series once, however many volumes are passed or already registered', async () => {
    const result = await addEntriesToLibrary({ title: '薬屋のひとりごと', entries: [bookVol('1'), bookVol('2')] }, db);
    expect(result.added).toHaveLength(1);
    expect(result.added[0].title).toBe('薬屋のひとりごと');

    const later = await addEntriesToLibrary({ title: '薬屋のひとりごと', entries: [bookVol('3')], workId: result.workId }, db);
    expect(later.added).toEqual([]);
  });

  it('recognises rows added through search (same source and externalId)', async () => {
    const work = await createWork({ title: '薬屋のひとりごと' }, db);
    await createEdition(
      { workId: work.id, mediaType: 'anime', title: '薬屋のひとりごと', sourceRefs: [{ source: 'anilist', externalId: '101', url: '' }] },
      db,
    );
    const result = await addEntriesToLibrary({ title: '薬屋のひとりごと', entries: [anime] }, db);
    expect(result.added).toEqual([]);
    expect(await listWorks(db)).toHaveLength(1);
  });

  it('falls back to a new work when the target work no longer exists', async () => {
    const result = await addEntriesToLibrary({ title: '薬屋のひとりごと', entries: [anime], workId: 'gone' }, db);
    expect(result.added).toHaveLength(1);
    expect(result.workId).not.toBe('gone');
    expect(await listWorks(db)).toHaveLength(1);
  });

  it('records the original medium on a new work, and only a known one', async () => {
    const result = await addEntriesToLibrary({ title: '薬屋のひとりごと', entries: [anime], origin: 'novel' }, db);
    expect((await listWorks(db)).find((w) => w.id === result.workId)?.origin).toBe('novel');

    const other = await addEntriesToLibrary({ title: '別の作品', entries: [novel], origin: 'game' as never }, db);
    expect((await listWorks(db)).find((w) => w.id === other.workId)?.origin).toBeUndefined();
  });

  it('fills the original medium of an existing work that has none, but never overwrites one', async () => {
    const plain = await createWork({ title: '薬屋のひとりごと' }, db);
    await addEntriesToLibrary({ title: '薬屋のひとりごと', entries: [anime], workId: plain.id, origin: 'novel' }, db);
    expect((await listWorks(db)).find((w) => w.id === plain.id)?.origin).toBe('novel');

    const known = await createWork({ title: '別の作品', origin: 'manga' }, db);
    await addEntriesToLibrary({ title: '別の作品', entries: [novel], workId: known.id, origin: 'novel' }, db);
    expect((await listWorks(db)).find((w) => w.id === known.id)?.origin).toBe('manga');
  });

  it('gives a coverless book the original novel cover and marks it automatic', async () => {
    const result = await addEntriesToLibrary(
      { title: '薬屋のひとりごと', entries: [bookVol('1')], novelCover: 'https://img.example/novel.jpg', mangaCover: 'https://img.example/manga.jpg' },
      db,
    );
    const [ln] = await listEditionsByWork(result.workId!, db);
    expect(ln.coverUrl).toBe('https://img.example/novel.jpg');
    expect(ln.syncHints?.coverFrom).toBe('catalog');
  });
});
