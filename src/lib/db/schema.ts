import Dexie, { type EntityTable } from 'dexie';

export type MediaType = 'web-novel' | 'light-novel' | 'manga' | 'anime';
export type Status = 'plan' | 'active' | 'paused' | 'done' | 'dropped';
export type Source = 'anilist' | 'narou' | 'openbd' | 'ndl' | 'syoboi' | 'kakuyomu' | 'manual';

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
  createdAt: string;
  updatedAt: string;
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
  date: string; // YYYY-MM-DD（時刻不明も許容）
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
  airingStatus: 'NOT_YET_RELEASED' | 'RELEASING' | 'FINISHED';
  startDate?: string;
}

export interface DetectionEvent {
  // US-004
  id: string;
  workId: string;
  candidate: DetectionCandidate;
  matchScore: number; // タイトル照合スコア (0-1)
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
  }
}

export function createDB(name?: string): TriptychDB {
  return new TriptychDB(name);
}

export const db = new TriptychDB();
