import { beforeEach, describe, expect, it } from 'vitest';
import { createDB, type TriptychDB } from '../db/schema';
import { createEdition, createWork, getEdition, updateEditionSyncHints } from '../db/store';
import { isAutoCover, parseCoverUrlInput, saveEditionCover } from './edition-cover';

describe('parseCoverUrlInput', () => {
  it('http(s) の URL だけを受け付け、前後の空白は落とす', () => {
    expect(parseCoverUrlInput(' https://s4.anilist.co/a.jpg ')).toEqual({ ok: true, url: 'https://s4.anilist.co/a.jpg' });
    expect(parseCoverUrlInput('http://example.com/a.png')).toEqual({ ok: true, url: 'http://example.com/a.png' });
    expect(parseCoverUrlInput('javascript:alert(1)').ok).toBe(false);
    expect(parseCoverUrlInput('data:image/png;base64,AAAA').ok).toBe(false);
    expect(parseCoverUrlInput('example.com/a.jpg').ok).toBe(false);
  });

  it('空は表紙を外す', () => {
    expect(parseCoverUrlInput('')).toEqual({ ok: true, url: undefined });
    expect(parseCoverUrlInput('   ')).toEqual({ ok: true, url: undefined });
  });
});

describe('isAutoCover', () => {
  it('表紙があって coverFrom が立っている時だけ自動', () => {
    expect(isAutoCover({ coverUrl: 'https://x/a.jpg', syncHints: { coverFrom: 'anilist' } })).toBe(true);
    expect(isAutoCover({ coverUrl: 'https://x/a.jpg', syncHints: {} })).toBe(false);
    expect(isAutoCover({ coverUrl: undefined, syncHints: { coverFrom: 'catalog' } })).toBe(false);
  });
});

describe('saveEditionCover', () => {
  let db: TriptychDB;
  let counter = 0;

  beforeEach(() => {
    counter += 1;
    db = createDB(`triptych-edition-cover-${counter}-${Date.now()}`);
  });

  async function autoCoveredEdition() {
    const work = await createWork({ title: '薬屋のひとりごと' }, db);
    const edition = await createEdition(
      { workId: work.id, mediaType: 'anime', title: '薬屋のひとりごと', coverUrl: 'https://x/auto.jpg' },
      db,
    );
    await updateEditionSyncHints(edition.id, { coverFrom: 'anilist', syoboiTid: '7000' }, db);
    return edition;
  }

  it('手で入れた表紙を保存し、自動の印だけを消す（他の手がかりは残す）', async () => {
    const edition = await autoCoveredEdition();
    await saveEditionCover(edition.id, 'https://x/manual.jpg', db);
    const saved = await getEdition(edition.id, db);
    expect(saved?.coverUrl).toBe('https://x/manual.jpg');
    expect(saved?.syncHints?.coverFrom).toBeUndefined();
    expect(saved?.syncHints?.syoboiTid).toBe('7000');
  });

  it('表紙を外した時も自動の印を消す', async () => {
    const edition = await autoCoveredEdition();
    await saveEditionCover(edition.id, undefined, db);
    const saved = await getEdition(edition.id, db);
    expect(saved?.coverUrl).toBeUndefined();
    expect(saved?.syncHints?.coverFrom).toBeUndefined();
  });
});
