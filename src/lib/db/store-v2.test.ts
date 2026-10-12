import { beforeEach, describe, expect, it } from 'vitest';
import { createDB, type Edition, type TriptychDB } from './schema';
import {
  addSourceRef,
  createEdition,
  createManualRelease,
  createRelease,
  createWork,
  deleteRelease,
  getRelease,
  listReleasesByEdition,
  mergeRelease,
  removeSourceRef,
  setEditionCompleted,
  updateEditionSyncHints,
  updateRelease,
} from './store';

let db: TriptychDB;
let dbCounter = 0;

beforeEach(() => {
  dbCounter += 1;
  db = createDB(`triptych-store-v2-${dbCounter}-${Date.now()}`);
});

async function newEdition(mediaType: Edition['mediaType'] = 'light-novel'): Promise<Edition> {
  const work = await createWork({ title: '本好きの下剋上' }, db);
  return createEdition({ workId: work.id, mediaType, title: 'Edition' }, db);
}

const NOW = new Date('2026-10-13T12:00:00+09:00');
const ISBN_A = '9784864727471';
const ISBN_B = '9784864727488';

describe('mergeRelease: ISBN（externalId）キー', () => {
  it('同じ label で別 ISBN の 2 冊は別 Release になる', async () => {
    const edition = await newEdition();
    const a = await mergeRelease(
      { editionId: edition.id, kind: 'volume', label: '第3巻', date: '2026-10-10', externalId: ISBN_A, source: 'ndl' },
      db,
    );
    const b = await mergeRelease(
      { editionId: edition.id, kind: 'volume', label: '第3巻', date: '2026-11-10', externalId: ISBN_B, source: 'ndl' },
      db,
    );
    expect(a.id).not.toBe(b.id);
    expect(await listReleasesByEdition(edition.id, db)).toHaveLength(2);
  });

  it('同期を繰り返しても日付が往復せず、dateHistory も増えない', async () => {
    const edition = await newEdition();
    const sync = async () => {
      await mergeRelease(
        { editionId: edition.id, kind: 'volume', label: '第3巻', date: '2026-10-10', externalId: ISBN_A, source: 'ndl' },
        db,
      );
      await mergeRelease(
        { editionId: edition.id, kind: 'volume', label: '第3巻', date: '2026-11-10', externalId: ISBN_B, source: 'ndl' },
        db,
      );
    };
    await sync();
    await sync();
    await sync();

    const releases = await listReleasesByEdition(edition.id, db);
    expect(releases).toHaveLength(2);
    const byIsbn = new Map(releases.map((r) => [r.externalId, r]));
    expect(byIsbn.get(ISBN_A)?.date).toBe('2026-10-10');
    expect(byIsbn.get(ISBN_B)?.date).toBe('2026-11-10');
    for (const r of releases) expect(r.dateHistory).toEqual([]);
  });

  it('変化がなければ書き込まず、updatedAt も変わらない', async () => {
    const edition = await newEdition();
    const input = {
      editionId: edition.id,
      kind: 'volume' as const,
      label: '第3巻',
      date: '2026-10-10',
      externalId: ISBN_A,
      source: 'ndl' as const,
    };
    const first = await mergeRelease(input, db);
    const second = await mergeRelease(input, db);
    expect(second).toEqual(first);
    expect((await getRelease(first.id, db))?.updatedAt).toBe(first.updatedAt);
  });

  it('externalId が同じなら label が変わっても同じ Release を更新する（記録なし）', async () => {
    const edition = await newEdition();
    const first = await mergeRelease(
      { editionId: edition.id, kind: 'volume', label: '第3巻', date: '2026-10-10', externalId: ISBN_A, source: 'ndl' },
      db,
    );
    const renamed = await mergeRelease(
      { editionId: edition.id, kind: 'volume', label: '第3巻（新装版）', date: '2026-10-10', externalId: ISBN_A, source: 'ndl' },
      db,
    );
    expect(renamed.id).toBe(first.id);
    expect(renamed.label).toBe('第3巻（新装版）');
    expect(renamed.dateHistory).toEqual([]);
    expect(await listReleasesByEdition(edition.id, db)).toHaveLength(1);
  });

  it('externalId が無ければ従来どおり label で照合する', async () => {
    const edition = await newEdition();
    const first = await mergeRelease(
      { editionId: edition.id, kind: 'volume', label: '第3巻', date: '2026-10-10', source: 'ndl' },
      db,
    );
    const again = await mergeRelease(
      { editionId: edition.id, kind: 'volume', label: '第3巻', date: '2026-10-24', source: 'ndl' },
      db,
    );
    expect(again.id).toBe(first.id);
    expect(again.dateHistory.map((h) => h.date)).toEqual(['2026-10-10']);
  });

  it('kind が違えば externalId が同じでも別 Release', async () => {
    const edition = await newEdition('anime');
    await mergeRelease(
      { editionId: edition.id, kind: 'season-start', label: '第2期', date: '2026-10-04', externalId: '1', source: 'anilist' },
      db,
    );
    await mergeRelease(
      { editionId: edition.id, kind: 'episode', label: '第1話', date: '2026-10-04', externalId: '1', source: 'anilist' },
      db,
    );
    expect(await listReleasesByEdition(edition.id, db)).toHaveLength(2);
  });

  it('不正な日付は拒否する', async () => {
    const edition = await newEdition();
    await expect(
      mergeRelease(
        { editionId: edition.id, kind: 'volume', label: '第1巻', date: '2026-8', source: 'ndl' },
        db,
      ),
    ).rejects.toThrow(RangeError);
    expect(await listReleasesByEdition(edition.id, db)).toHaveLength(0);
  });
});

describe('mergeRelease: 精緻化と延期の区別', () => {
  async function seed(precision: 'day' | 'month' | 'year', date: string) {
    const edition = await newEdition();
    const merge = (next: { date: string; precision?: 'day' | 'month' | 'year'; time?: string; label?: string }) =>
      mergeRelease(
        {
          editionId: edition.id,
          kind: 'volume',
          label: next.label ?? '第3巻',
          date: next.date,
          precision: next.precision,
          time: next.time,
          externalId: ISBN_A,
          source: 'ndl',
        },
        db,
      );
    await merge({ date, precision });
    return merge;
  }

  it('month 2026-08 → day 2026-08-25 は精緻化なので記録しない', async () => {
    const merge = await seed('month', '2026-08-01');
    const merged = await merge({ date: '2026-08-25', precision: 'day' });
    expect(merged.date).toBe('2026-08-25');
    expect(merged.precision).toBe('day');
    expect(merged.dateHistory).toEqual([]);
  });

  it('month 2026-08 → day 2026-09-02 は延期なので旧日付を記録する', async () => {
    const merge = await seed('month', '2026-08-01');
    const merged = await merge({ date: '2026-09-02', precision: 'day' });
    expect(merged.date).toBe('2026-09-02');
    expect(merged.dateHistory.map((h) => h.date)).toEqual(['2026-08-01']);
  });

  it('同精度（day）で日付が違えば記録する', async () => {
    const merge = await seed('day', '2026-08-25');
    const merged = await merge({ date: '2026-08-26', precision: 'day' });
    expect(merged.dateHistory.map((h) => h.date)).toEqual(['2026-08-25']);
  });

  it('同精度（month）で月が違えば記録する', async () => {
    const merge = await seed('month', '2026-08-01');
    const merged = await merge({ date: '2026-09-01', precision: 'month' });
    expect(merged.dateHistory.map((h) => h.date)).toEqual(['2026-08-01']);
  });

  it('year → month / day で年内に収まる精緻化は記録しない', async () => {
    const merge = await seed('year', '2026-01-01');
    const refined = await merge({ date: '2026-08-01', precision: 'month' });
    expect(refined.dateHistory).toEqual([]);
    const refinedAgain = await merge({ date: '2026-08-25', precision: 'day' });
    expect(refinedAgain.dateHistory).toEqual([]);
    expect(refinedAgain.date).toBe('2026-08-25');
  });

  it('year → 翌年の日付は延期', async () => {
    const merge = await seed('year', '2026-01-01');
    const merged = await merge({ date: '2027-01-05', precision: 'day' });
    expect(merged.dateHistory.map((h) => h.date)).toEqual(['2026-01-01']);
  });

  it('day → month の粗化で旧日付がその月に収まるなら延期ではない（precision は反映）', async () => {
    const merge = await seed('day', '2026-08-25');
    const merged = await merge({ date: '2026-08-01', precision: 'month' });
    expect(merged.precision).toBe('month');
    expect(merged.dateHistory).toEqual([]);
  });

  it('day → 別の月の month は延期', async () => {
    const merge = await seed('day', '2026-08-25');
    const merged = await merge({ date: '2026-09-01', precision: 'month' });
    expect(merged.dateHistory.map((h) => h.date)).toEqual(['2026-08-25']);
  });

  it('time・label の更新は記録なしで反映し、time は消せる', async () => {
    const merge = await seed('day', '2026-10-16');
    const withTime = await merge({ date: '2026-10-16', precision: 'day', time: '25:30' });
    expect(withTime.time).toBe('25:30');
    expect(withTime.dateHistory).toEqual([]);

    const relabeled = await merge({ date: '2026-10-16', precision: 'day', time: '25:30', label: '第3巻 特典付き' });
    expect(relabeled.label).toBe('第3巻 特典付き');
    expect(relabeled.dateHistory).toEqual([]);

    const cleared = await merge({ date: '2026-10-16', precision: 'day', label: '第3巻 特典付き' });
    expect(cleared.time).toBeUndefined();
    const stored = await getRelease(cleared.id, db);
    expect(stored?.time).toBeUndefined();
    expect(stored?.dateHistory).toEqual([]);
  });

  it('month / year の date は月初・年初に揃えて保存する', async () => {
    const edition = await newEdition();
    const month = await mergeRelease(
      { editionId: edition.id, kind: 'volume', label: 'a', date: '2026-08-15', precision: 'month', source: 'ndl' },
      db,
    );
    const year = await mergeRelease(
      { editionId: edition.id, kind: 'volume', label: 'b', date: '2026-08-15', precision: 'year', source: 'ndl' },
      db,
    );
    expect(month.date).toBe('2026-08-01');
    expect(year.date).toBe('2026-01-01');
  });
});

describe('mergeRelease: 手動 Release は同期が触らない', () => {
  it('externalId が同じ手動 Release は上書きも重複作成もされない', async () => {
    const edition = await newEdition();
    const manual = await createManualRelease(
      { editionId: edition.id, kind: 'volume', label: '第5巻', date: '2026-12-10', externalId: ISBN_A },
      db,
    );
    const merged = await mergeRelease(
      { editionId: edition.id, kind: 'volume', label: '第5巻 (NDL)', date: '2027-01-10', precision: 'month', time: '10:00', externalId: ISBN_A, source: 'ndl' },
      db,
    );
    expect(merged).toEqual(manual);
    const releases = await listReleasesByEdition(edition.id, db);
    expect(releases).toHaveLength(1);
    expect(releases[0]).toEqual(manual);
  });

  it('label が同じ手動 Release も上書きも重複作成もされない', async () => {
    const edition = await newEdition();
    const manual = await createManualRelease(
      { editionId: edition.id, kind: 'volume', label: '第5巻', date: '2026-12-10' },
      db,
    );
    await mergeRelease(
      { editionId: edition.id, kind: 'volume', label: '第5巻', date: '2027-01-10', source: 'ndl' },
      db,
    );
    const releases = await listReleasesByEdition(edition.id, db);
    expect(releases).toEqual([manual]);
  });

  it('同期で作られた Release を手で編集すると、以後の同期は触らない', async () => {
    const edition = await newEdition();
    const synced = await mergeRelease(
      { editionId: edition.id, kind: 'volume', label: '第3巻', date: '2026-10-10', externalId: ISBN_A, source: 'ndl' },
      db,
    );
    expect(synced.origin).toBe('sync');

    const edited = await updateRelease(synced.id, { date: '2026-10-12' }, db);
    expect(edited.origin).toBe('manual');

    const afterSync = await mergeRelease(
      { editionId: edition.id, kind: 'volume', label: '第3巻', date: '2026-10-24', externalId: ISBN_A, source: 'ndl' },
      db,
    );
    expect(afterSync.date).toBe('2026-10-12');
    expect(afterSync.dateHistory).toEqual([]);
    expect(await listReleasesByEdition(edition.id, db)).toHaveLength(1);
  });

  it('同じ label の同期 Release と手動 Release が並ぶ場合は、同期側だけを更新する', async () => {
    const edition = await newEdition();
    const manual = await createManualRelease(
      { editionId: edition.id, kind: 'volume', label: '第5巻', date: '2026-12-10' },
      db,
    );
    // 手動と同期で label が衝突している状態（label キーの照合のみ）
    const synced = await createRelease(
      { editionId: edition.id, kind: 'volume', label: '第5巻', date: '2026-12-11', source: 'ndl' },
      db,
    );
    const merged = await mergeRelease(
      { editionId: edition.id, kind: 'volume', label: '第5巻', date: '2026-12-12', source: 'ndl' },
      db,
    );
    expect(merged.id).toBe(synced.id);
    expect(merged.date).toBe('2026-12-12');
    expect(await getRelease(manual.id, db)).toEqual(manual);
  });
});

describe('mergeRelease: 過去日の新規 Release は既読にする', () => {
  const base = { kind: 'volume' as const, source: 'ndl' as const };

  it('日付が過去の新規 Release は seenAt が作成時刻で埋まる', async () => {
    const edition = await newEdition();
    const merged = await mergeRelease(
      { ...base, editionId: edition.id, label: '第1巻', date: '2026-10-12' },
      db,
      { now: NOW },
    );
    expect(merged.seenAt).toBe(NOW.toISOString());
    expect(merged.createdAt).toBe(NOW.toISOString());
  });

  it('今日・未来の日付は新着のまま（seenAt なし）', async () => {
    const edition = await newEdition();
    const today = await mergeRelease(
      { ...base, editionId: edition.id, label: '第1巻', date: '2026-10-13' },
      db,
      { now: NOW },
    );
    const future = await mergeRelease(
      { ...base, editionId: edition.id, label: '第2巻', date: '2026-12-10' },
      db,
      { now: NOW },
    );
    expect(today.seenAt).toBeUndefined();
    expect(future.seenAt).toBeUndefined();
  });

  it('「今日」は JST の暦日で判定する（JST 00:30 ならまだ UTC では前日）', async () => {
    const edition = await newEdition();
    const justAfterMidnight = new Date('2026-10-14T00:30:00+09:00');
    const yesterdayJst = await mergeRelease(
      { ...base, editionId: edition.id, label: '第1巻', date: '2026-10-13' },
      db,
      { now: justAfterMidnight },
    );
    const todayJst = await mergeRelease(
      { ...base, editionId: edition.id, label: '第2巻', date: '2026-10-14' },
      db,
      { now: justAfterMidnight },
    );
    expect(yesterdayJst.seenAt).toBeDefined();
    expect(todayJst.seenAt).toBeUndefined();
  });

  it('月精度は月末を過ぎてから過去扱い', async () => {
    const edition = await newEdition();
    const lastMonth = await mergeRelease(
      { ...base, editionId: edition.id, label: '先月', date: '2026-09-01', precision: 'month' },
      db,
      { now: NOW },
    );
    const thisMonth = await mergeRelease(
      { ...base, editionId: edition.id, label: '今月', date: '2026-10-01', precision: 'month' },
      db,
      { now: NOW },
    );
    expect(lastMonth.seenAt).toBeDefined();
    expect(thisMonth.seenAt).toBeUndefined();
  });

  it('markPastSeen: false なら過去日でも新着のまま', async () => {
    const edition = await newEdition();
    const merged = await mergeRelease(
      { ...base, editionId: edition.id, label: '第1巻', date: '2020-01-01' },
      db,
      { markPastSeen: false, now: NOW },
    );
    expect(merged.seenAt).toBeUndefined();
  });

  it('既存 Release の更新では seenAt を触らない', async () => {
    const edition = await newEdition();
    await mergeRelease({ ...base, editionId: edition.id, label: '第1巻', date: '2026-12-10' }, db, { now: NOW });
    const moved = await mergeRelease(
      { ...base, editionId: edition.id, label: '第1巻', date: '2026-10-01' },
      db,
      { now: NOW },
    );
    expect(moved.seenAt).toBeUndefined();
    expect(moved.dateHistory.map((h) => h.date)).toEqual(['2026-12-10']);
  });
});

describe('createManualRelease / updateRelease / deleteRelease', () => {
  it('createManualRelease は origin=manual・source=manual・既読で作る', async () => {
    const edition = await newEdition();
    const release = await createManualRelease(
      { editionId: edition.id, kind: 'volume', label: '第5巻', date: '2026-12-10' },
      db,
    );
    expect(release.origin).toBe('manual');
    expect(release.source).toBe('manual');
    expect(release.precision).toBe('day');
    expect(release.seenAt).toBeDefined();
    expect(await getRelease(release.id, db)).toEqual(release);
  });

  it('createManualRelease は精度・時刻・月初への正規化を扱う', async () => {
    const edition = await newEdition('anime');
    const month = await createManualRelease(
      { editionId: edition.id, kind: 'season-start', label: '第3期', date: '2027-01-20', precision: 'month' },
      db,
    );
    expect(month.precision).toBe('month');
    expect(month.date).toBe('2027-01-01');

    const day = await createManualRelease(
      { editionId: edition.id, kind: 'stream-start', label: 'Netflix', date: '2026-10-16', time: '25:30' },
      db,
    );
    expect(day.time).toBe('25:30');
  });

  it('createManualRelease は存在しない Edition・不正な日付を拒否する', async () => {
    const edition = await newEdition();
    await expect(
      createManualRelease({ editionId: 'nope', kind: 'volume', label: 'x', date: '2026-12-10' }, db),
    ).rejects.toThrow('Edition not found');
    await expect(
      createManualRelease({ editionId: edition.id, kind: 'volume', label: 'x', date: '12/10' }, db),
    ).rejects.toThrow(RangeError);
  });

  it('updateRelease: seenAt だけの更新では origin を変えない', async () => {
    const edition = await newEdition();
    const synced = await mergeRelease(
      { editionId: edition.id, kind: 'volume', label: '第3巻', date: '2026-12-10', source: 'ndl' },
      db,
    );
    const seen = await updateRelease(synced.id, { seenAt: '2026-10-13T00:00:00.000Z' }, db);
    expect(seen.origin).toBe('sync');
    expect((await getRelease(synced.id, db))?.origin).toBe('sync');
  });

  it('updateRelease: 値が変わらない保存では origin を変えない', async () => {
    const edition = await newEdition();
    const synced = await mergeRelease(
      { editionId: edition.id, kind: 'volume', label: '第3巻', date: '2026-12-10', source: 'ndl' },
      db,
    );
    const same = await updateRelease(
      synced.id,
      { label: '第3巻', date: '2026-12-10', precision: 'day', kind: 'volume' },
      db,
    );
    expect(same.origin).toBe('sync');
  });

  it('updateRelease: 内容を変えると origin=manual、origin を明示すれば同期に戻せる', async () => {
    const edition = await newEdition();
    const synced = await mergeRelease(
      { editionId: edition.id, kind: 'volume', label: '第3巻', date: '2026-12-10', source: 'ndl' },
      db,
    );
    const edited = await updateRelease(synced.id, { label: '第3巻（予約開始）' }, db);
    expect(edited.origin).toBe('manual');
    const restored = await updateRelease(synced.id, { origin: 'sync' }, db);
    expect(restored.origin).toBe('sync');
    expect((await getRelease(synced.id, db))?.origin).toBe('sync');
  });

  it('updateRelease: precision を month に変えると date を月初に揃え、time は undefined で消せる', async () => {
    const edition = await newEdition('anime');
    const created = await createManualRelease(
      { editionId: edition.id, kind: 'episode', label: '第1話', date: '2026-10-16', time: '25:30' },
      db,
    );
    const updated = await updateRelease(created.id, { precision: 'month', time: undefined }, db);
    expect(updated.precision).toBe('month');
    expect(updated.date).toBe('2026-10-01');
    const stored = await getRelease(created.id, db);
    expect(stored?.date).toBe('2026-10-01');
    expect(stored?.time).toBeUndefined();
  });

  it('updateRelease: 不正な日付・存在しない id を拒否する', async () => {
    const edition = await newEdition();
    const created = await createManualRelease(
      { editionId: edition.id, kind: 'volume', label: '第1巻', date: '2026-12-10' },
      db,
    );
    await expect(updateRelease(created.id, { date: 'soon' }, db)).rejects.toThrow(RangeError);
    await expect(updateRelease('nope', { label: 'x' }, db)).rejects.toThrow('Release not found');
    expect((await getRelease(created.id, db))?.date).toBe('2026-12-10');
  });

  it('deleteRelease は手動 Release も消せる', async () => {
    const edition = await newEdition();
    const created = await createManualRelease(
      { editionId: edition.id, kind: 'volume', label: '第1巻', date: '2026-12-10' },
      db,
    );
    await deleteRelease(created.id, db);
    expect(await listReleasesByEdition(edition.id, db)).toHaveLength(0);
  });
});

describe('addSourceRef / removeSourceRef', () => {
  const narou = { source: 'narou' as const, externalId: 'n4830bu', url: 'https://ncode.syosetu.com/n4830bu/' };

  it('sourceRef を追加でき、同じ source+externalId は重複させない', async () => {
    const edition = await newEdition('web-novel');
    const added = await addSourceRef(edition.id, narou, db);
    expect(added.sourceRefs).toEqual([narou]);

    const again = await addSourceRef(edition.id, { ...narou, url: 'https://example.com/' }, db);
    expect(again.sourceRefs).toEqual([narou]);
    expect(again.updatedAt).toBe(added.updatedAt);
  });

  it('source か externalId が違えば別の sourceRef として追加できる', async () => {
    const edition = await newEdition('web-novel');
    await addSourceRef(edition.id, narou, db);
    await addSourceRef(edition.id, { ...narou, externalId: 'n0000aa' }, db);
    const last = await addSourceRef(edition.id, { ...narou, source: 'kakuyomu' }, db);
    expect(last.sourceRefs).toHaveLength(3);
  });

  it('sourceRef を削除でき、無いものを消しても何も起きない', async () => {
    const edition = await newEdition('web-novel');
    const other = { source: 'kakuyomu' as const, externalId: '16816', url: 'https://kakuyomu.jp/works/16816' };
    await addSourceRef(edition.id, narou, db);
    const added = await addSourceRef(edition.id, other, db);

    const removed = await removeSourceRef(edition.id, { source: 'narou', externalId: 'n4830bu' }, db);
    expect(removed.sourceRefs).toEqual([other]);

    const untouched = await removeSourceRef(edition.id, { source: 'narou', externalId: 'n4830bu' }, db);
    expect(untouched.sourceRefs).toEqual([other]);
    expect(untouched.updatedAt).toBe(removed.updatedAt);
    expect(added.sourceRefs).toHaveLength(2);
  });

  it('存在しない Edition は拒否する', async () => {
    await expect(addSourceRef('nope', narou, db)).rejects.toThrow('Edition not found');
    await expect(removeSourceRef('nope', narou, db)).rejects.toThrow('Edition not found');
  });
});

describe('updateEditionSyncHints / setEditionCompleted', () => {
  it('syncHints は部分マージされ、既存のキーを保つ', async () => {
    const edition = await newEdition();
    const first = await updateEditionSyncHints(edition.id, { ndlLine: '' }, db);
    expect(first.syncHints).toEqual({ ndlLine: '' });

    const second = await updateEditionSyncHints(edition.id, { anilistSourceId: '98436' }, db);
    expect(second.syncHints).toEqual({ ndlLine: '', anilistSourceId: '98436' });

    const third = await updateEditionSyncHints(edition.id, { ndlLine: 'TOジュニア文庫' }, db);
    expect(third.syncHints).toEqual({ ndlLine: 'TOジュニア文庫', anilistSourceId: '98436' });
    expect((await db.editions.get(edition.id))?.syncHints).toEqual(third.syncHints);
  });

  it('undefined は変更なし、null は削除、全部消えたら syncHints ごと消える', async () => {
    const edition = await newEdition();
    await updateEditionSyncHints(edition.id, { ndlLine: 'a', anilistSourceId: '1' }, db);

    const keep = await updateEditionSyncHints(edition.id, { ndlLine: undefined, syoboiTid: '4000' }, db);
    expect(keep.syncHints).toEqual({ ndlLine: 'a', anilistSourceId: '1', syoboiTid: '4000' });

    const removed = await updateEditionSyncHints(
      edition.id,
      { ndlLine: null, anilistSourceId: null, syoboiTid: null },
      db,
    );
    expect(removed.syncHints).toBeUndefined();
    expect((await db.editions.get(edition.id))?.syncHints).toBeUndefined();
  });

  it('変化がなければ updatedAt を動かさない', async () => {
    const edition = await newEdition();
    const first = await updateEditionSyncHints(edition.id, { ndlLine: 'a' }, db);
    const same = await updateEditionSyncHints(edition.id, { ndlLine: 'a' }, db);
    expect(same.updatedAt).toBe(first.updatedAt);
  });

  it('setEditionCompleted は完結を切り替える', async () => {
    const edition = await newEdition('web-novel');
    expect(edition.completed).toBeUndefined();

    const done = await setEditionCompleted(edition.id, true, db);
    expect(done.completed).toBe(true);
    expect((await db.editions.get(edition.id))?.completed).toBe(true);

    const reopened = await setEditionCompleted(edition.id, false, db);
    expect(reopened.completed).toBe(false);
  });

  it('存在しない Edition は拒否する', async () => {
    await expect(updateEditionSyncHints('nope', { ndlLine: 'a' }, db)).rejects.toThrow('Edition not found');
    await expect(setEditionCompleted('nope', true, db)).rejects.toThrow('Edition not found');
  });
});
