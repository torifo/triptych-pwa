import type { DatePrecision, DetectionEvent, MediaType, Release, Source, Status } from '../db/schema';

export const MEDIA_TYPE_LABELS: Record<MediaType, string> = {
  'web-novel': 'WEB',
  'light-novel': '文庫',
  manga: '漫画',
  anime: 'ANIME',
};

export const STATUS_LABELS: Record<Status, string> = {
  plan: '積み',
  active: '進行中',
  paused: '中断',
  done: '完了',
  dropped: '切り',
};

export const PROGRESS_UNIT_LABELS: Record<'volume' | 'episode' | 'part', string> = {
  volume: '巻',
  episode: '話',
  part: '部分',
};

// メディア種別ごとの進捗単位の既定値（anime→話 / web-novel→部分 / manga・light-novel→巻）
export const DEFAULT_PROGRESS_UNIT: Record<MediaType, 'volume' | 'episode' | 'part'> = {
  'web-novel': 'part',
  'light-novel': 'volume',
  manga: 'volume',
  anime: 'episode',
};

export const MEDIA_TYPES: MediaType[] = ['web-novel', 'light-novel', 'manga', 'anime'];
export const STATUSES: Status[] = ['plan', 'active', 'paused', 'done', 'dropped'];

// ---------------------------------------------------------------------------
// ソース名
// ---------------------------------------------------------------------------

/** 検索結果・ソース一覧で使う名称。 */
export const SOURCE_LABELS: Record<Source, string> = {
  anilist: 'AniList',
  narou: 'なろう',
  openbd: 'openBD',
  ndl: 'NDL',
  syoboi: 'しょぼいカレンダー',
  kakuyomu: 'カクヨム',
  manual: '手動',
};

/** 同期状態（最終同期・エラー一覧）で使う名称。`detect` はアニメ化検知。 */
const SYNC_KEY_LABELS: Record<string, string> = {
  anilist: 'AniList',
  narou: 'なろう',
  openbd: 'openBD',
  ndl: 'NDL Search',
  syoboi: 'しょぼかる',
  kakuyomu: 'カクヨム',
  manual: '手動',
  detect: '検知',
};

export function syncKeyLabel(key: string): string {
  return SYNC_KEY_LABELS[key] ?? key;
}

// ---------------------------------------------------------------------------
// Release
// ---------------------------------------------------------------------------

export const PRECISION_LABELS: Record<DatePrecision, string> = {
  day: '日',
  month: '月',
  year: '年',
};

/** 手動 Release フォームの種類ラベル。 */
export const RELEASE_KIND_LABELS: Record<Release['kind'], string> = {
  volume: '巻（発売）',
  episode: '話（放送・更新）',
  'season-start': '新期開始',
  'stream-start': '配信開始',
};

/** メディア種別ごとに選べる Release の種類（先頭が既定）。 */
export const RELEASE_KINDS_BY_MEDIA: Record<MediaType, Release['kind'][]> = {
  'web-novel': ['episode', 'volume'],
  'light-novel': ['volume'],
  manga: ['volume'],
  anime: ['episode', 'stream-start', 'season-start'],
};

// ---------------------------------------------------------------------------
// アニメ化検知
// ---------------------------------------------------------------------------

export const DETECTION_VIA_LABELS: Record<NonNullable<DetectionEvent['via']>, string> = {
  relation: '原作から',
  sequel: '続編',
  title: 'タイトル一致',
};

export const AIRING_STATUS_LABELS: Record<DetectionEvent['candidate']['airingStatus'], string> = {
  NOT_YET_RELEASED: '放送前',
  RELEASING: '放送中',
  FINISHED: '放送済',
  CANCELLED: '中止',
  HIATUS: '休止中',
};

const ANIME_FORMAT_LABELS: Record<string, string> = {
  TV: 'TV',
  TV_SHORT: 'TVショート',
  MOVIE: '映画',
  SPECIAL: 'スペシャル',
  OVA: 'OVA',
  ONA: 'ONA',
  MUSIC: '音楽',
};

export function animeFormatLabel(format: string): string {
  return ANIME_FORMAT_LABELS[format] ?? format;
}
