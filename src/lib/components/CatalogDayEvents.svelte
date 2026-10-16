<script lang="ts">
  // 日の段（番組・発売）ごとの「カタログから」。多い時は上位5件＋「ほか n 件」で畳み、押すと全部開く。
  import { Layers } from '@lucide/svelte';
  import type { CatalogEvent } from '../catalog/types';
  import { DAY_EVENT_LIMIT, foldEvents } from '../calendar/catalog-overlay';
  import CatalogEventRow from './CatalogEventRow.svelte';

  // 段に Library の行も並ぶ時だけ「カタログから」で分ける（カタログだけの段では見出しの繰り返しになる）
  let { events, labeled = true }: { events: readonly CatalogEvent[]; labeled?: boolean } = $props();

  let expanded = $state(false);
  const folded = $derived(foldEvents(events, expanded));
</script>

{#if events.length > 0}
  <div class="catalog-day">
    {#if labeled}
      <p class="sub-head">
        <Layers size={12} strokeWidth={2} aria-hidden="true" />
        <span class="type-label">カタログから</span>
        <span class="mono count">{events.length}</span>
      </p>
    {/if}
    <ul class="rows">
      {#each folded.shown as event (`${event.clusterId}|${event.event}|${event.label}`)}
        <li><CatalogEventRow {event} /></li>
      {/each}
    </ul>
    {#if folded.rest > 0}
      <button type="button" class="more" aria-expanded="false" onclick={() => (expanded = true)}>
        ほか <span class="mono">{folded.rest}</span> 件
      </button>
    {:else if expanded && events.length > DAY_EVENT_LIMIT}
      <button type="button" class="more" aria-expanded="true" onclick={() => (expanded = false)}>たたむ</button>
    {/if}
  </div>
{/if}

<style>
  .catalog-day {
    display: flex;
    flex-direction: column;
    min-width: 0;
  }
  .sub-head {
    display: flex;
    align-items: center;
    gap: 0.375rem;
    margin: 0;
    padding: 0.375rem 0.25rem 0.25rem;
    color: var(--muted);
    line-height: 1.2;
  }
  .count {
    font-size: 10px;
    color: var(--muted);
  }
  .rows {
    margin: 0;
    padding: 0;
    list-style: none;
    border-top: 1px solid var(--line);
  }
  .more {
    align-self: flex-start;
    min-height: 2rem;
    padding: 0.25rem;
    font-size: 11px;
    color: var(--fg);
    text-decoration: underline;
    text-decoration-color: var(--line);
    text-underline-offset: 3px;
  }
  .more:hover {
    text-decoration-color: var(--fg);
  }
  .more:focus-visible {
    outline: 2px solid var(--fg);
    outline-offset: 2px;
  }
</style>
