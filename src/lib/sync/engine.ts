// Sync Engine (FR-004, specs/mvp-core/design.md "Sync Engine" / "同期の対象と頻度").
//
// アプリ起動時(1h スロットル付き) + 手動プルで発火し、追跡中(status ∈
// {plan, active, paused}) の Edition が持つ sourceRef を集めて collector を
// 叩き、結果を Release/Edition.progress へマージする。ソース間は並列、
// ソース内は直列。ソース障害は該当ソースのみスキップして続行し、
// `meta.lastSyncErrors` に記録する（US-005）。
//
// UI 購読用の状態は `./status.ts` が Svelte 5 の $state で公開するが、この
// モジュールからは意図的に import しない — vitest（本リポジトリの
// vitest.config.ts）は svelte プラグインを通さないため `$state` のコンパイル
// 変換が効かず、.svelte.ts を import すると `$state is not defined` で
// 落ちる。engine.ts と status.ts を繋ぐ配線は、App.svelte の起動時フックを
// 追加する後続タスクが担う（このタスクの制約: src/lib/sync/** のみ変更可）。

import { apiGet, ApiError } from '../api';
import { db as defaultDB, type TriptychDB } from '../db/schema';
import type { Edition, EditionProgress, Release, Source, SourceRef } from '../db/schema';
import { listEditions, listReleasesByEdition, mergeRelease, updateEditionProgress, deleteRelease } from '../db/store';
import { runDetection } from './detect';

// ---------------------------------------------------------------------------
// Collector DTO shapes (mirrors backend/dto.py + backend/adapters/*.py).
// Kept local since the collector is a separate Python process — no shared
// type-generation pipeline exists yet.
// ---------------------------------------------------------------------------

interface ReleaseDTO {
  kind: Release['kind'];
  label: string;
  date: string;
  source: Source;
  externalId?: string | null;
}

interface MediaDetailDTO {
  source: 'anilist';
  externalId: string;
  mediaType: string;
  title: string;
  cover?: string | null;
  url: string;
  releases: ReleaseDTO[];
}

interface NarouNovelDetail {
  source: 'narou';
  ncode: string;
  title: string;
  author: string;
  generalAllNo: number;
  generalLastup: string;
  completed: boolean;
  url: string;
}

interface KakuyomuWorkDTO {
  source: 'kakuyomu';
  externalId: string;
  mediaType: 'web-novel';
  title: string;
  author?: string | null;
  episodeCount?: number | null;
  url: string;
  releases: ReleaseDTO[];
}

// ---------------------------------------------------------------------------
// Sync status / options
// ---------------------------------------------------------------------------

const SYNC_META_KEY = 'sync';
const THROTTLE_MS = 60 * 60 * 1000; // 1h (design.md 「同期の対象と頻度」)
const TRACKED_STATUSES: Edition['status'][] = ['plan', 'active', 'paused'];
const HANDLED_SOURCES: Source[] = ['anilist', 'narou', 'kakuyomu', 'ndl'];

export interface SyncSourceError {
  message: string;
  occurredAt: string; // ISO 8601
}

export type SyncErrors = Partial<Record<Source, SyncSourceError>>;

interface SyncMeta {
  lastSyncAt?: string;
  lastSyncErrors?: SyncErrors;
}

export interface SyncOptions {
  /** 1h スロットルを無視して強制実行する（手動プル用）。collector 側の TTL キャッシュも `?refresh=1` で無視させる。 */
  force?: boolean;
  database?: TriptychDB;
  /** テスト用の時刻注入フック。省略時は `new Date()`。 */
  now?: () => Date;
}

export interface SyncRunResult {
  ran: boolean;
  skipped?: 'throttled';
  errors: SyncErrors;
}

// ---------------------------------------------------------------------------
// runSync
// ---------------------------------------------------------------------------

export async function runSync(opts: SyncOptions = {}): Promise<SyncRunResult> {
  const database = opts.database ?? defaultDB;
  const now = opts.now ?? (() => new Date());
  const force = opts.force ?? false;

  const metaEntry = await database.meta.get(SYNC_META_KEY);
  const meta: SyncMeta = (metaEntry?.value as SyncMeta | undefined) ?? {};

  if (!force && meta.lastSyncAt) {
    const elapsed = now().getTime() - new Date(meta.lastSyncAt).getTime();
    if (elapsed < THROTTLE_MS) {
      return { ran: false, skipped: 'throttled', errors: meta.lastSyncErrors ?? {} };
    }
  }

  const editions = (await listEditions(database)).filter((edition) =>
    TRACKED_STATUSES.includes(edition.status),
  );

  const bySource = new Map<Source, Array<{ edition: Edition; ref: SourceRef }>>();
  for (const edition of editions) {
    for (const ref of edition.sourceRefs) {
      if (!HANDLED_SOURCES.includes(ref.source)) continue;
      const list = bySource.get(ref.source) ?? [];
      list.push({ edition, ref });
      bySource.set(ref.source, list);
    }
  }

  const errors: SyncErrors = {};

  // ソース間は並列、ソース内は直列（design.md 「同期の対象と頻度」）。
  await Promise.all(
    Array.from(bySource.entries()).map(async ([source, items]) => {
      const handler = SOURCE_HANDLERS[source as HandledSource];
      for (const { edition, ref } of items) {
        try {
          await handler(edition, ref, database, force);
        } catch (err) {
          // ソース障害はそのソースのみスキップして続行し、記録する（US-005）。
          errors[source] = {
            message: err instanceof ApiError ? err.message : errorMessage(err),
            occurredAt: now().toISOString(),
          };
        }
      }
    }),
  );

  const nextMeta: SyncMeta = {
    lastSyncAt: now().toISOString(),
    lastSyncErrors: errors,
  };
  await database.meta.put({ key: SYNC_META_KEY, value: nextMeta });

  // アニメ化検知（FR-005）。24hスロットルは同期の force とは独立
  // （design.md「検知スロットル」は Work 毎24h固定）。検知側の失敗
  // (collector 障害等) で同期結果そのものを壊さないよう握りつぶす。
  try {
    await runDetection({ database, now: opts.now });
  } catch {
    // 検知は補助機能。ここで投げ直すと runSync 全体が失敗扱いになるため無視する。
  }

  return { ran: true, errors };
}

/**
 * App 起動時フックから呼ばれることを想定した薄いラッパー。
 * 実際の配線（App.svelte への組み込み）は後続タスクが行う。
 */
export async function maybeAutoSync(opts: SyncOptions = {}): Promise<SyncRunResult> {
  return runSync({ ...opts, force: opts.force ?? false });
}

function errorMessage(err: unknown): string {
  if (err instanceof Error) return err.message;
  return String(err);
}

// ---------------------------------------------------------------------------
// Per-source handlers
// ---------------------------------------------------------------------------

type HandledSource = 'anilist' | 'narou' | 'kakuyomu' | 'ndl';

type SourceHandler = (
  edition: Edition,
  ref: SourceRef,
  database: TriptychDB,
  force: boolean,
) => Promise<void>;

const SOURCE_HANDLERS: Record<HandledSource, SourceHandler> = {
  anilist: syncAnilist,
  narou: syncNarou,
  kakuyomu: syncKakuyomu,
  ndl: syncNdl,
};

/**
 * anime: season-start Release を通常マージし、AniList の nextAiringEpisode
 * (kind='episode') は「次話」のローリング更新として、同じ kind/source の
 * 既存 Release を常に1件に保つ（放送済み話は残さない）。
 */
async function syncAnilist(
  edition: Edition,
  ref: SourceRef,
  database: TriptychDB,
): Promise<void> {
  const dto = await apiGet<MediaDetailDTO>(`/api/anilist/media/${ref.externalId}`);

  for (const release of dto.releases) {
    if (release.kind === 'season-start') {
      await mergeRelease(
        {
          editionId: edition.id,
          kind: 'season-start',
          label: release.label,
          date: release.date,
          source: 'anilist',
        },
        database,
      );
    } else if (release.kind === 'episode') {
      await mergeRollingRelease(
        edition.id,
        'episode',
        'anilist',
        release.label,
        release.date,
        database,
      );
    }
  }
}

/**
 * web-novel (なろう): 全部分数(generalAllNo)が Edition.progress.total と
 * 異なる場合のみ progress を更新し、最終掲載日を kind='episode'・
 * label='第N部分' の Release としてマージする。
 */
async function syncNarou(
  edition: Edition,
  ref: SourceRef,
  database: TriptychDB,
  force: boolean,
): Promise<void> {
  const dto = await apiGet<NarouNovelDetail>(
    `/api/narou/novel/${ref.externalId}`,
    force ? { refresh: '1' } : undefined,
  );

  if (edition.progress?.total === dto.generalAllNo) return;

  const nextProgress: EditionProgress = {
    unit: 'part',
    current: edition.progress?.current ?? dto.generalAllNo,
    total: dto.generalAllNo,
  };
  await updateEditionProgress(edition.id, nextProgress, database);

  await mergeRelease(
    {
      editionId: edition.id,
      kind: 'episode',
      label: `第${dto.generalAllNo}部分`,
      date: dto.generalLastup,
      source: 'narou',
    },
    database,
  );
}

/**
 * web-novel (カクヨム): なろうと同様、episodeCount が progress.total と
 * 異なる場合のみ progress を更新し、adapter が返す最終更新 Release をマージする。
 */
async function syncKakuyomu(
  edition: Edition,
  ref: SourceRef,
  database: TriptychDB,
): Promise<void> {
  const dto = await apiGet<KakuyomuWorkDTO>(`/api/kakuyomu/work/${ref.externalId}`);

  if (dto.episodeCount == null) return;
  if (edition.progress?.total === dto.episodeCount) return;

  const nextProgress: EditionProgress = {
    unit: 'episode',
    current: edition.progress?.current ?? dto.episodeCount,
    total: dto.episodeCount,
  };
  await updateEditionProgress(edition.id, nextProgress, database);

  const release = dto.releases[0];
  if (release) {
    await mergeRelease(
      {
        editionId: edition.id,
        kind: release.kind,
        label: release.label,
        date: release.date,
        source: 'kakuyomu',
      },
      database,
    );
  }
}

/**
 * light-novel (NDL/openBD 2段構え): シリーズ新刊 Release (kind='volume') を
 * mergeRelease でマージする。dateHistory の処理は mergeRelease 側に一任。
 */
async function syncNdl(
  edition: Edition,
  ref: SourceRef,
  database: TriptychDB,
  force: boolean,
): Promise<void> {
  const title = ref.externalId || edition.title;
  const releases = await apiGet<ReleaseDTO[]>(
    '/api/ndl/series',
    force ? { title, refresh: '1' } : { title },
  );

  for (const release of releases) {
    if (!release.date) continue; // openBD が発売日を持たない候補はスキップ
    await mergeRelease(
      {
        editionId: edition.id,
        kind: release.kind,
        label: release.label,
        date: release.date,
        source: 'ndl',
      },
      database,
    );
  }
}

/**
 * 「次話」のようなローリング Release（同一 kind/source につき常に1件だけを
 * 保持し、放送済み/既読分は残さない）を扱う共通ヘルパー。
 *
 * 同じ label（= 同じ話数）の既存 Release があれば mergeRelease に委譲して
 * dateHistory を保持したまま日付だけ更新する（延期検知）。label が変わった
 * (= 次の話に進んだ) 場合は古いローリング Release を削除してから新規作成する
 * — 放送済み話の履歴を残さないという仕様のため、この場合は dateHistory を
 * 引き継がない。
 */
async function mergeRollingRelease(
  editionId: string,
  kind: Release['kind'],
  source: Source,
  label: string,
  date: string,
  database: TriptychDB,
): Promise<void> {
  const existing = (await listReleasesByEdition(editionId, database)).filter(
    (r) => r.kind === kind && r.source === source,
  );
  const sameLabel = existing.some((r) => r.label === label);
  if (!sameLabel) {
    for (const stale of existing) {
      await deleteRelease(stale.id, database);
    }
  }
  await mergeRelease({ editionId, kind, label, date, source }, database);
}
