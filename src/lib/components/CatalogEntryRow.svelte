<script lang="ts">
  import { ExternalLink } from '@lucide/svelte';
  import type { Source } from '../db/schema';
  import type { CatalogEntry, OriginCovers } from '../catalog/types';
  import {
    entryCounts,
    entryStatusLabel,
    formatEntryPeriod,
    formatLabel,
    linkBadge,
    namesLine,
  } from '../catalog/view';
  import { SOURCE_LABELS } from './labels';
  import { isSafeHttpUrl } from './release-view';
  import { entryCover } from './catalog-cover';
  import CatalogKindBadge from './CatalogKindBadge.svelte';
  import Cover from './Cover.svelte';
  import MonoTime from './MonoTime.svelte';

  let {
    entry,
    registered,
    busy,
    showKind = false,
    covers,
    onadd,
  }: {
    entry: CatalogEntry;
    registered: boolean;
    busy: boolean;
    /** 候補欄のように面が混ざる時だけ、種別の活字バッジを出す。 */
    showKind?: boolean;
    /** 表紙の無い本・Web 小説に出す原作の表紙（この作品の行だけ。候補欄では渡さない）。 */
    covers?: OriginCovers;
    onadd: (entry: CatalogEntry) => void;
  } = $props();

  const sourceLabel = $derived(SOURCE_LABELS[entry.source as Source] ?? entry.source);
  const period = $derived(formatEntryPeriod(entry));
  // 形式・状態は文字、期間と話数・巻数は等幅
  const meta = $derived([formatLabel(entry), entryStatusLabel(entry)].filter((v): v is string => !!v));
  const figures = $derived([period, ...entryCounts(entry)].filter((v): v is string => !!v));
  const cover = $derived(entryCover(entry, covers));
  const link = $derived(linkBadge(entry));
  const href = $derived(entry.url && isSafeHttpUrl(entry.url) ? entry.url : null);
  const creators = $derived(namesLine(entry.creators));
  const studios = $derived(namesLine(entry.studios));
</script>

<div class="row-hover flex items-start gap-2.5 py-2.5" style="border-bottom: 1px solid var(--line); min-width: 0;">
  <Cover src={cover.url} title={cover.title} mediaType={cover.mediaType} size="xs" />
  <div class="min-w-0 flex-1">
    <p class="text-sm" style="overflow-wrap: anywhere;">{entry.title}</p>

    <p class="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px]" style="color: var(--muted);">
      {#if showKind}
        <CatalogKindBadge kind={entry.kind} />
      {/if}
      <span style="white-space: nowrap;">{sourceLabel}</span>
      {#each meta as item}
        <span style="white-space: nowrap;">{item}</span>
      {/each}
      {#each figures as item}
        <MonoTime text={item} />
      {/each}
    </p>

    {#if creators}
      <p class="text-[11px]" style="color: var(--muted); overflow-wrap: anywhere;">作者 {creators}</p>
    {/if}
    {#if studios}
      <p class="text-[11px]" style="color: var(--muted); overflow-wrap: anywhere;">制作 {studios}</p>
    {/if}

    {#if href || link}
      <p class="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px]" style="color: var(--muted);">
        {#if href}
          <a
            {href}
            target="_blank"
            rel="noopener noreferrer"
            class="inline-flex items-center gap-1 underline"
            style="white-space: nowrap; color: var(--fg);"
            aria-label={`${entry.title} を ${sourceLabel} で開く`}
            >{sourceLabel} で開く<ExternalLink size={11} aria-hidden="true" /></a
          >
        {/if}
        {#if link}
          <span class="type-label" style="text-transform: none; letter-spacing: 0.06em;">{link}</span>
        {/if}
      </p>
    {/if}
  </div>

  {#if registered}
    <button
      type="button"
      class="shrink-0 px-2 py-1 text-[11px]"
      style="border: 1px solid var(--line); color: var(--muted); white-space: nowrap;"
      disabled
      aria-label={`${entry.title} は登録済み`}
    >
      登録済み
    </button>
  {:else}
    <button
      type="button"
      class="shrink-0 px-2 py-1 text-[11px] disabled:opacity-40"
      style="border: 1px solid var(--fg); color: var(--fg); white-space: nowrap;"
      disabled={busy}
      aria-label={`${entry.title} を Library に追加`}
      onclick={() => onadd(entry)}
    >
      追加
    </button>
  {/if}
</div>
