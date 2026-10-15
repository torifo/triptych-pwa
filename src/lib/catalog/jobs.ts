// カタログの差分更新ジョブ（/api/catalog/jobs/*）。型は backend/api/catalog_jobs.py の Job の写し。

import { ApiError, apiGet, apiPost } from '../api';

export type CatalogJobState = 'running' | 'succeeded' | 'failed';

export interface CatalogJob {
  id: string;
  kind: 'recent';
  state: CatalogJobState;
  /** UTC の ISO 8601（表示は JST に直す）。 */
  startedAt: string;
  finishedAt: string | null;
  exitCode: number | null;
  /** ログの末尾（空行を除いて最大 40 行）。 */
  lines: string[];
}

/** 実行中に状態を読み直す間隔。 */
export const JOB_POLL_MS = 3000;
/** 差分更新（build --recent）の所要時間の目安。 */
export const RECENT_JOB_MINUTES = 15;
/** 読み直しが続けて失敗したら、読むのをやめる回数。 */
export const MAX_POLL_FAILURES = 5;

const JOB_STATES: readonly unknown[] = ['running', 'succeeded', 'failed'];

/** 応答のジョブを確かめる。形が違えば null。 */
function toJob(raw: unknown): CatalogJob | null {
  if (typeof raw !== 'object' || raw === null) return null;
  const j = raw as Record<string, unknown>;
  if (typeof j.id !== 'string' || !JOB_STATES.includes(j.state) || typeof j.startedAt !== 'string') return null;
  return {
    id: j.id,
    kind: 'recent',
    state: j.state as CatalogJobState,
    startedAt: j.startedAt,
    finishedAt: typeof j.finishedAt === 'string' ? j.finishedAt : null,
    exitCode: typeof j.exitCode === 'number' ? j.exitCode : null,
    lines: Array.isArray(j.lines) ? j.lines.filter((line): line is string => typeof line === 'string') : [],
  };
}

/** 差分更新を始める。実行中なら 409 の ApiError（isAlreadyRunning で見分ける）。 */
export async function startRecentJob(): Promise<CatalogJob> {
  const res = await apiPost<{ job?: unknown }>('/api/catalog/jobs/recent');
  const job = toJob(res?.job);
  if (!job) throw new Error('collector の応答の形が違います');
  return job;
}

/** 今の（直近の）ジョブ。collector を再起動すると忘れるので null。 */
export async function fetchCurrentJob(): Promise<CatalogJob | null> {
  const res = await apiGet<{ job?: unknown }>('/api/catalog/jobs/current');
  return toJob(res?.job);
}

/** 別の更新が走っている（409）。collector の再起動直後は、current が null でもこうなる。 */
export function isAlreadyRunning(err: unknown): boolean {
  return err instanceof ApiError && err.status === 409;
}

/** ボタンの下に出す理由。 */
export function describeJobError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 409) return '別の更新が実行中です（ターミナルや前回の実行）。終わるまで待ってください';
    if (err.status === 404 || err.status === 405) {
      return 'collector にこの機能がありません。collector を新しくして起動し直してください';
    }
    if (err.status === 401 || err.status === 403) {
      return `collector のトークンが違います（${err.status}）。「collector 接続」を見てください`;
    }
    if (err.status >= 500) return `collector でエラーが起きました（${err.status}）`;
    return `受け付けられませんでした（${err.status}）`;
  }
  if (err instanceof TypeError) return 'collector に接続できませんでした';
  if (err instanceof Error) return err.message;
  return String(err);
}

/** ログの末尾 n 行（空行は飛ばす）。 */
export function tailLines(lines: readonly string[], n: number): string[] {
  return lines.filter((line) => line.trim() !== '').slice(-n);
}

function parseIso(iso: string): number {
  // マイクロ秒は Date.parse が嫌う環境がある
  return Date.parse(iso.trim().replace(/(\.\d{3})\d+/, '$1'));
}

/** 始まってからの時間を「45秒」「12分03秒」「1時間02分」で。読めない時刻は —。 */
export function formatElapsed(fromIso: string, toMs: number): string {
  const from = parseIso(fromIso);
  if (Number.isNaN(from)) return '—';
  const total = Math.max(0, Math.floor((toMs - from) / 1000));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  const pad = (n: number) => String(n).padStart(2, '0');
  if (hours > 0) return `${hours}時間${pad(minutes)}分`;
  if (minutes > 0) return `${minutes}分${pad(seconds)}秒`;
  return `${seconds}秒`;
}

/** 終わった時刻（無ければ今）までの所要時間。 */
export function jobElapsed(job: Pick<CatalogJob, 'startedAt' | 'finishedAt'>, now: number = Date.now()): string {
  const end = job.finishedAt ? parseIso(job.finishedAt) : now;
  return formatElapsed(job.startedAt, Number.isNaN(end) ? now : end);
}

export function jobOutcome(job: Pick<CatalogJob, 'state' | 'exitCode'>): { kind: 'running' | 'ok' | 'error'; text: string } {
  if (job.state === 'running') return { kind: 'running', text: '差分更新を実行中' };
  if (job.state === 'succeeded') return { kind: 'ok', text: '差分更新が終わりました' };
  return {
    kind: 'error',
    text: job.exitCode === null ? '差分更新に失敗しました' : `差分更新に失敗しました（終了コード ${job.exitCode}）`,
  };
}

export interface PollJobOptions {
  fetchJob?: () => Promise<CatalogJob | null>;
  /** 読むたびに呼ぶ。実行中でない・null なら、それを最後に止まる。 */
  onUpdate: (job: CatalogJob | null) => void;
  /** 読めなかった時。gaveUp が true なら、それを最後に止まる。 */
  onError?: (err: unknown, gaveUp: boolean) => void;
  intervalMs?: number;
}

/** 実行中のジョブを intervalMs ごとに読み直す。戻り値で止める。 */
export function pollJob({
  fetchJob = fetchCurrentJob,
  onUpdate,
  onError,
  intervalMs = JOB_POLL_MS,
}: PollJobOptions): () => void {
  let stopped = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let failures = 0;

  const schedule = () => {
    if (!stopped) timer = setTimeout(tick, intervalMs);
  };

  async function tick() {
    let job: CatalogJob | null;
    try {
      job = await fetchJob();
    } catch (err) {
      if (stopped) return;
      failures += 1;
      const gaveUp = failures >= MAX_POLL_FAILURES;
      onError?.(err, gaveUp);
      if (gaveUp) stopped = true;
      schedule();
      return;
    }
    if (stopped) return;
    failures = 0;
    onUpdate(job);
    if (!job || job.state !== 'running') stopped = true;
    schedule();
  }

  schedule();
  return () => {
    stopped = true;
    if (timer !== undefined) clearTimeout(timer);
  };
}
