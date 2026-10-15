import Dexie, { type EntityTable, type Transaction } from 'dexie';

export type MediaType = 'web-novel' | 'light-novel' | 'manga' | 'anime';
export type Status = 'plan' | 'active' | 'paused' | 'done' | 'dropped';
export type Source = 'anilist' | 'narou' | 'openbd' | 'ndl' | 'syoboi' | 'kakuyomu' | 'manual';
export type DatePrecision = 'day' | 'month' | 'year';
export type AiringStatus = 'NOT_YET_RELEASED' | 'RELEASING' | 'FINISHED' | 'CANCELLED' | 'HIATUS';

export interface Work {
  id: string; // nanoid
  title: string; // 代表タイトル（日本語）
  titleNormalized: string; // 検知照合用（NFKC・空白/記号除去・小文字化）
  aliases: string[]; // 略称・英題
  coverEditionId?: string; // 代表カバーに使う Edition
  createdAt: string;
  updatedAt: string; // ISO 8601
}

export interface SourceRef {
  source: Source;
  externalId: string; // ncode / AniList id / ISBN / TID / kakuyomu id
  url: string;
}

export interface EditionProgress {
  unit: 'volume' | 'episode' | 'part'; // 巻 / 話 / なろう部分
  current: number;
  total?: number;
}

export interface Edition {
  id: string;
  workId: string; // → Work
  mediaType: MediaType;
  title: string; // Edition固有タイトル（例: 「〜（コミック版）」）
  sourceRefs: SourceRef[]; // 複数ソース紐付け可（anime: anilist+syoboi 等）
  coverUrl?: string;
  status: Status;
  progress?: EditionProgress; // US-002 現在位置
  linkSource: 'auto' | 'manual'; // US-001 リンク由来（検知却下の判定に使用）
  note?: string;
  completed?: boolean; // v2: 原作側の完結（なろう end=0 の連載、カクヨム完結など）
  syncHints?: EditionSyncHints; // v2
  createdAt: string;
  updatedAt: string;
}

export interface EditionSyncHints {
  ndlLine?: string; // 採用した NDL の line キー（'' = 本編）
  anilistSourceId?: string; // 検知用に解決した AniList NOVEL/MANGA の id
  syoboiTid?: string; // しょぼいカレンダーの TID
}

export interface ReleaseDateHistoryEntry {
  date: string;
  changedAt: string;
}

export interface Release {
  id: string;
  editionId: string; // → Edition
  kind: 'volume' | 'episode' | 'season-start' | 'stream-start';
  label: string; // 「第12巻」「第2期 第1話」
  date: string; // YYYY-MM-DD（month/year 精度では月初・年初で埋めた並べ替え用の値）
  precision?: DatePrecision; // v2（未設定は 'day'）
  time?: string; // v2 アニメの放送時刻（JST・朝5時区切り表記 例 '25:30'）
  externalId?: string; // v2 volume=ISBN13 / episode='<anilistId>:<n>' / season-start=anilistId
  origin?: 'sync' | 'manual'; // v2（未設定は 'sync'）。manual は同期で上書きも削除もしない
  rolling?: boolean; // v2 配信中のサービスの「次回の配信」（stream-start）。日付は同期ごとに動き、延期にも新着にもしない
  dateHistory: ReleaseDateHistoryEntry[]; // 延期履歴 (US-003)
  source: Source;
  seenAt?: string; // ユーザーが新着マークを確認した時刻
  createdAt: string;
  updatedAt: string;
}

export interface DetectionCandidate {
  // 検知されたアニメ
  source: 'anilist';
  externalId: string;
  title: string;
  airingStatus: AiringStatus;
  format?: string; // v2 TV / MOVIE / OVA ...
  startDate?: string;
  startPrecision?: DatePrecision; // v2
}

export interface DetectionEvent {
  // US-004
  id: string;
  workId: string;
  candidate: DetectionCandidate;
  matchScore: number; // タイトル照合スコア (0-1)。relation/sequel 由来は 1
  via?: 'relation' | 'sequel' | 'title'; // v2（未設定は 'title'）
  state: 'pending' | 'accepted' | 'rejected';
  createdAt: string;
}

export interface MetaEntry {
  key: string;
  value: unknown;
}

export class TriptychDB extends Dexie {
  works!: EntityTable<Work, 'id'>;
  editions!: EntityTable<Edition, 'id'>;
  releases!: EntityTable<Release, 'id'>;
  detections!: EntityTable<DetectionEvent, 'id'>;
  meta!: EntityTable<MetaEntry, 'key'>;

  constructor(name = 'triptych') {
    super(name);
    this.version(1).stores({
      works: 'id, titleNormalized',
      editions: 'id, workId, status, mediaType',
      releases: 'id, editionId, date',
      detections: 'id, workId, state',
      meta: 'key',
    });
    this.version(2)
      .stores({
        releases: 'id, editionId, date, [editionId+kind+externalId]',
      })
      .upgrade(upgradeToV2);
  }
}

/**
 * v1 → v2: 既定値（precision/origin/via）を補い、監査で見つかった誤データ
 * （anime 以外の Edition に付いた AniList 由来の Release）を消す。
 */
export async function upgradeToV2(tx: Transaction): Promise<void> {
  const editions = (await tx.table('editions').toArray()) as Edition[];
  const nonAnime = new Set(editions.filter((e) => e.mediaType !== 'anime').map((e) => e.id));
  const releases = tx.table('releases');
  await releases
    .filter((r: Release) => r.source === 'anilist' && nonAnime.has(r.editionId))
    .delete();
  await releases.toCollection().modify((r: Release) => {
    if (!r.precision) r.precision = 'day';
    if (!r.origin) r.origin = r.source === 'manual' ? 'manual' : 'sync';
  });
  await tx
    .table('detections')
    .toCollection()
    .modify((d: DetectionEvent) => {
      if (!d.via) d.via = 'title';
    });
}

export function createDB(name?: string): TriptychDB {
  return new TriptychDB(name);
}

export const db = new TriptychDB();
