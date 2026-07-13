<script lang="ts">
  import { onMount } from 'svelte';
  import { listReleases, listEditions, listWorks } from '../lib/db/store';
  import type { Release, Edition, Work } from '../lib/db/schema';
  import { navigate } from '../lib/router.svelte';
  import MediaBadge from '../lib/components/MediaBadge.svelte';

  const TRACKED_STATUSES: Edition['status'][] = ['plan', 'active', 'paused'];
  const WEEKDAYS = ['日', '月', '火', '水', '木', '金', '土'];

  let releases = $state<Release[]>([]);
  let editions = $state<Edition[]>([]);
  let works = $state<Work[]>([]);
  let view = $state<'month' | 'list'>('month');

  const today = new Date();
  let cursorYear = $state(today.getFullYear());
  let cursorMonth = $state(today.getMonth()); // 0-11

  const editionMap = $derived(new Map(editions.map((e) => [e.id, e])));
  const workMap = $derived(new Map(works.map((w) => [w.id, w])));

  async function refresh() {
    releases = await listReleases();
    editions = await listEditions();
    works = await listWorks();
  }

  onMount(refresh);

  const trackedReleases = $derived(
    releases
      .filter((r) => {
        const edition = editionMap.get(r.editionId);
        return !!edition && TRACKED_STATUSES.includes(edition.status);
      })
      .sort((a, b) => a.date.localeCompare(b.date)),
  );

  function workTitleFor(editionId: string): string {
    const edition = editionMap.get(editionId);
    if (!edition) return '(不明な作品)';
    return workMap.get(edition.workId)?.title ?? '(不明な作品)';
  }

  function pad2(n: number): string {
    return String(n).padStart(2, '0');
  }

  function dateKey(y: number, m: number, d: number): string {
    return `${y}-${pad2(m + 1)}-${pad2(d)}`;
  }

  interface DayCell {
    date: string | null;
    day: number | null;
    isToday: boolean;
  }

  const monthGrid = $derived(
    (() => {
      const first = new Date(cursorYear, cursorMonth, 1);
      const startWeekday = first.getDay();
      const daysInMonth = new Date(cursorYear, cursorMonth + 1, 0).getDate();
      const todayKey = dateKey(today.getFullYear(), today.getMonth(), today.getDate());

      const cells: DayCell[] = [];
      for (let i = 0; i < startWeekday; i++) {
        cells.push({ date: null, day: null, isToday: false });
      }
      for (let d = 1; d <= daysInMonth; d++) {
        const key = dateKey(cursorYear, cursorMonth, d);
        cells.push({ date: key, day: d, isToday: key === todayKey });
      }
      while (cells.length % 7 !== 0) {
        cells.push({ date: null, day: null, isToday: false });
      }
      return cells;
    })(),
  );

  const releasesByDate = $derived(
    (() => {
      const map = new Map<string, Release[]>();
      for (const r of trackedReleases) {
        const list = map.get(r.date) ?? [];
        list.push(r);
        map.set(r.date, list);
      }
      return map;
    })(),
  );

  function prevMonth() {
    if (cursorMonth === 0) {
      cursorMonth = 11;
      cursorYear -= 1;
    } else {
      cursorMonth -= 1;
    }
  }

  function nextMonth() {
    if (cursorMonth === 11) {
      cursorMonth = 0;
      cursorYear += 1;
    } else {
      cursorMonth += 1;
    }
  }

  function isPostponed(release: Release): boolean {
    return release.dateHistory.length > 0;
  }

  function goToWork(editionId: string) {
    const edition = editionMap.get(editionId);
    if (edition) navigate(`#/work/${edition.workId}`);
  }

  function formatDate(date: string): string {
    const [, m, d] = date.split('-');
    return `${m}/${d}`;
  }
</script>

<section class="flex flex-col gap-3 p-4">
  <div class="flex items-center justify-between gap-2">
    <h1 class="page-title">Calendar<span class="type-label" style="margin-left: 0.8em;">発売・配信</span></h1>
    <div class="flex" style="border: 1px solid var(--fg);">
      <button
        type="button"
        class="px-2 py-1 text-[11px]"
        style={`white-space: nowrap; color: ${view === 'month' ? 'var(--bg)' : 'var(--fg)'}; background: ${view === 'month' ? 'var(--fg)' : 'transparent'};`}
        onclick={() => (view = 'month')}
      >
        月
      </button>
      <button
        type="button"
        class="px-2 py-1 text-[11px]"
        style={`white-space: nowrap; color: ${view === 'list' ? 'var(--bg)' : 'var(--fg)'}; background: ${view === 'list' ? 'var(--fg)' : 'transparent'}; border-left: 1px solid var(--fg);`}
        onclick={() => (view = 'list')}
      >
        リスト
      </button>
    </div>
  </div>

  {#if view === 'month'}
    <div class="flex items-center justify-between gap-2">
      <button
        type="button"
        class="px-2 py-1 text-xs"
        style="border: 1px solid var(--line); color: var(--fg);"
        onclick={prevMonth}
      >
        ＜
      </button>
      <span class="text-sm font-bold" style="white-space: nowrap;">{cursorYear}/{pad2(cursorMonth + 1)}</span>
      <button
        type="button"
        class="px-2 py-1 text-xs"
        style="border: 1px solid var(--line); color: var(--fg);"
        onclick={nextMonth}
      >
        ＞
      </button>
    </div>

    <div class="grid grid-cols-7" style="border-top: 1px solid var(--line); border-left: 1px solid var(--line);">
      {#each WEEKDAYS as w (w)}
        <div
          class="py-1 text-center text-[10px]"
          style="border-right: 1px solid var(--line); border-bottom: 1px solid var(--line); color: var(--muted);"
        >
          {w}
        </div>
      {/each}
      {#each monthGrid as cell, i (i)}
        <div
          class="flex flex-col gap-0.5 p-1"
          style={`border-right: 1px solid var(--line); border-bottom: 1px solid var(--line); min-height: 56px; min-width: 0; ${cell.isToday ? 'background: var(--hi-soft);' : ''}`}
        >
          {#if cell.day !== null}
            <span class="text-[10px]" style="color: var(--muted); white-space: nowrap;">{cell.day}</span>
            {#each (releasesByDate.get(cell.date ?? '') ?? []).slice(0, 2) as release (release.id)}
              <button
                type="button"
                class="truncate px-0.5 text-left text-[9px]"
                style="border: 1px solid var(--line); white-space: nowrap; min-width: 0;"
                onclick={() => goToWork(release.editionId)}
              >
                {workTitleFor(release.editionId)}{#if isPostponed(release)}<span style="color: var(--muted);">・延期</span>{/if}
              </button>
            {/each}
            {#if (releasesByDate.get(cell.date ?? '') ?? []).length > 2}
              <span class="text-[9px]" style="color: var(--muted); white-space: nowrap;">
                +{(releasesByDate.get(cell.date ?? '') ?? []).length - 2}
              </span>
            {/if}
          {/if}
        </div>
      {/each}
    </div>
  {:else}
    <div class="flex flex-col" style="border-top: 1px solid var(--line);">
      {#if trackedReleases.length === 0}
        <p class="py-6 text-center text-xs" style="color: var(--muted);">追跡中の Release はありません</p>
      {/if}
      {#each trackedReleases as release (release.id)}
        {@const edition = editionMap.get(release.editionId)}
        <button
          type="button"
          class="flex items-center gap-2 py-2 text-left"
          style="border-bottom: 1px solid var(--line); min-width: 0;"
          onclick={() => goToWork(release.editionId)}
        >
          <span class="text-[11px] font-bold" style="white-space: nowrap;">{formatDate(release.date)}</span>
          {#if edition}
            <MediaBadge mediaType={edition.mediaType} />
          {/if}
          <span class="min-w-0 flex-1 truncate text-sm">{workTitleFor(release.editionId)}</span>
          <span class="truncate text-[11px]" style="color: var(--muted); max-width: 25%;">{release.label}</span>
          {#if isPostponed(release)}
            <span class="text-[10px]" style="color: var(--muted); white-space: nowrap;">延期</span>
          {/if}
        </button>
      {/each}
    </div>
  {/if}
</section>
