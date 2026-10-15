<script lang="ts">
  // Work 詳細・カタログ詳細の共通ヘッダー。表紙をぼかして敷いた淡い背景に、大きな表紙と題名を置く。
  // 色は抽出しない（画像をそのまま敷くので CORS が要らない）。
  import type { Snippet } from 'svelte';
  import type { MediaType } from '../db/schema';
  import Cover from './Cover.svelte';
  import { isSafeHttpUrl } from './release-view';

  let {
    src,
    title,
    subtitle,
    mediaType,
    onAir = false,
    children,
  }: {
    src?: string | null;
    title: string;
    subtitle?: string;
    mediaType?: MediaType;
    onAir?: boolean;
    /** 題名の下に置くもの（媒体の並び・操作など） */
    children?: Snippet;
  } = $props();

  const url = $derived(src && isSafeHttpUrl(src) ? src : undefined);
  // CSS の url() を壊す文字だけ逃がす
  const bgImage = $derived(url ? `url("${url.replace(/["\\\n\r]/g, (c) => encodeURIComponent(c))}")` : undefined);
</script>

<header class="hero" class:plain={!url}>
  {#if bgImage}
    <div class="hero-bg" style:background-image={bgImage} aria-hidden="true"></div>
    <div class="hero-veil" aria-hidden="true"></div>
  {/if}
  <div class="hero-body">
    <Cover src={url} {title} {mediaType} size="l" {onAir} />
    <div class="hero-text">
      <h1 class="hero-title">{title}</h1>
      {#if subtitle}<p class="hero-sub">{subtitle}</p>{/if}
      {@render children?.()}
    </div>
  </div>
</header>

<style>
  .hero {
    position: relative;
    overflow: hidden;
    border-bottom: 1px solid var(--fg);
  }
  .hero.plain {
    background: var(--paper);
  }
  .hero-bg {
    position: absolute;
    inset: -32px;
    background-size: cover;
    background-position: center 30%;
    filter: blur(28px) saturate(1.4);
  }
  .hero-veil {
    position: absolute;
    inset: 0;
    background: color-mix(in srgb, var(--bg) 58%, transparent);
  }
  .hero-body {
    position: relative;
    display: flex;
    align-items: flex-end;
    gap: 1rem;
    padding: 1.75rem 1rem 1.1rem;
  }
  .hero-text {
    display: flex;
    flex-direction: column;
    gap: 0.4rem;
    min-width: 0;
  }
  .hero-title {
    font-size: 1.35rem;
    font-weight: 700;
    letter-spacing: -0.01em;
    line-height: 1.25;
    margin: 0;
    overflow-wrap: anywhere;
  }
  .hero-sub {
    font-size: 0.78rem;
    color: var(--muted);
    margin: 0;
  }
</style>
