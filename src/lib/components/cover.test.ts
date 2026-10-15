import { describe, expect, it } from 'vitest';
import { coverUrlOf, editionCover, fallbackCoverText, sizedCoverUrl, workCover } from './cover';

describe('coverUrlOf', () => {
  it('http(s) の URL だけを表紙にする', () => {
    expect(coverUrlOf({ coverUrl: 'https://s4.anilist.co/a.jpg' })).toBe('https://s4.anilist.co/a.jpg');
    expect(coverUrlOf({ coverUrl: 'javascript:alert(1)' })).toBeUndefined();
    expect(coverUrlOf({ coverUrl: '  ' })).toBeUndefined();
    expect(coverUrlOf({})).toBeUndefined();
    expect(coverUrlOf(undefined)).toBeUndefined();
  });
});

describe('editionCover', () => {
  it('題名は Work 名を優先する', () => {
    const cover = editionCover({ coverUrl: 'https://x/a.jpg', title: '薬屋 第2期', mediaType: 'anime' }, '薬屋のひとりごと');
    expect(cover).toEqual({ url: 'https://x/a.jpg', title: '薬屋のひとりごと', mediaType: 'anime' });
  });
});

describe('workCover', () => {
  const work = { title: '本好きの下剋上', coverEditionId: undefined as string | undefined };

  it('coverEditionId の表紙を最優先する', () => {
    const editions = [
      { id: 'a', coverUrl: 'https://x/anime.jpg', mediaType: 'anime' as const },
      { id: 'b', coverUrl: 'https://x/book.jpg', mediaType: 'light-novel' as const },
    ];
    expect(workCover({ ...work, coverEditionId: 'b' }, editions).url).toBe('https://x/book.jpg');
  });

  it('coverEditionId に表紙が無ければ、表紙のある Edition をアニメ・漫画・文庫・Web の順で選ぶ', () => {
    const editions = [
      { id: 'w', coverUrl: 'https://x/web.jpg', mediaType: 'web-novel' as const },
      { id: 'b', coverUrl: undefined, mediaType: 'light-novel' as const },
      { id: 'm', coverUrl: 'https://x/manga.jpg', mediaType: 'manga' as const },
    ];
    const cover = workCover({ ...work, coverEditionId: 'b' }, editions);
    expect(cover.url).toBe('https://x/manga.jpg');
    expect(cover.mediaType).toBe('manga');
  });

  it('どれにも表紙が無ければ URL 無し（文字の表紙）で、媒体は最初の Edition', () => {
    const cover = workCover(work, [{ id: 'w', mediaType: 'web-novel' }]);
    expect(cover).toEqual({ url: undefined, title: '本好きの下剋上', mediaType: 'web-novel' });
  });
});

describe('fallbackCoverText', () => {
  it('副題を落とす', () => {
    expect(fallbackCoverText('本好きの下剋上 ~司書になるためには手段を選んでいられません~', 'light-novel')).toEqual({
      title: '本好きの下剋上',
      media: '文庫',
    });
    expect(fallbackCoverText('薬屋のひとりごと（コミック版）').title).toBe('薬屋のひとりごと');
  });

  it('最初の区切りが早すぎる時は題名全体を使う', () => {
    expect(fallbackCoverText('Re:ゼロから始める異世界生活').title).toBe('Re:ゼロから始める異…');
  });

  it('12 字を超えたら切って … を付ける', () => {
    expect(fallbackCoverText('転生したらスライムだった件について考える').title).toBe('転生したらスライムだっ…');
    expect([...fallbackCoverText('転生したらスライムだった件について考える').title]).toHaveLength(12);
  });

  it('空の題名でも何か出す', () => {
    expect(fallbackCoverText('  ', 'anime')).toEqual({ title: '—', media: 'ANIME' });
  });
});

describe('sizedCoverUrl', () => {
  const base = 'https://s4.anilist.co/file/anilistcdn/media/anime/cover/';

  it('行の大きさは small、横並び・棚・詳細は medium にする', () => {
    expect(sizedCoverUrl(`${base}small/bx1-a.jpg`, 'xs')).toBe(`${base}small/bx1-a.jpg`);
    expect(sizedCoverUrl(`${base}large/bx1-a.jpg`, 's')).toBe(`${base}small/bx1-a.jpg`);
    expect(sizedCoverUrl(`${base}small/bx1-a.jpg`, 'm')).toBe(`${base}medium/bx1-a.jpg`);
    expect(sizedCoverUrl(`${base}small/bx1-a.jpg`, 'l')).toBe(`${base}medium/bx1-a.jpg`);
    expect(sizedCoverUrl(`${base}small/bx1-a.jpg`, 'fill')).toBe(`${base}medium/bx1-a.jpg`);
    expect(sizedCoverUrl('https://s4.anilist.co/file/anilistcdn/media/manga/cover/small/bx2-b.png', 'l')).toBe(
      'https://s4.anilist.co/file/anilistcdn/media/manga/cover/medium/bx2-b.png',
    );
  });

  it('AniList 以外はそのまま', () => {
    expect(sizedCoverUrl('https://example.com/cover/small/a.jpg', 'l')).toBe('https://example.com/cover/small/a.jpg');
  });
});
