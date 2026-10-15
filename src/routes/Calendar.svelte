<script lang="ts">
  import { onMount } from 'svelte';
  import { listReleases, listEditions, listWorks } from '../lib/db/store';
  import type { Release, Edition, Work } from '../lib/db/schema';
  import { formatReleaseDate, todayJst } from '../lib/dates';
  import { navigate } from '../lib/router.svelte';
  import ReleaseRow from '../lib/components/ReleaseRow.svelte';
  import {
    groupByDate,
    groupByMonth,
    isPostponed,
    monthlyUndecided,
    splitByPrecision,
    trackedReleases,
    yearlyUndecided,
  } from '../lib/components/release-view';
  import { startedStreamServices } from '../lib/components/release-history';

  const WEEKDAYS = ['日', '月', '火', '水', '木', '金', '土'];
  const GRID_ITEMS_PER_CELL = 2;

  let releases = $state<Release[]>([]);
  let editions = $state<Edition[]>([]);
  let works = $state<Work[]>([]);
  let view = $state<'month' | 'list'>('month');

  // 「今日」は JST の暦日。端末のタイムゾーンに左右されない
  let today = $state(todayJst());
  let cursorYear = $state(Number(todayJst().slice(0, 4)));
  let cursorMonth = $state(Number(todayJst().slice(5, 7)) - 1); // 0-11

  const editionMap = $derived(new Map(editions.map((e) => [e.id, e])));
  const workMap = $derived(new Map(works.map((w) => [w.id, w])));

  async function refresh() {
    releases = await listReleases();
    editions = await listEditions();
    works = await listWorks();
    today = todayJst();
  }

  onMount(refresh);

  const tracked = $derived(trackedReleases(releases, editionMap));
  const split = $derived(splitByPrecision(tracked));
  const streaming = $derived(startedStreamServices(releases, today));

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
      // UTC で暦を計算する（端末のタイムゾーンで月初の曜日がずれないように）
      const startWeekday = new Date(Date.UTC(cursorYear, cursorMonth, 1)).getUTCDay();
      const daysInMonth = new Date(Date.UTC(cursorYear, cursorMonth + 1, 0)).getUTCDate();

      const cells: DayCell[] = [];
      for (let i = 0; i < startWeekday; i++) {
        cells.push({ date: null, day: null, isToday: false });
      }
      for (let d = 1; d <= daysInMonth; d++) {
        const key = dateKey(cursorYear, cursorMonth, d);
        cells.push({ date: key, day: d, isToday: key === today });
      }
      while (cells.length % 7 !== 0) {
        cells.push({ date: null, day: null, isToday: false });
      }
      return cells;
    })(),
  );

  // day 精度だけが日付セルに入る。month / year 精度は下の「未定」欄へ
  const releasesByDate = $derived(groupByDate(split.days));
  const monthUndecided = $derived(monthlyUndecided(split.months, cursorYear, cursorMonth + 1));
  const yearUndecided = $derived(yearlyUndecided(split.years, today));

  // リスト表示: day 精度は日付順、month 精度は月ごと、year 精度は時期未定
  const monthGroups = $derived(groupByMonth(split.months));
  const dayGroups = $derived(
    (() => {
      const groups: { key: string; year: number; month: number; releases: Release[] }[] = [];
      for (const release of split.days) {
        const key = release.date.slice(0, 7);
        const last = groups[groups.length - 1];
        if (last && last.key === key) {
          last.releases.push(release);
        } else {
          groups.push({
            key,
            year: Number(key.slice(0, 4)),
            month: Number(key.slice(5, 7)),
            releases: [release],
          });
        }
      }
      return groups;
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

  function goToWork(editionId: string) {
    const edition = editionMap.get(editionId);
    if (edition) navigate(`#/work/${edition.workId}`);
  }

  function cellLabel(release: Release): string {
    return `${workTitleFor(release.editionId)} ${release.label} ${formatReleaseDate(release)}`;
  }
</script>

{#snippet rows(list: Release[])}
  <div class="flex flex-col" style="border-top: 1px solid var(--line);">
    {#each list as release (release.id)}
      <ReleaseRow
        {release}
        edition={editionMap.get(release.editionId)}
        workTitle={workTitleFor(release.editionId)}
        streaming={streaming.get(release.editionId)}
        onclick={() => goToWork(release.editionId)}
      />
    {/each}
  </div>
{/snippet}

<section class="flex flex-col gap-3 p-4">
  <div class="flex items-center justify-between gap-2">
    <h1 class="page-title">Calendar<span class="type-label" style="margin-left: 0.8em;">発売・配信</span></h1>
    <div class="flex" style="border: 1px solid var(--fg);">
      <button
        type="button"
        class="px-2 py-1 text-[11px]"
        style={`white-space: nowrap; color: ${view === 'month' ? 'var(--bg)' : 'var(--fg)'}; background: ${view === 'month' ? 'var(--fg)' : 'transparent'}; border-right: 1px solid var(--fg);`}
        aria-pressed={view === 'month'}
        onclick={() => (view = 'month')}
      >
        月
      </button>
      <button
        type="button"
        class="px-2 py-1 text-[11px]"
        style={`white-space: nowrap; color: ${view === 'list' ? 'var(--bg)' : 'var(--fg)'}; background: ${view === 'list' ? 'var(--fg)' : 'transparent'};`}
        aria-pressed={view === 'list'}
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
        aria-label="前の月"
        onclick={prevMonth}
      >
        ＜
      </button>
      <span class="text-sm font-bold" style="white-space: nowrap;">{cursorYear}/{pad2(cursorMonth + 1)}</span>
      <button
        type="button"
        class="px-2 py-1 text-xs"
        style="border: 1px solid var(--line); color: var(--fg);"
        aria-label="次の月"
        onclick={nextMonth}
      >
        ＞
      </button>
    </div>

    <div
      class="grid"
      style="grid-template-columns: repeat(7, minmax(0, 1fr)); gap: 1px; background: var(--line); border: 1px solid var(--line);"
    >
      {#each WEEKDAYS as w (w)}
        <div class="py-1 text-center text-[10px]" style="background: var(--bg); color: var(--muted);">
          {w}
        </div>
      {/each}
      {#each monthGrid as cell, i (i)}
        {@const cellReleases = releasesByDate.get(cell.date ?? '') ?? []}
        <div
          class="flex flex-col gap-0.5 p-1"
          style={`min-height: 56px; min-width: 0; background: ${cell.isToday ? 'var(--hi-soft)' : 'var(--bg)'};`}
          aria-current={cell.isToday ? 'date' : undefined}
        >
          {#if cell.day !== null}
            <span class="text-[10px]" style={`white-space: nowrap; color: ${cell.isToday ? 'var(--fg)' : 'var(--muted)'}; ${cell.isToday ? 'font-weight: 700;' : ''}`}>{cell.day}</span>
            {#each cellReleases.slice(0, GRID_ITEMS_PER_CELL) as release (release.id)}
              <button
                type="button"
                class="flex flex-col px-0.5 text-left text-[9px]"
                style="border: 1px solid var(--line); min-width: 0; line-height: 1.35;"
                aria-label={cellLabel(release)}
                onclick={() => goToWork(release.editionId)}
              >
                <span class="truncate" style="white-space: nowrap;">
                  {workTitleFor(release.editionId)}{#if isPostponed(release)}<span style="color: var(--muted);">・延期</span>{/if}
                </span>
                {#if release.time}
                  <span style="color: var(--muted); white-space: nowrap; font-variant-numeric: tabular-nums;">{release.time}</span>
                {/if}
              </button>
            {/each}
            {#if cellReleases.length > GRID_ITEMS_PER_CELL}
              <span class="text-[9px]" style="color: var(--muted); white-space: nowrap;">
                +{cellReleases.length - GRID_ITEMS_PER_CELL}
              </span>
            {/if}
          {/if}
        </div>
      {/each}
    </div>

    {#if monthUndecided.length > 0}
      <div class="flex flex-col gap-1.5">
        <div class="shou" style="margin-bottom: 0;">
          <h2>日付未定（{cursorMonth + 1}月中）</h2>
          <span class="no">THIS MONTH</span>
        </div>
        {@render rows(monthUndecided)}
      </div>
    {/if}

    {#if yearUndecided.length > 0}
      <div class="flex flex-col gap-1.5">
        <div class="shou" style="margin-bottom: 0;">
          <h2>時期未定</h2>
          <span class="no">TBA</span>
        </div>
        {@render rows(yearUndecided)}
      </div>
    {/if}
  {:else}
    {#if tracked.length === 0}
      <p class="py-6 text-center text-xs" style="color: var(--muted);">追跡中の Release はありません</p>
    {/if}

    {#each dayGroups as group (group.key)}
      <div class="flex flex-col gap-1">
        <span class="type-label">{group.year}年{group.month}月</span>
        {@render rows(group.releases)}
      </div>
    {/each}

    {#if monthGroups.length > 0}
      <div class="flex flex-col gap-1.5">
        <div class="shou" style="margin-bottom: 0;">
          <h2>日付未定</h2>
          <span class="no">MONTH ONLY</span>
        </div>
        {#each monthGroups as group (group.key)}
          <div class="flex flex-col gap-1">
            <span class="type-label">{group.year}年{group.month}月中</span>
            {@render rows(group.releases)}
          </div>
        {/each}
      </div>
    {/if}

    {#if split.years.length > 0}
      <div class="flex flex-col gap-1.5">
        <div class="shou" style="margin-bottom: 0;">
          <h2>時期未定</h2>
          <span class="no">TBA</span>
        </div>
        {@render rows(split.years)}
      </div>
    {/if}
  {/if}
</section>
