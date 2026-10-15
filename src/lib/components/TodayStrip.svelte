<script lang="ts">
  // Home の「今日」: 表紙 m の横並び。表紙の左下に等幅の時刻、下に作品名と「あと 3:12」「#14」など。
  import type { CoverSource } from './cover';
  import Cover from './Cover.svelte';
  import { isAiringNow, todaySubLabel, type TodayItem } from './home-view';

  let {
    items,
    covers,
    now,
    onopen,
  }: {
    items: readonly TodayItem[];
    covers: ReadonlyMap<string, CoverSource>;
    now: Date;
    onopen: (item: TodayItem) => void;
  } = $props();

  function coverOf(item: TodayItem): CoverSource {
    return covers.get(item.editionId) ?? { title: item.release.label };
  }
</script>

<ul class="strip">
  {#each items as item (item.editionId)}
    {@const cover = coverOf(item)}
    {@const sub = todaySubLabel(item, now)}
    <li class="card">
      <button type="button" class="card-button" aria-label={`${cover.title} ${item.badge} ${sub.text}`} onclick={() => onopen(item)}>
        <Cover src={cover.url} title={cover.title} mediaType={cover.mediaType} size="l" onAir={isAiringNow(item.release, now)}>
          {#snippet badge()}
            <span class="mono time-badge">{item.badge}</span>
          {/snippet}
        </Cover>
        <span class="title">{cover.title}</span>
        <span class="mono sub" class:upcoming={sub.upcoming}>{sub.text}</span>
      </button>
    </li>
  {/each}
</ul>

<style>
  /* 画面の端まで流し、端の余白はスクロールの内側に持つ */
  .strip {
    display: flex;
    gap: 0.75rem;
    margin: 0 -1rem;
    padding: 0 1rem 0.25rem;
    list-style: none;
    overflow-x: auto;
    overscroll-behavior-x: contain;
    scroll-snap-type: x mandatory;
    scroll-padding-inline: 1rem;
    scrollbar-width: none;
  }
  .strip::-webkit-scrollbar {
    display: none;
  }

  /* 今日の作品は Home の主役なので、詳細ヘッダーと同じ大きさで見せる */
  .card {
    flex: 0 0 112px;
    width: 112px;
    scroll-snap-align: start;
  }
  .card-button {
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
    width: 100%;
    text-align: left;
  }
  .card-button:focus-visible {
    outline: 2px solid var(--fg);
    outline-offset: 2px;
  }

  /* 黒地に白の等幅（ダークでは反転） */
  .time-badge {
    display: block;
    padding: 1px 4px;
    background: var(--fg);
    color: var(--bg);
    font-size: 12px;
    font-weight: 500;
    line-height: 1.4;
    white-space: nowrap;
  }

  .title,
  .sub {
    display: block;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .title {
    font-size: 12px;
    font-weight: 500;
    line-height: 1.35;
  }
  .sub {
    font-size: 11px;
    line-height: 1.3;
    color: var(--muted);
  }
  .sub.upcoming {
    color: var(--fg);
    font-weight: 500;
  }
</style>
