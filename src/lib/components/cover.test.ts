import { describe, expect, it } from 'vitest';
import {
  coverSrcset,
  coverUrlOf,
  editionCover,
  editionDisplayCover,
  fallbackCoverText,
  sizedCoverUrl,
  workCover,
  workHeroCover,
} from './cover';

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
  type CoverWork = Parameters<typeof workCover>[0];
  const work: CoverWork = { title: '本好きの下剋上', coverEditionId: undefined };
  const anime = { id: 'a', coverUrl: 'https://x/anime.jpg', mediaType: 'anime' as const };
  const manga = { id: 'm', coverUrl: 'https://x/manga.jpg', mediaType: 'manga' as const };
  const novel = { id: 'n', coverUrl: 'https://x/novel.jpg', mediaType: 'light-novel' as const };
  const web = { id: 'w', coverUrl: 'https://x/web.jpg', mediaType: 'web-novel' as const };

  it('coverEditionId の表紙を最優先する', () => {
    expect(workCover({ ...work, coverEditionId: 'a' }, [anime, novel]).url).toBe('https://x/anime.jpg');
  });

  it('原作が小説なら文庫 → 漫画 → アニメの順', () => {
    const novelWork = { ...work, origin: 'novel' as const };
    expect(workCover(novelWork, [anime, manga, novel])).toEqual({ url: 'https://x/novel.jpg', title: '本好きの下剋上', mediaType: 'light-novel' });
    expect(workCover(novelWork, [anime, manga]).url).toBe('https://x/manga.jpg');
    expect(workCover(novelWork, [anime]).url).toBe('https://x/anime.jpg');
  });

  it('原作が漫画なら漫画 → 文庫 → アニメの順', () => {
    const mangaWork = { ...work, origin: 'manga' as const };
    expect(workCover(mangaWork, [anime, novel, manga]).url).toBe('https://x/manga.jpg');
    expect(workCover(mangaWork, [anime, novel]).url).toBe('https://x/novel.jpg');
  });

  it('アニメオリジナルならアニメ → 漫画 → 文庫の順', () => {
    const animeWork = { ...work, origin: 'anime' as const };
    expect(workCover(animeWork, [novel, manga, anime]).url).toBe('https://x/anime.jpg');
    expect(workCover(animeWork, [novel, manga]).url).toBe('https://x/manga.jpg');
  });

  it('原作が分からなければ文庫 → 漫画 → アニメの順（アニメの絵を先にしない）', () => {
    expect(workCover(work, [anime, manga, novel]).url).toBe('https://x/novel.jpg');
    expect(workCover(work, [anime, manga]).url).toBe('https://x/manga.jpg');
  });

  it('coverEditionId に表紙が無ければ原作の順で選ぶ', () => {
    const cover = workCover({ ...work, coverEditionId: 'b' }, [anime, { id: 'b', mediaType: 'light-novel' }, manga]);
    expect(cover.url).toBe('https://x/manga.jpg');
    expect(cover.mediaType).toBe('manga');
  });

  it('Web 小説の表紙は代表にしない', () => {
    const cover = workCover({ ...work, origin: 'novel' }, [web]);
    expect(cover).toEqual({ url: undefined, title: '本好きの下剋上', mediaType: 'web-novel' });
  });

  it('同じ媒体が複数なら最初の期（放送開始の早いもの）、開始日が無ければ登録の早いもの', () => {
    const s1 = { id: 's1', coverUrl: 'https://x/s1.jpg', mediaType: 'anime' as const, createdAt: '2026-10-03T00:00:00Z' };
    const s3 = { id: 's3', coverUrl: 'https://x/s3.jpg', mediaType: 'anime' as const, createdAt: '2026-10-01T00:00:00Z' };
    const s2 = { id: 's2', coverUrl: 'https://x/s2.jpg', mediaType: 'anime' as const, createdAt: '2026-10-02T00:00:00Z' };
    const animeWork = { ...work, origin: 'anime' as const };
    const starts = new Map([
      ['s1', { date: '2018-01-04' }],
      ['s2', { date: '2021-01-07' }],
      ['s3', { date: '2024-04-04' }],
    ]);
    expect(workCover(animeWork, [s3, s2, s1], starts).url).toBe('https://x/s1.jpg');
    // 開始日の分かる期が先、分からないものは登録順
    expect(workCover(animeWork, [s3, s2, s1], new Map([['s2', { date: '2021-01-07' }]])).url).toBe('https://x/s2.jpg');
    expect(workCover(animeWork, [s1, s2, s3]).url).toBe('https://x/s3.jpg');
  });

  it('どれにも表紙が無ければ URL 無し（文字の表紙）で、媒体は原作の順で最初にある Edition', () => {
    expect(workCover(work, [{ id: 'w', mediaType: 'web-novel' }])).toEqual({ url: undefined, title: '本好きの下剋上', mediaType: 'web-novel' });
    expect(workCover(work, [{ id: 'a', mediaType: 'anime' }, { id: 'n', mediaType: 'light-novel' }]).mediaType).toBe('light-novel');
    expect(workCover(work, []).mediaType).toBeUndefined();
  });
});

describe('editionDisplayCover', () => {
  const work = { title: '本好きの下剋上' };
  const anime = { id: 'a', coverUrl: 'https://x/anime.jpg', mediaType: 'anime' as const, title: '本好きの下剋上 第3期' };
  const novel = { id: 'n', coverUrl: 'https://x/novel.jpg', mediaType: 'light-novel' as const, title: '本好きの下剋上' };
  const bare = (mediaType: 'anime' | 'manga' | 'light-novel' | 'web-novel') => ({ id: `bare-${mediaType}`, coverUrl: undefined, mediaType, title: '本好きの下剋上 版' });

  it('自分の表紙があればそれ（題名は Work 名）', () => {
    expect(editionDisplayCover(anime, work, [anime, novel])).toEqual({ url: 'https://x/anime.jpg', title: '本好きの下剋上', mediaType: 'anime' });
  });

  it('表紙の無い漫画・文庫・アニメは他の媒体の表紙を借りず、文字の表紙', () => {
    for (const mediaType of ['manga', 'light-novel', 'anime'] as const) {
      const edition = bare(mediaType);
      expect(editionDisplayCover(edition, work, [anime, novel, edition])).toEqual({ url: undefined, title: '本好きの下剋上', mediaType });
    }
  });

  it('表紙の無い Web 小説だけは同じ作品の文庫の表紙を借りる（媒体は自分のまま）', () => {
    const edition = bare('web-novel');
    expect(editionDisplayCover(edition, work, [anime, novel, edition])).toEqual({ url: 'https://x/novel.jpg', title: '本好きの下剋上', mediaType: 'web-novel' });
    expect(editionDisplayCover(edition, work, [anime, edition]).url).toBeUndefined();
  });

  it('Work が無ければ Edition の題名で組む', () => {
    expect(editionDisplayCover(bare('manga'), undefined, [anime]).title).toBe('本好きの下剋上 版');
  });
});

describe('workHeroCover', () => {
  const work = { title: 'ゆるキャン△', origin: 'manga' as const };
  const s1 = { id: 's1', coverUrl: 'https://x/s1.jpg', mediaType: 'anime' as const };
  const s3 = { id: 's3', coverUrl: 'https://x/s3.jpg', mediaType: 'anime' as const };
  const manga = { id: 'm', coverUrl: 'https://x/manga.jpg', mediaType: 'manga' as const };
  const bareNovel = { id: 'n', coverUrl: undefined, mediaType: 'light-novel' as const };

  it('開いている Edition に自分の表紙があればそれ（期ごとの絵）', () => {
    expect(workHeroCover(work, [s1, s3, manga], s3)).toEqual({ url: 'https://x/s3.jpg', title: 'ゆるキャン△', mediaType: 'anime' });
  });

  it('無ければ作品の代表表紙（原作の絵）', () => {
    expect(workHeroCover(work, [s1, manga, bareNovel], bareNovel)).toEqual({ url: 'https://x/manga.jpg', title: 'ゆるキャン△', mediaType: 'manga' });
    expect(workHeroCover(work, [s1, manga], undefined).url).toBe('https://x/manga.jpg');
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

describe('coverSrcset', () => {
  const base = 'https://s4.anilist.co/file/anilistcdn/media/anime/cover/';

  it('幅いっぱい（棚・PC の詳細）の AniList は medium と large を並べ、ブラウザに選ばせる', () => {
    expect(coverSrcset(`${base}small/bx1-a.jpg`, 'fill')).toBe(`${base}medium/bx1-a.jpg 230w, ${base}large/bx1-a.jpg 460w`);
  });

  it('大きさの決まった表紙と AniList 以外は出さない', () => {
    expect(coverSrcset(`${base}small/bx1-a.jpg`, 'l')).toBeUndefined();
    expect(coverSrcset('https://example.com/a.jpg', 'fill')).toBeUndefined();
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
