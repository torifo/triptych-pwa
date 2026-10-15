<script lang="ts">
  // カレンダーの升目に並べる極小の表紙（幅は親に合わせる）。画像が無い時は生成りの面に題名の1字目。
  import { isSafeHttpUrl } from './release-view';

  let { src, title }: { src?: string | null; title: string } = $props();

  let failedSrc = $state<string | undefined>();

  const url = $derived(src && isSafeHttpUrl(src) && failedSrc !== src ? src : undefined);
  const initial = $derived([...title.trim()][0] ?? '');
</script>

<span class="mini" aria-hidden="true">
  {#if url}
    <img src={url} alt="" loading="lazy" decoding="async" onerror={() => (failedSrc = url)} />
  {:else}
    <span class="paper">{initial}</span>
  {/if}
</span>

<style>
  .mini {
    position: relative;
    display: block;
    width: 100%;
    aspect-ratio: 2 / 3;
    border-radius: 1px;
    overflow: hidden;
    background: var(--cover-ph);
  }
  .mini::after {
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
    display: flex;
    align-items: center;
    justify-content: center;
    background: var(--paper);
    color: var(--paper-ink);
    font-size: 9px;
    font-weight: 700;
    line-height: 1;
  }
</style>
