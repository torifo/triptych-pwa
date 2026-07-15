import type { MediaType, Status } from '../db/schema';

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
