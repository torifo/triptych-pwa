<script lang="ts">
  // 1日分の段（番組 → 発売 → Web、見出しに分類の印）。各段は Library の行が先、カタログの出来事（5件＋「ほか n 件」）が後。
  import type { Snippet } from 'svelte';
  import type { CatalogEvent } from '../catalog/types';
  import type { Release } from '../db/schema';
  import { CATEGORY_LABELS, type DaySection } from '../calendar/category';
  import CatalogDayEvents from './CatalogDayEvents.svelte';
  import CategoryMark from './CategoryMark.svelte';

  let {
    sections,
    row,
    spread = false,
    narrow = false,
  }: {
    sections: readonly DaySection<Release, CatalogEvent>[];
    /** Library の1行（ReleaseRow）。行き先や ON AIR は呼ぶ側が持つ */
    row: Snippet<[Release]>;
    /** PC 幅で段を横に並べる（月の選んだ日・リスト） */
    spread?: boolean;
    /** PC 幅の週の7列。狭い列に合わせて行を積み直す */
    narrow?: boolean;
  } = $props();
</script>

<div class="sections" class:spread class:narrow>
  {#each sections as section (section.category)}
    <div class="section">
      <h3 class="section-head">
        <CategoryMark category={section.category} />
        <span>{CATEGORY_LABELS[section.category]}</span>
        <span class="mono count">{section.count}<span class="sr-only">件</span></span>
      </h3>
      {#if section.releases.length > 0}
        <div class="lib">
          {#each section.releases as release (release.id)}
            {@render row(release)}
          {/each}
        </div>
      {/if}
      <CatalogDayEvents events={section.events} labeled={section.releases.length > 0} />
    </div>
  {/each}
</div>

<style>
  .sections {
    display: flex;
    flex-direction: column;
    min-width: 0;
  }
  .section {
    display: flex;
    flex-direction: column;
    min-width: 0;
  }
  .section-head {
    display: flex;
    align-items: center;
    gap: 0.375rem;
    margin: 0;
    padding: 0.625rem 0.25rem 0.3rem;
    font-size: 11.5px;
    font-weight: 500;
    letter-spacing: 0.06em;
    line-height: 1.2;
    color: var(--fg);
  }
  .count {
    font-size: 10.5px;
    font-weight: 400;
    color: var(--muted);
  }
  .lib {
    display: flex;
    flex-direction: column;
    border-top: 1px solid var(--line);
  }

  @media (min-width: 64rem) {
    /* 段が2つ以上ある日は横に並べる（1つなら全幅） */
    .spread {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(20rem, 1fr));
      column-gap: 2rem;
      align-items: start;
    }

    /* 週の7列は狭いので、表紙を左に置き、右に時刻・題名（2行まで）・話数を積む。行の部品は変えずに並べ方だけ外から替える */
    .narrow :global(.release-row),
    .narrow :global(.event-row) {
      display: grid;
      grid-template-columns: auto minmax(0, 1fr);
      grid-template-areas: 'thumb when' 'thumb body';
      align-items: start;
      column-gap: 0.5rem;
      row-gap: 0.125rem;
    }
    .narrow :global(.release-row .when),
    .narrow :global(.event-row .when) {
      grid-area: when;
      flex-direction: row;
      align-items: center;
      gap: 0.375rem;
      min-width: 0;
    }
    .narrow :global(.release-row .lamp) {
      margin-top: 0;
    }
    .narrow :global(.release-row .thumb),
    .narrow :global(.event-row .thumb) {
      grid-area: thumb;
    }
    .narrow :global(.release-row .body),
    .narrow :global(.event-row .body) {
      grid-area: body;
    }
    /* 種別（放送・発売）は時刻の行の右端に重ねる */
    .narrow :global(.event-row .kind) {
      grid-area: when;
      justify-self: end;
    }
    .narrow :global(.release-row .head) {
      flex-wrap: wrap;
      row-gap: 0.125rem;
    }
    /* 題名は1行を占め、媒体の札は次の行へ（題名を札に押し潰させない） */
    .narrow :global(.release-row .title) {
      flex: 1 1 100%;
    }
    .narrow :global(.release-row .title),
    .narrow :global(.event-row .title) {
      display: -webkit-box;
      -webkit-box-orient: vertical;
      -webkit-line-clamp: 2;
      line-clamp: 2;
      white-space: normal;
      font-size: 0.8125rem;
      line-height: 1.35;
    }
  }
</style>
