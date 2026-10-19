import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '../api';
import {
  JOB_POLL_MS,
  MAX_POLL_FAILURES,
  RECENT_JOB_NOTE,
  describeJobError,
  fetchCurrentJob,
  formatElapsed,
  isAlreadyRunning,
  jobElapsed,
  jobOutcome,
  pollJob,
  startRecentJob,
  tailLines,
  type CatalogJob,
} from './jobs';

function job(patch: Partial<CatalogJob> = {}): CatalogJob {
  return {
    id: 'j1',
    kind: 'recent',
    state: 'running',
    startedAt: '2026-10-15T03:00:00+00:00',
    finishedAt: null,
    exitCode: null,
    lines: [],
    ...patch,
  };
}

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

describe('API', () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    localStorage.clear();
    fetchMock.mockReset();
    vi.stubGlobal('fetch', fetchMock);
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('差分更新を POST /api/catalog/jobs/recent で始め、ジョブを返す', async () => {
    fetchMock.mockResolvedValue(json({ job: job() }, 202));
    expect(await startRecentJob()).toEqual(job());
    expect(fetchMock).toHaveBeenCalledWith('/api/catalog/jobs/recent', { method: 'POST', headers: {} });
  });

  it('実行中なら 409（already running）の ApiError', async () => {
    fetchMock.mockResolvedValue(json({ detail: 'already running' }, 409));
    const err = await startRecentJob().catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(isAlreadyRunning(err)).toBe(true);
  });

  it('今のジョブを GET /api/catalog/jobs/current で読む（無ければ null）', async () => {
    fetchMock.mockResolvedValueOnce(json({ job: job({ state: 'succeeded', exitCode: 0 }) }));
    expect(await fetchCurrentJob()).toMatchObject({ state: 'succeeded', exitCode: 0 });
    expect(fetchMock.mock.calls[0][0]).toBe('/api/catalog/jobs/current');

    fetchMock.mockResolvedValueOnce(json({ job: null }));
    expect(await fetchCurrentJob()).toBeNull();
  });

  it('形の違う応答は null として扱う', async () => {
    fetchMock.mockResolvedValueOnce(json({}));
    expect(await fetchCurrentJob()).toBeNull();
    fetchMock.mockResolvedValueOnce(json({ job: { id: 'x' } }));
    expect(await fetchCurrentJob()).toBeNull();
  });

  it('lines が無い・配列でない時は空にする', async () => {
    fetchMock.mockResolvedValueOnce(json({ job: { ...job(), lines: undefined } }));
    expect((await fetchCurrentJob())?.lines).toEqual([]);
  });
});

describe('isAlreadyRunning', () => {
  it('409 だけ', () => {
    expect(isAlreadyRunning(new ApiError(409, 'x', 'already running'))).toBe(true);
    expect(isAlreadyRunning(new ApiError(404, 'x'))).toBe(false);
    expect(isAlreadyRunning(new TypeError('Failed to fetch'))).toBe(false);
  });
});

describe('describeJobError', () => {
  it('409 は失敗ではなく、別の更新が走っていると伝える', () => {
    expect(describeJobError(new ApiError(409, 'x', 'already running'))).toBe(
      '別の更新が実行中です（ターミナルや前回の実行）。終わるまで待ってください',
    );
  });

  it('API が無い（404・405）、トークン、collector のエラー、つながらない', () => {
    expect(describeJobError(new ApiError(404, 'x', 'Not Found'))).toContain('この機能がありません');
    expect(describeJobError(new ApiError(405, 'x'))).toContain('この機能がありません');
    expect(describeJobError(new ApiError(401, 'x'))).toContain('トークン');
    expect(describeJobError(new ApiError(500, 'x'))).toBe('collector でエラーが起きました（500）');
    expect(describeJobError(new ApiError(422, 'x'))).toBe('受け付けられませんでした（422）');
    expect(describeJobError(new TypeError('Failed to fetch'))).toBe('collector に接続できませんでした');
    expect(describeJobError(new Error('boom'))).toBe('boom');
  });
});

describe('表示', () => {
  it('目安は 15〜30 分（NDL の混み具合で延びる）と、常駐させている時の週1の自動更新', () => {
    expect(RECENT_JOB_NOTE).toBe('目安 15〜30分。常駐させていれば毎週月曜 5:30 にも自動で走ります');
  });

  it('ログは末尾の n 行（空行は飛ばす）', () => {
    expect(tailLines(['a', '', 'b', '  ', 'c', 'd'], 3)).toEqual(['b', 'c', 'd']);
    expect(tailLines([], 3)).toEqual([]);
  });

  it('経過時間は 秒／分秒／時間分', () => {
    const start = '2026-10-15T03:00:00+00:00';
    const t0 = Date.parse(start);
    expect(formatElapsed(start, t0 + 45_000)).toBe('45秒');
    expect(formatElapsed(start, t0 + (12 * 60 + 3) * 1000)).toBe('12分03秒');
    expect(formatElapsed(start, t0 + (62 * 60 + 9) * 1000)).toBe('1時間02分');
    expect(formatElapsed(start, t0 - 5000)).toBe('0秒');
    expect(formatElapsed('bad', t0)).toBe('—');
  });

  it('マイクロ秒付きの時刻も読める', () => {
    const start = '2026-10-15T03:00:00.123456+00:00';
    expect(formatElapsed(start, Date.parse('2026-10-15T03:01:00.123Z'))).toBe('1分00秒');
  });

  it('結果の言葉: 実行中・終わった・失敗（終了コード）', () => {
    expect(jobOutcome(job())).toEqual({ kind: 'running', text: '差分更新を実行中' });
    expect(jobOutcome(job({ state: 'succeeded', exitCode: 0 }))).toEqual({ kind: 'ok', text: '差分更新が終わりました' });
    expect(jobOutcome(job({ state: 'failed', exitCode: 2 }))).toEqual({
      kind: 'error',
      text: '差分更新に失敗しました（終了コード 2）',
    });
    expect(jobOutcome(job({ state: 'failed' }))).toEqual({ kind: 'error', text: '差分更新に失敗しました' });
  });
});

describe('pollJob', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('3 秒ごとに読み、実行中でなくなったら止める', async () => {
    expect(JOB_POLL_MS).toBe(3000);
    const replies = [job(), job({ lines: ['== anilist =='] }), job({ state: 'succeeded', exitCode: 0 })];
    const fetchJob = vi.fn(async () => replies.shift() ?? null);
    const onUpdate = vi.fn();

    pollJob({ fetchJob, onUpdate });

    await vi.advanceTimersByTimeAsync(JOB_POLL_MS - 1);
    expect(fetchJob).toHaveBeenCalledTimes(0);
    await vi.advanceTimersByTimeAsync(1);
    expect(fetchJob).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(JOB_POLL_MS * 5);
    expect(fetchJob).toHaveBeenCalledTimes(3);
    expect(onUpdate.mock.calls.map(([j]) => j?.state)).toEqual(['running', 'running', 'succeeded']);
  });

  it('ジョブが消えた（collector の再起動）ら null を渡して止める', async () => {
    const fetchJob = vi.fn(async () => null);
    const onUpdate = vi.fn();
    pollJob({ fetchJob, onUpdate });
    await vi.advanceTimersByTimeAsync(JOB_POLL_MS * 3);
    expect(fetchJob).toHaveBeenCalledTimes(1);
    expect(onUpdate).toHaveBeenCalledWith(null);
  });

  it('止める関数を呼べば、それ以降は読まない', async () => {
    const fetchJob = vi.fn(async () => job());
    const stop = pollJob({ fetchJob, onUpdate: () => {} });
    await vi.advanceTimersByTimeAsync(JOB_POLL_MS);
    stop();
    await vi.advanceTimersByTimeAsync(JOB_POLL_MS * 3);
    expect(fetchJob).toHaveBeenCalledTimes(1);
  });

  it('読めない時は知らせて続け、続けて失敗したらあきらめる', async () => {
    const fetchJob = vi.fn(async (): Promise<CatalogJob | null> => {
      throw new TypeError('Failed to fetch');
    });
    const onError = vi.fn();
    pollJob({ fetchJob, onUpdate: () => {}, onError });

    await vi.advanceTimersByTimeAsync(JOB_POLL_MS * (MAX_POLL_FAILURES + 3));

    expect(fetchJob).toHaveBeenCalledTimes(MAX_POLL_FAILURES);
    expect(onError).toHaveBeenCalledTimes(MAX_POLL_FAILURES);
    expect(onError.mock.calls.map(([, gaveUp]) => gaveUp)).toEqual([
      ...Array(MAX_POLL_FAILURES - 1).fill(false),
      true,
    ]);
  });

  it('一度読めれば失敗の数え直し', async () => {
    let n = 0;
    const fetchJob = vi.fn(async () => {
      n += 1;
      if (n % 2 === 1) throw new TypeError('Failed to fetch');
      return n >= 12 ? job({ state: 'failed', exitCode: 1 }) : job();
    });
    const onError = vi.fn();
    pollJob({ fetchJob, onUpdate: () => {}, onError });
    await vi.advanceTimersByTimeAsync(JOB_POLL_MS * 20);
    expect(fetchJob).toHaveBeenCalledTimes(12);
    expect(onError.mock.calls.every(([, gaveUp]) => gaveUp === false)).toBe(true);
  });
});

describe('jobElapsed', () => {
  it('終わったジョブは終わった時刻まで、実行中は今まで', () => {
    const startedAt = '2026-10-15T03:00:00+00:00';
    const now = Date.parse('2026-10-15T03:20:00Z');
    expect(jobElapsed({ startedAt, finishedAt: '2026-10-15T03:14:30+00:00' }, now)).toBe('14分30秒');
    expect(jobElapsed({ startedAt, finishedAt: null }, now)).toBe('20分00秒');
  });
});
