<script lang="ts">
  import type { Edition, Release } from '../db/schema';
  import { formatReleaseDate } from '../dates';
  import { isPostponed } from './release-view';
  import MediaBadge from './MediaBadge.svelte';

  let {
    release,
    edition,
    workTitle,
    onclick,
    accent = false,
    emphasis = true,
  }: {
    release: Release;
    edition: Edition | undefined;
    workTitle: string;
    onclick: () => void;
    /** 左に黄バーを付ける（直近の予定など、強調してよい行だけ） */
    accent?: boolean;
    /** 日付を太字にする（false なら muted） */
    emphasis?: boolean;
  } = $props();

  const when = $derived(formatReleaseDate(release));
  const postponed = $derived(isPostponed(release));
</script>

<button
  type="button"
  class={`row-hover flex flex-col gap-0.5 py-2 text-left ${accent ? 'hi-bar pr-2' : ''}`}
  style="border-bottom: 1px solid var(--line); min-width: 0; width: 100%;"
  {onclick}
>
  <span class="flex items-center gap-2" style="min-width: 0;">
    <span
      class={`text-[11px] ${emphasis ? 'font-bold' : ''}`}
      style={`white-space: nowrap; font-variant-numeric: tabular-nums; ${emphasis ? '' : 'color: var(--muted);'}`}
    >
      {when}
    </span>
    {#if edition}
      <MediaBadge mediaType={edition.mediaType} />
    {/if}
    <span class="min-w-0 flex-1 truncate text-sm">{workTitle}</span>
  </span>
  {#if release.label || postponed}
    <span class="flex items-center gap-2 text-[11px]" style="color: var(--muted); min-width: 0;">
      <span class="min-w-0 truncate">{release.label}</span>
      {#if postponed}
        <span style="white-space: nowrap;">延期</span>
      {/if}
    </span>
  {/if}
</button>
