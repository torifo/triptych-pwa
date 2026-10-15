<script lang="ts">
  import { onMount } from 'svelte';
  import { ChevronLeft, ChevronRight } from '@lucide/svelte';
  import { listReleases, listEditions, listWorks } from '../lib/db/store';
  import type { Release, Edition, Work } from '../lib/db/schema';
  import { formatReleaseDate, todayJst } from '../lib/dates';
  import { navigate } from '../lib/router.svelte';
  import ReleaseRow from '../lib/components/ReleaseRow.svelte';
  import MiniCover from '../lib/components/MiniCover.svelte';
  import OnAirLamp from '../lib/components/OnAirLamp.svelte';
  import { buildCoverIndex } from '../lib/components/home-view';
  import { calendarDay, cellThumbs, dayCellLabel, dayOnAirMark } from '../lib/components/calendar-view';
  import {
    groupByDate,
    groupByMonth,
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
  // 升目で選んだ日（未選択ならこの月の今日）。その日の行を升目の下に出す
  let selectedDate = $state<string | null>(null);

  const editionMap = $derived(new Map(editions.map((e) => [e.id, e])));
  const workMap = $derived(new Map(works.map((w) => [w.id, w])));
  const covers = $derived(buildCoverIndex(editions, works));

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
  const shownDay = $derived(calendarDay(selectedDate, today, cursorYear, cursorMonth + 1));
  const shownDayReleases = $derived(shownDay ? (releasesByDate.get(shownDay) ?? []) : []);
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

  function selectDay(date: string) {
    selectedDate = date;
  }

  function goToWork(editionId: string) {
    const edition = editionMap.get(editionId);
    // 押した Edition のタブで開く
    if (edition) navigate(`#/work/${encodeURIComponent(edition.workId)}?edition=${encodeURIComponent(edition.id)}`);
  }

</script>

{#snippet rows(list: Release[], timeOnly = false)}
  <div class="flex flex-col" style="border-top: 1px solid var(--line);">
    {#each list as release (release.id)}
      <ReleaseRow
        {release}
        edition={editionMap.get(release.editionId)}
        workTitle={workTitleFor(release.editionId)}
        streaming={streaming.get(release.editionId)}
        cover={covers.get(release.editionId)}
        {timeOnly}
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
      <button type="button" class="month-step" aria-label="前の月" onclick={prevMonth}>
        <ChevronLeft size={16} strokeWidth={2} aria-hidden="true" />
      </button>
      <span class="mono text-sm" style="white-space: nowrap; font-weight: 500; letter-spacing: 0.04em;">
        {cursorYear}.{pad2(cursorMonth + 1)}
      </span>
      <button type="button" class="month-step" aria-label="次の月" onclick={nextMonth}>
        <ChevronRight size={16} strokeWidth={2} aria-hidden="true" />
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
        {#if cell.date && cell.day !== null}
          {@const date = cell.date}
          {@const cellReleases = releasesByDate.get(date) ?? []}
          {@const thumbs = cellThumbs(cellReleases, GRID_ITEMS_PER_CELL)}
          {@const mark = dayOnAirMark(cellReleases, date, today)}
          <button
            type="button"
            class="cell"
            class:today={cell.isToday}
            class:selected={shownDay === date}
            aria-current={cell.isToday ? 'date' : undefined}
            aria-pressed={shownDay === date}
            aria-label={dayCellLabel(date, cellReleases.length, mark)}
            onclick={() => selectDay(date)}
          >
            <span class="cell-head">
              <span class="mono cell-day">{cell.day}</span>
              {#if mark === 'today'}
                <OnAirLamp />
              {:else if mark === 'upcoming'}
                <span class="cell-dot"></span>
              {/if}
            </span>
            {#if thumbs.shown.length > 0}
              <span class="cell-thumbs">
                {#each thumbs.shown as release (release.id)}
                  {@const cover = covers.get(release.editionId)}
                  <MiniCover src={cover?.url} title={cover?.title ?? workTitleFor(release.editionId)} />
                {/each}
              </span>
            {/if}
            {#if thumbs.rest > 0}
              <span class="mono cell-more">+{thumbs.rest}</span>
            {/if}
          </button>
        {:else}
          <div class="cell"></div>
        {/if}
      {/each}
    </div>

    {#if shownDay}
      <div class="flex flex-col gap-1.5">
        <div class="shou" style="margin-bottom: 0;">
          <h2 class="mono" style="font-weight: 500;">{formatReleaseDate({ date: shownDay })}</h2>
          <span class="no">{shownDay === today ? 'TODAY' : 'DAY'}</span>
        </div>
        {#if shownDayReleases.length > 0}
          {@render rows(shownDayReleases, true)}
        {:else}
          <p class="text-xs" style="color: var(--muted);">この日の予定はありません</p>
        {/if}
      </div>
    {/if}

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

<style>
  .month-step {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 2rem;
    height: 2rem;
    border: 1px solid var(--line);
    color: var(--fg);
  }

  .cell {
    display: flex;
    flex-direction: column;
    gap: 3px;
    min-width: 0;
    min-height: 56px;
    padding: 3px;
    background: var(--bg);
    text-align: left;
  }
  .cell.today {
    background: var(--hi-soft);
  }
  /* 選んだ日は内側に黒い罫（升目の幅を変えない） */
  .cell.selected {
    box-shadow: inset 0 0 0 1.5px var(--fg);
  }
  .cell:focus-visible {
    outline: 2px solid var(--fg);
    outline-offset: -2px;
  }
  .cell-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 2px;
    min-width: 0;
    line-height: 1.2;
  }
  .cell-day {
    font-size: 10px;
    color: var(--muted);
  }
  .today .cell-day {
    color: var(--fg);
    font-weight: 500;
  }
  /* これから先の放送・配信がある日。点滅はさせず、今日だけ ON AIR のランプにする */
  .cell-dot {
    width: 5px;
    height: 5px;
    border-radius: 50%;
    background: var(--onair);
    flex-shrink: 0;
  }
  .cell-thumbs {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 2px;
  }
  .cell-more {
    font-size: 9px;
    line-height: 1.2;
    color: var(--muted);
    white-space: nowrap;
  }
</style>
