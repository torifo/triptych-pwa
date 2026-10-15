<script lang="ts">
  import type { Edition, Release } from '../db/schema';
  import { displayReleaseTime, formatReleaseDate } from '../dates';
  import { isPostponed } from './release-view';
  import { displayReleaseLabel } from './labels';
  import type { CoverSize, CoverSource } from './cover';
  import Cover from './Cover.svelte';
  import MediaBadge from './MediaBadge.svelte';
  import OnAirLamp from './OnAirLamp.svelte';

  let {
    release,
    edition,
    workTitle,
    onclick,
    accent = false,
    emphasis = true,
    streaming = [],
    cover,
    isNew = false,
    timeOnly = false,
    coverSize = 'xs',
    onAir = false,
  }: {
    release: Release;
    edition: Edition | undefined;
    workTitle: string;
    onclick: () => void;
    /** 作品名を太くする（直近の予定など、強調してよい行だけ） */
    accent?: boolean;
    /** 日付を濃くする（false なら muted） */
    emphasis?: boolean;
    /** この Edition で配信が始まっているサービス名。次話の行にだけ添える */
    streaming?: readonly string[];
    /** 行の左に出す表紙（xs） */
    cover?: CoverSource;
    /** まだ見ていない（新着）。直近の行に NEW の印を付ける */
    isNew?: boolean;
    /** 日の一覧など、見出しに日付がある所では日付を省いて時刻（無ければ種類の言葉）だけ出す */
    timeOnly?: boolean;
    /** 表紙の大きさ（既定 xs）。カレンダーの週では s */
    coverSize?: CoverSize;
    /** いま放送中。時刻の下に ON AIR のランプを出す */
    onAir?: boolean;
  } = $props();

  // 日付と時刻を分け、時刻を日付の下に等幅で揃える（番組表の組み方）
  // 時計表記の設定では 25:30 を翌日の 1:30 として出す（保存している値はそのまま）
  const shown = $derived(displayReleaseTime(release));
  const day = $derived(formatReleaseDate({ date: shown.date, precision: release.precision }));
  const postponed = $derived(isPostponed(release));
  const services = $derived(release.kind === 'episode' ? streaming.join('・') : '');
  // 時刻の無い行に出す種類の言葉（timeOnly の時）
  const KIND_WORDS: Record<Release['kind'], string> = {
    volume: '発売',
    episode: '放送',
    'season-start': '放送開始',
    'stream-start': '配信',
  };
</script>

<button type="button" class="release-row row-hover" class:accent {onclick}>
  <span class="when mono" class:muted={!emphasis}>
    {#if timeOnly}
      <span class="day">{shown.time ? `${shown.nextDay ? '翌' : ''}${shown.time}` : KIND_WORDS[release.kind]}</span>
    {:else}
      <span class="day">{day}</span>
      {#if shown.time}
        <span class="time">{shown.time}</span>
      {/if}
    {/if}
    {#if onAir}
      <span class="lamp"><OnAirLamp label="ON AIR" /></span>
    {/if}
  </span>
  {#if cover}
    <!-- 作品名は右に文字で出すので、表紙は読み上げない -->
    <span class="thumb" aria-hidden="true">
      <Cover src={cover.url} title={cover.title} mediaType={cover.mediaType} size={coverSize} />
    </span>
  {/if}
  <span class="body">
    <span class="head">
      <span class="title">{workTitle}</span>
      {#if isNew}
        <span class="new mono">NEW</span>
      {/if}
      {#if edition}
        <MediaBadge mediaType={edition.mediaType} />
      {/if}
    </span>
    {#if release.label || postponed || services}
      <span class="meta">
        <span class="label" class:shrink={!!services}>{displayReleaseLabel(release)}</span>
        {#if services}
          <span class="services">{services} 配信中</span>
        {/if}
        {#if postponed}
          <span class="postponed">延期</span>
        {/if}
      </span>
    {/if}
  </span>
</button>

<style>
  /* 新着の印: 黒地に白（ダークでは反転）。媒体の箱（枠線）と見分ける */
  .new {
    flex-shrink: 0;
    padding: 0 4px;
    background: var(--fg);
    color: var(--bg);
    font-size: 9px;
    font-weight: 500;
    letter-spacing: 0.08em;
    line-height: 1.6;
  }
  .release-row {
    display: flex;
    align-items: center;
    gap: 0.625rem;
    width: 100%;
    min-width: 0;
    padding: 0.5rem 0.25rem;
    border-bottom: 1px solid var(--line);
    text-align: left;
  }

  /* 日付の列は幅を揃える（'10/16(金)'・'2026年10月' が収まる幅） */
  .when {
    display: flex;
    flex-direction: column;
    flex-shrink: 0;
    min-width: 5.6em;
    font-size: 11px;
    line-height: 1.35;
    font-weight: 500;
    color: var(--fg);
  }
  .when.muted {
    font-weight: 400;
    color: var(--muted);
  }
  .day,
  .time {
    white-space: nowrap;
  }
  .time {
    font-size: 12px;
  }
  .lamp {
    display: flex;
    margin-top: 0.2rem;
  }

  .thumb {
    display: flex;
    flex-shrink: 0;
  }

  .body {
    display: flex;
    flex-direction: column;
    gap: 0.125rem;
    flex: 1;
    min-width: 0;
  }
  .head {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    min-width: 0;
  }
  .title {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 0.875rem;
    line-height: 1.4;
  }
  .accent .title {
    font-weight: 700;
  }

  .meta {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    min-width: 0;
    font-size: 11px;
    color: var(--muted);
  }
  .label,
  .services {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .label.shrink {
    flex-shrink: 0;
    max-width: 60%;
  }
  .postponed {
    white-space: nowrap;
    color: var(--fg);
  }
</style>
