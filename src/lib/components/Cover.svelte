<script lang="ts">
  // 表紙。比率 2:3。画像が無い・読み込めない時は題名を縦に組んだ「文字の表紙」に代える。
  import type { Snippet } from 'svelte';
  import type { MediaType } from '../db/schema';
  import { COVER_WIDTH, fallbackCoverText, sizedCoverUrl, type CoverSize } from './cover';
  import OnAirLamp from './OnAirLamp.svelte';
  import { isSafeHttpUrl } from './release-view';

  let {
    src,
    title,
    mediaType,
    size = 's',
    onAir = false,
    badge,
  }: {
    src?: string | null;
    title: string;
    mediaType?: MediaType;
    size?: CoverSize;
    /** 今日放送・配信がある時に右上へ ON AIR ランプ */
    onAir?: boolean;
    /** 表紙の左下に載せるもの（等幅の時刻など） */
    badge?: Snippet;
  } = $props();

  let failedSrc = $state<string | undefined>();

  const safe = $derived(src && isSafeHttpUrl(src) ? sizedCoverUrl(src, size) : undefined);
  const url = $derived(safe && failedSrc !== safe ? safe : undefined);
  const text = $derived(fallbackCoverText(title, mediaType));
  const width = $derived(size === 'fill' ? '100%' : `${COVER_WIDTH[size]}px`);
</script>

<div class="cover" data-size={size} style:width>
  {#if url}
    <img src={url} alt={title} loading="lazy" decoding="async" onerror={() => (failedSrc = url)} />
  {:else}
    <div class="paper" role="img" aria-label={title}>
      {#if size !== 'xs'}<span class="mark" aria-hidden="true"></span>{/if}
      <!-- xs（28px）は縦組みが何列にも折れて読めないので、頭の1字だけを大きく -->
      <span class="paper-title" aria-hidden="true">{size === 'xs' ? [...text.title][0] : text.title}</span>
      {#if text.media && size !== 'xs'}<span class="mono paper-media" aria-hidden="true">{text.media}</span>{/if}
    </div>
  {/if}
  {#if onAir}
    <span class="lamp"><OnAirLamp ring /></span>
  {/if}
  {#if badge}
    <span class="badge">{@render badge()}</span>
  {/if}
</div>

<style>
  .cover {
    position: relative;
    aspect-ratio: 2 / 3;
    flex-shrink: 0;
    border-radius: 2px;
    overflow: hidden;
    background: var(--cover-ph);
  }
  /* 画像の上にも細い縁を引いて、白い表紙が地に溶けないようにする */
  .cover::after {
    content: '';
    position: absolute;
    inset: 0;
    border: 1px solid var(--cover-edge);
    border-radius: inherit;
    pointer-events: none;
  }
  img {
    display: block;
    width: 100%;
    height: 100%;
    object-fit: cover;
  }

  .paper {
    position: absolute;
    inset: 0;
    background: var(--paper);
    color: var(--paper-ink);
    padding: 12% 10%;
    display: flex;
    flex-direction: column;
    align-items: flex-end;
  }
  .mark {
    position: absolute;
    top: 9%;
    left: 10%;
    width: 9%;
    aspect-ratio: 1;
    min-width: 4px;
    background: var(--paper-ink);
  }
  .paper-title {
    writing-mode: vertical-rl;
    font-weight: 700;
    letter-spacing: 0.08em;
    line-height: 1.15;
    overflow: hidden;
    flex: 1;
    min-height: 0;
  }
  .paper-media {
    position: absolute;
    left: 10%;
    bottom: 7%;
    font-size: 0.6rem;
    letter-spacing: 0.08em;
    opacity: 0.75;
  }
  [data-size='xs'] .paper {
    padding: 0;
    align-items: center;
    justify-content: center;
  }
  [data-size='xs'] .paper-title {
    writing-mode: horizontal-tb;
    flex: 0 0 auto;
    font-size: 14px;
    line-height: 1;
  }
  [data-size='s'] .paper-title {
    font-size: 11px;
  }
  [data-size='m'] .paper-title {
    font-size: 12px;
  }
  [data-size='l'] .paper-title {
    font-size: 16px;
  }
  [data-size='l'] .paper-media {
    font-size: 0.7rem;
  }
  [data-size='fill'] .paper-title {
    font-size: clamp(12px, 3.6vw, 15px);
  }

  .lamp {
    position: absolute;
    top: 5px;
    right: 5px;
    display: flex;
  }
  .badge {
    position: absolute;
    left: 0;
    bottom: 0;
    max-width: 100%;
  }
</style>
