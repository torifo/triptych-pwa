import { describe, expect, it } from 'vitest';
import type { Edition, Source } from '../db/schema';
import { sourceRefSyncNote, sourceRefUse } from './source-ref-sync';

function edition(
  mediaType: Edition['mediaType'],
  over: Partial<Pick<Edition, 'status' | 'syncHints'>> = {},
): Pick<Edition, 'mediaType' | 'status' | 'syncHints'> {
  return { mediaType, status: 'active', ...over };
}

describe('sourceRefUse（engine.planTasks / detect.resolveSourceId に合わせる）', () => {
  it('アニメは AniList を Release 同期に使い、それ以外は使わない', () => {
    expect(sourceRefUse(edition('anime'), 'anilist')).toBe('sync');
    expect(sourceRefUse(edition('anime'), 'openbd')).toBe('unused');
    expect(sourceRefUse(edition('anime'), 'narou')).toBe('unused');
  });

  it('しょぼかるの sourceRef は TID が未確定の間だけ効く', () => {
    expect(sourceRefUse(edition('anime'), 'syoboi')).toBe('sync');
    expect(sourceRefUse(edition('anime', { syncHints: { syoboiTid: '7000' } }), 'syoboi')).toBe('unused');
  });

  it('Web 小説はなろう・カクヨムを同期に使い、AniList は検知にだけ使う', () => {
    expect(sourceRefUse(edition('web-novel'), 'narou')).toBe('sync');
    expect(sourceRefUse(edition('web-novel'), 'kakuyomu')).toBe('sync');
    expect(sourceRefUse(edition('web-novel'), 'anilist')).toBe('detect');
    expect(sourceRefUse(edition('web-novel'), 'openbd')).toBe('unused');
  });

  it('文庫・漫画は openBD を使わず、AniList は検知にだけ使う', () => {
    for (const mediaType of ['light-novel', 'manga'] as const) {
      expect(sourceRefUse(edition(mediaType), 'openbd')).toBe('unused');
      expect(sourceRefUse(edition(mediaType), 'anilist')).toBe('detect');
      expect(sourceRefUse(edition(mediaType), 'narou')).toBe('unused');
    }
  });

  it('文庫・漫画の NDL は系列が未固定の時だけ効く（固定済みは saved line が優先）', () => {
    expect(sourceRefUse(edition('manga'), 'ndl')).toBe('ndl-series');
    expect(sourceRefUse(edition('manga', { syncHints: { ndlLine: '' } }), 'ndl')).toBe('ndl-series-fixed');
    expect(sourceRefUse(edition('light-novel', { syncHints: { ndlLine: 'x' } }), 'ndl')).toBe('ndl-series-fixed');
  });
});

describe('sourceRefSyncNote', () => {
  it('同期に使われるソースは「次回の同期から反映」', () => {
    expect(sourceRefSyncNote(edition('web-novel'), 'narou')).toBe('次回の同期から反映されます');
    expect(sourceRefSyncNote(edition('anime'), 'anilist')).toBe('次回の同期から反映されます');
  });

  it('文庫・漫画に ISBN（openBD）を足しても同期されないと伝える', () => {
    const note = sourceRefSyncNote(edition('light-novel'), 'openbd');
    expect(note).toContain('NDL');
    expect(note).toContain('同期に使われません');
    expect(note).not.toContain('次回の同期から反映');
  });

  it('文庫・漫画の AniList は検知用だと伝える', () => {
    const note = sourceRefSyncNote(edition('manga'), 'anilist');
    expect(note).toContain('検知');
    expect(note).not.toContain('次回の同期から反映');
  });

  it('NDL は系列の取得に使われるか、固定済みで変わらないかを伝える', () => {
    expect(sourceRefSyncNote(edition('manga'), 'ndl')).toBe('次回の同期から、この系列で発売日を取得します');
    expect(sourceRefSyncNote(edition('manga', { syncHints: { ndlLine: '' } }), 'ndl')).toContain('変わりません');
  });

  it('アニメ・Web 小説で使われないソースは参照用と伝える', () => {
    expect(sourceRefSyncNote(edition('anime'), 'openbd')).toBe('このソースは同期には使われません（参照用）');
    expect(sourceRefSyncNote(edition('web-novel'), 'openbd')).toBe('このソースは同期には使われません（参照用）');
  });

  it('追跡中でない Edition は、同期に使うソースでも対象外だと伝える', () => {
    expect(sourceRefSyncNote(edition('web-novel', { status: 'done' }), 'narou')).toBe(
      'ステータスが「完了」の間は同期の対象外です',
    );
    expect(sourceRefSyncNote(edition('manga', { status: 'dropped' }), 'anilist')).toBe(
      'ステータスが「切り」の間は同期の対象外です',
    );
  });

  it('どの媒体・ソースの組み合わせでも空文字にならない', () => {
    const sources: Source[] = ['anilist', 'narou', 'openbd', 'ndl', 'syoboi', 'kakuyomu', 'manual'];
    for (const mediaType of ['web-novel', 'light-novel', 'manga', 'anime'] as const) {
      for (const source of sources) {
        expect(sourceRefSyncNote(edition(mediaType), source).length).toBeGreaterThan(0);
      }
    }
  });
});
