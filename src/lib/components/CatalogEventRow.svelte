<script lang="ts">
  // カレンダーに重ねるカタログの出来事の行（Library の行より薄め）。押すとカタログ詳細へ。
  import type { CatalogEvent } from '../catalog/types';
  import { eventCover, eventHref, eventKindNote, eventKindWord, eventLabel, eventTimeText } from '../calendar/catalog-overlay';
  import Cover from './Cover.svelte';

  let { event }: { event: CatalogEvent } = $props();

  const cover = $derived(eventCover(event));
  const label = $derived(eventLabel(event));
  const kindWord = $derived(eventKindWord(event.event));
  const kindNote = $derived(eventKindNote(event.event));
</script>

<a class="event-row row-hover" href={eventHref(event)}>
  <span class="when mono">{eventTimeText(event)}</span>
  <!-- 題名は右に文字で出すので、表紙は読み上げない -->
  <span class="thumb" aria-hidden="true">
    <Cover src={cover.url} title={cover.title} mediaType={cover.mediaType} size="xs" />
  </span>
  <span class="body">
    <span class="title">{event.title}</span>
    {#if label !== kindWord || event.estimated}
      <span class="label">
        {#if label !== kindWord}{label}{/if}
        <!-- 次の話から毎週と見込んだ分。休止でずれることがあるので、確かな話と見分けられるように -->
        {#if event.estimated}<span class="estimated">見込み</span>{/if}
      </span>
    {/if}
  </span>
  {#if kindNote}
    <span class="kind mono">{kindNote}</span>
  {/if}
</a>

<style>
  .event-row {
    display: flex;
    align-items: center;
    gap: 0.625rem;
    width: 100%;
    min-width: 0;
    padding: 0.375rem 0.25rem;
    border-bottom: 1px solid var(--line);
    color: inherit;
    text-decoration: none;
  }
  .event-row:focus-visible {
    outline: 2px solid var(--fg);
    outline-offset: -2px;
  }

  /* Library の行（ReleaseRow）と同じ幅で、表紙の左端を揃える */
  .when {
    flex-shrink: 0;
    min-width: 5.6em;
    font-size: 11px;
    line-height: 1.35;
    color: var(--muted);
    white-space: nowrap;
  }

  .thumb {
    display: flex;
    flex-shrink: 0;
  }

  .body {
    display: flex;
    flex-direction: column;
    flex: 1;
    min-width: 0;
  }
  .title,
  .label {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .title {
    font-size: 0.8125rem;
    line-height: 1.4;
    color: color-mix(in srgb, var(--fg) 72%, var(--bg));
  }
  .label {
    font-size: 11px;
    line-height: 1.35;
    color: var(--muted);
  }

  .kind {
    flex-shrink: 0;
    font-size: 10px;
    letter-spacing: 0.04em;
    color: var(--muted);
    white-space: nowrap;
  }
  .estimated {
    margin-left: 0.375rem;
    padding: 0 0.25rem;
    border: 1px dashed var(--line);
    font-size: 9px;
    letter-spacing: 0.04em;
    color: var(--muted);
    white-space: nowrap;
  }
</style>
