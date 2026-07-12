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

export const MEDIA_TYPES: MediaType[] = ['web-novel', 'light-novel', 'manga', 'anime'];
export const STATUSES: Status[] = ['plan', 'active', 'paused', 'done', 'dropped'];
