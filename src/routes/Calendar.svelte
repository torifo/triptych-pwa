<script lang="ts">
  import { onMount } from 'svelte';
  import { CalendarPlus, ChevronLeft, ChevronRight, Layers } from '@lucide/svelte';
  import { listReleases, listEditions, listWorks } from '../lib/db/store';
  import type { Release, Edition, Work } from '../lib/db/schema';
  import { formatReleaseDate, todayJst } from '../lib/dates';
  import { navigate } from '../lib/router.svelte';
  import { preferences, type WeekStart } from '../lib/preferences';
  import { fetchCatalogEvents } from '../lib/catalog/api';
  import type { CatalogEventPage } from '../lib/catalog/types';
  import ReleaseRow from '../lib/components/ReleaseRow.svelte';
  import MiniCover from '../lib/components/MiniCover.svelte';
  import OnAirLamp from '../lib/components/OnAirLamp.svelte';
  import FilterChip from '../lib/components/FilterChip.svelte';
  import CatalogDayEvents from '../lib/components/CatalogDayEvents.svelte';
  import type { CoverSize } from '../lib/components/cover';
  import { buildCoverIndex, isAiringNow } from '../lib/components/home-view';
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
  import {
    MEDIA_FILTERS,
    catalogKindsFor,
    dayHeading,
    filterByMedia,
    loadCalendarView,
    loadCatalogOverlay,
    monthRange,
    rangeLabel,
    saveCalendarView,
    saveCatalogOverlay,
    weekDates,
    weekStart,
    type CalendarView,
    type MediaFilter,
  } from '../lib/calendar/view';
  import {
    describeOverlayError,
    groupEventsByDate,
    libraryKeys,
    loadOverlayEvents,
    overlayEvents,
    overlayNote,
    type OverlayStatus,
  } from '../lib/calendar/catalog-overlay';
  import { ICS_DAYS, buildIcs, icsEventsFor, icsFilename } from '../lib/calendar/ics';
  import { saveTextFile } from '../lib/calendar/download';

  const WEEKDAYS_SUN = ['日', '月', '火', '水', '木', '金', '土'];
  // 週の始まり（設定）。月曜からなら升目の先頭を1つずらす
  let weekStartPref = $state<WeekStart>(preferences.get('weekStart'));
  onMount(() => preferences.subscribe((prefs) => (weekStartPref = prefs.weekStart)));
  const WEEKDAYS = $derived(weekStartPref === 'mon' ? [...WEEKDAYS_SUN.slice(1), WEEKDAYS_SUN[0]] : WEEKDAYS_SUN);
  const GRID_ITEMS_PER_CELL = 2;
  const VIEWS: { value: CalendarView; label: string }[] = [
    { value: 'week', label: '週' },
    { value: 'month', label: '月' },
    { value: 'list', label: 'リスト' },
  ];

  let releases = $state<Release[]>([]);
  let editions = $state<Edition[]>([]);
  let works = $state<Work[]>([]);
  let loaded = $state(false);
  // 既定は週。選択は端末に保存（localStorage が使えなければ週）
  let view = $state<CalendarView>(loadCalendarView());
  // Library の外（カタログ）の出来事も重ねる。既定は ON
  let overlayOn = $state(loadCatalogOverlay());
  let mediaFilter = $state<MediaFilter>('all');

  // 「今日」は JST の暦日。端末のタイムゾーンに左右されない。ON AIR と日付の切り替わりは時計で拾う
  let now = $state(new Date());
  const today = $derived(todayJst(now));

  // 週: 今日から7日。offset で前後の週へ
  let weekOffset = $state(0);
  // 月の升目
  let cursorYear = $state(Number(todayJst().slice(0, 4)));
  let cursorMonth = $state(Number(todayJst().slice(5, 7)) - 1); // 0-11
  // 升目で選んだ日（未選択ならこの月の今日）。その日の行を升目の下に出す
  let selectedDate = $state<string | null>(null);

  let exportNote = $state<string | null>(null);

  const editionMap = $derived(new Map(editions.map((e) => [e.id, e])));
  const workMap = $derived(new Map(works.map((w) => [w.id, w])));
  const covers = $derived(buildCoverIndex(editions, works));

  async function refresh() {
    const [r, e, w] = await Promise.all([listReleases(), listEditions(), listWorks()]);
    releases = r;
    editions = e;
    works = w;
    now = new Date();
    loaded = true;
  }

  onMount(() => {
    void refresh();
    const id = setInterval(() => (now = new Date()), 60_000);
    return () => clearInterval(id);
  });

  const tracked = $derived(trackedReleases(releases, editionMap));
  const filtered = $derived(filterByMedia(tracked, editionMap, mediaFilter));
  const split = $derived(splitByPrecision(filtered));
  const streaming = $derived(startedStreamServices(releases, today));

  function setView(next: CalendarView) {
    view = next;
    saveCalendarView(next);
  }

  function setOverlay(on: boolean) {
    overlayOn = on;
    saveCatalogOverlay(on);
  }

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

  // ---------------------------------------------------------------------------
  // 週
  // ---------------------------------------------------------------------------

  const weekFrom = $derived(weekStart(today, weekOffset));
  const days = $derived(weekDates(weekFrom));
  const weekTo = $derived(days[days.length - 1]);

  // ---------------------------------------------------------------------------
  // 月の升目
  // ---------------------------------------------------------------------------

  interface DayCell {
    date: string | null;
    day: number | null;
    isToday: boolean;
  }

  const monthGrid = $derived(
    (() => {
      // UTC で暦を計算する（端末のタイムゾーンで月初の曜日がずれないように）
      const sundayFirst = new Date(Date.UTC(cursorYear, cursorMonth, 1)).getUTCDay();
      const startWeekday = weekStartPref === 'mon' ? (sundayFirst + 6) % 7 : sundayFirst;
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

  // ---------------------------------------------------------------------------
  // カタログの重ね表示（表示中の期間の出来事）
  // ---------------------------------------------------------------------------

  const catalogKinds = $derived(catalogKindsFor(mediaFilter));
  const overlayRange = $derived(
    view === 'week' ? { from: weekFrom, to: weekTo } : view === 'month' ? monthRange(cursorYear, cursorMonth + 1) : null,
  );
  const overlayKey = $derived(overlayRange ? `${overlayRange.from}|${overlayRange.to}|${catalogKinds.join(',')}` : '');

  type Loaded = { key: string } & ({ status: 'loading' } | { status: 'ready'; page: CatalogEventPage } | { status: 'error'; reason: string });
  let overlayLoad = $state<Loaded | null>(null);
  // 取れた期間は覚えておき、週や月を行き来しても取り直さない（画面を離れたら捨てる）
  const overlayCache = new Map<string, CatalogEventPage>();
  let overlaySeq = 0;

  async function loadOverlay(key: string, range: { from: string; to: string }, kinds: typeof catalogKinds, month: boolean) {
    const cached = overlayCache.get(key);
    if (cached) {
      overlayLoad = { key, status: 'ready', page: cached };
      return;
    }
    const seq = ++overlaySeq;
    overlayLoad = { key, status: 'loading' };
    try {
      // 月は本だけで上限に掛かるので、アニメと本を別々に取って合わせる
      const page = await loadOverlayEvents(fetchCatalogEvents, range, kinds, month);
      overlayCache.set(key, page);
      if (seq === overlaySeq) overlayLoad = { key, status: 'ready', page };
    } catch (err) {
      if (seq === overlaySeq) overlayLoad = { key, status: 'error', reason: describeOverlayError(err) };
    }
  }

  $effect(() => {
    const range = overlayRange;
    const kinds = catalogKinds;
    const key = overlayKey;
    const month = view === 'month';
    if (!overlayOn || !range || kinds.length === 0) return;
    void loadOverlay(key, range, kinds, month);
  });

  const libKeys = $derived(libraryKeys(works, editions));
  const overlayPage = $derived(
    overlayOn && overlayLoad?.key === overlayKey && overlayLoad.status === 'ready' ? overlayLoad.page : null,
  );
  const overlayList = $derived(overlayPage ? overlayEvents(overlayPage.items, libKeys, catalogKinds) : []);
  const eventsByDate = $derived(groupEventsByDate(overlayList));
  const shownDayEvents = $derived(shownDay ? (eventsByDate.get(shownDay) ?? []) : []);

  const overlayStatus = $derived.by((): OverlayStatus => {
    if (!overlayOn) return { status: 'off' };
    if (catalogKinds.length === 0) return { status: 'no-kinds' };
    if (!overlayLoad || overlayLoad.key !== overlayKey || overlayLoad.status === 'loading') return { status: 'loading' };
    if (overlayLoad.status === 'error') return { status: 'error', reason: overlayLoad.reason };
    return { status: 'ready', count: overlayList.length, truncated: overlayLoad.page.truncated };
  });
  const overlayText = $derived(overlayNote(overlayStatus));

  // ---------------------------------------------------------------------------
  // 予定表への書き出し（.ics）
  // ---------------------------------------------------------------------------

  function exportIcs() {
    const events = icsEventsFor({ releases, editions: editionMap, works: workMap, today });
    if (events.length === 0) {
      exportNote = `今日から${ICS_DAYS}日の予定がありません`;
      return;
    }
    saveTextFile(buildIcs(events, new Date()), icsFilename(today));
    exportNote = `${events.length} 件を書き出しました`;
  }
</script>

{#snippet rows(list: Release[], timeOnly = false, coverSize: CoverSize = 'xs', live = false)}
  <div class="flex flex-col" style={live ? undefined : 'border-top: 1px solid var(--line);'}>
    {#each list as release (release.id)}
      <ReleaseRow
        {release}
        edition={editionMap.get(release.editionId)}
        workTitle={workTitleFor(release.editionId)}
        streaming={streaming.get(release.editionId)}
        cover={covers.get(release.editionId)}
        {timeOnly}
        {coverSize}
        onAir={live && isAiringNow(release, now)}
        onclick={() => goToWork(release.editionId)}
      />
    {/each}
  </div>
{/snippet}

<section class="flex flex-col gap-3 p-4">
  <div class="flex flex-col gap-1">
    <div class="cal-head">
      <h1 class="page-title">Calendar<span class="type-label" style="margin-left: 0.8em;">発売・配信</span></h1>
      <button
        type="button"
        class="export"
        aria-label={`今日から${ICS_DAYS}日分の予定を .ics で書き出す`}
        onclick={exportIcs}
      >
        <CalendarPlus size={14} strokeWidth={2} aria-hidden="true" />
        書き出す
      </button>
    </div>
    <p class="fine">
      Library の予定（今日から<span class="mono">{ICS_DAYS}</span>日分）を .ics で。iPhone の Safari では、開くと「カレンダーに追加」できます。
    </p>
    {#if exportNote}
      <p class="fine" role="status" style="color: var(--fg);">{exportNote}</p>
    {/if}
  </div>

  <div class="controls">
    <div class="seg" role="group" aria-label="表示の切り替え">
      {#each VIEWS as item (item.value)}
        <button type="button" aria-pressed={view === item.value} onclick={() => setView(item.value)}>{item.label}</button>
      {/each}
    </div>
    {#if view !== 'list'}
      <div class="overlay">
        {#if overlayText}
          <span class="overlay-note" role="status">{overlayText}</span>
        {/if}
        <button
          type="button"
          class="overlay-toggle"
          aria-pressed={overlayOn}
          aria-label="Library の外（カタログ）の放送・発売も重ねる"
          title="カタログの放送・発売も重ねる"
          onclick={() => setOverlay(!overlayOn)}
        >
          <Layers size={14} strokeWidth={2} aria-hidden="true" />
          カタログ
        </button>
      </div>
    {/if}
  </div>

  <div class="chips" role="group" aria-label="媒体の絞り込み">
    {#each MEDIA_FILTERS as item (item.value)}
      <FilterChip active={mediaFilter === item.value} onclick={() => (mediaFilter = item.value)}>{item.label}</FilterChip>
    {/each}
  </div>

  {#if loaded && tracked.length === 0}
    <p class="text-xs" style="color: var(--muted);">Library に追加すると、ここに自分の予定が並びます</p>
  {/if}

  {#if view === 'week'}
    <div class="step-nav">
      <button type="button" class="step" aria-label="前の週" onclick={() => (weekOffset -= 1)}>
        <ChevronLeft size={16} strokeWidth={2} aria-hidden="true" />
      </button>
      <span class="mono step-label">{rangeLabel(weekFrom, weekTo)}</span>
      <button type="button" class="today-button" disabled={weekOffset === 0} onclick={() => (weekOffset = 0)}>今日</button>
      <button type="button" class="step" aria-label="次の週" onclick={() => (weekOffset += 1)}>
        <ChevronRight size={16} strokeWidth={2} aria-hidden="true" />
      </button>
    </div>

    <ol class="week">
      {#each days as date (date)}
        {@const head = dayHeading(date)}
        {@const dayReleases = releasesByDate.get(date) ?? []}
        {@const dayEvents = eventsByDate.get(date) ?? []}
        <li class="day" class:past={date < today} aria-current={date === today ? 'date' : undefined}>
          <h2 class="day-head">
            <span class="sr-only">{head.label}{date === today ? ' 今日' : ''}</span>
            <span class="mono day-date" aria-hidden="true">{head.date}</span>
            <span class="mono day-week" aria-hidden="true">{head.weekday}</span>
            {#if date === today}
              <span class="mono today-tag" aria-hidden="true">TODAY</span>
            {/if}
          </h2>
          {#if dayReleases.length > 0}
            {@render rows(dayReleases, true, 's', true)}
          {/if}
          <CatalogDayEvents events={dayEvents} />
          {#if dayReleases.length === 0 && dayEvents.length === 0}
            <p class="none">予定なし</p>
          {/if}
        </li>
      {/each}
    </ol>
  {:else if view === 'month'}
    <div class="step-nav">
      <button type="button" class="step" aria-label="前の月" onclick={prevMonth}>
        <ChevronLeft size={16} strokeWidth={2} aria-hidden="true" />
      </button>
      <span class="mono step-label">{cursorYear}.{pad2(cursorMonth + 1)}</span>
      <button type="button" class="step" aria-label="次の月" onclick={nextMonth}>
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
          {@const catalogCount = eventsByDate.get(date)?.length ?? 0}
          <button
            type="button"
            class="cell"
            class:today={cell.isToday}
            class:selected={shownDay === date}
            aria-current={cell.isToday ? 'date' : undefined}
            aria-pressed={shownDay === date}
            aria-label={dayCellLabel(date, cellReleases.length, mark, catalogCount)}
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
            {#if catalogCount > 0}
              <!-- カタログの出来事は件数によらず無彩色の点1つ -->
              <span class="cell-catalog" aria-hidden="true"></span>
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
        {/if}
        <CatalogDayEvents events={shownDayEvents} />
        {#if shownDayReleases.length === 0 && shownDayEvents.length === 0}
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
    {#if loaded && tracked.length > 0 && filtered.length === 0}
      <p class="py-6 text-center text-xs" style="color: var(--muted);">この媒体の予定はありません</p>
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
  /* ─── 見出しと書き出し ─── */
  .cal-head {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 0.5rem 0.75rem;
    min-width: 0;
  }
  .cal-head .page-title {
    margin: 0;
    min-width: 0;
  }
  .export {
    display: inline-flex;
    align-items: center;
    gap: 0.25rem;
    flex-shrink: 0;
    min-height: 2.25rem;
    padding: 0 0.625rem;
    border: 1px solid var(--fg);
    color: var(--fg);
    font-size: 11px;
    letter-spacing: 0.06em;
    white-space: nowrap;
    transition: background 0.2s ease;
  }
  .export:hover {
    background: color-mix(in srgb, var(--fg) 6%, transparent);
  }
  .fine {
    margin: 0;
    font-size: 10.5px;
    line-height: 1.5;
    color: var(--muted);
  }

  /* ─── 表示の切り替え・重ね表示・媒体 ─── */
  .controls {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 0.5rem;
    min-width: 0;
  }
  /* 区切りは隙間から覗く地の黒（ボタンは地の色で塗る） */
  .seg {
    display: inline-flex;
    flex-shrink: 0;
    gap: 1px;
    border: 1px solid var(--fg);
    background: var(--fg);
  }
  .seg button,
  .overlay-toggle {
    min-height: 2rem;
    padding: 0 0.75rem;
    font-size: 11px;
    white-space: nowrap;
    color: var(--fg);
    background: transparent;
  }
  .seg button {
    background: var(--bg);
  }
  .seg button[aria-pressed='true'],
  .overlay-toggle[aria-pressed='true'] {
    background: var(--fg);
    color: var(--bg);
  }
  .overlay {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: 0.5rem;
    flex: 1 1 auto;
    min-width: 0;
  }
  .overlay-note {
    min-width: 0;
    font-size: 10.5px;
    line-height: 1.3;
    text-align: right;
    color: var(--muted);
  }
  .overlay-toggle {
    display: inline-flex;
    align-items: center;
    gap: 0.25rem;
    flex-shrink: 0;
    padding: 0 0.625rem;
    border: 1px solid var(--fg);
  }
  .seg button:focus-visible,
  .overlay-toggle:focus-visible,
  .export:focus-visible,
  .step:focus-visible,
  .today-button:focus-visible {
    outline: 2px solid var(--fg);
    outline-offset: 2px;
  }
  .chips {
    display: flex;
    flex-wrap: wrap;
    gap: 0.375rem;
  }

  /* ─── 週・月の送り ─── */
  .step-nav {
    display: flex;
    align-items: center;
    gap: 0.5rem;
  }
  .step {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
    width: 2.25rem;
    height: 2.25rem;
    border: 1px solid var(--line);
    color: var(--fg);
  }
  /* 年をまたぐ週（'2026.12.29 – 2027.01.04'）は 320px で収まらないので、ダッシュの後ろで折り返してよい */
  .step-label {
    flex: 1;
    min-width: 0;
    text-align: center;
    font-size: 0.875rem;
    font-weight: 500;
    letter-spacing: 0.04em;
    line-height: 1.3;
  }
  .today-button {
    flex-shrink: 0;
    min-height: 2.25rem;
    padding: 0 0.625rem;
    border: 1px solid var(--fg);
    font-size: 11px;
    color: var(--fg);
  }
  .today-button:disabled {
    border-color: var(--line);
    color: var(--muted);
  }

  /* ─── 週 ─── */
  .week {
    display: flex;
    flex-direction: column;
    gap: 1.25rem;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  .day {
    display: flex;
    flex-direction: column;
    min-width: 0;
  }
  /* 日の見出し: mono の .shou と同じ黒罫。今日は黄ではなく黒地の TODAY */
  .day-head {
    display: flex;
    align-items: baseline;
    gap: 0.5rem;
    margin: 0;
    padding-bottom: 0.35rem;
    border-bottom: 1px solid var(--fg);
    line-height: 1.2;
  }
  .day-date {
    font-size: 1rem;
    font-weight: 500;
  }
  .day-week {
    font-size: 0.68rem;
    font-weight: 500;
    letter-spacing: 0.18em;
    color: var(--muted);
  }
  .today-tag {
    align-self: center;
    padding: 0 4px;
    background: var(--fg);
    color: var(--bg);
    font-size: 9px;
    font-weight: 500;
    letter-spacing: 0.08em;
    line-height: 1.6;
  }
  .past .day-date {
    color: var(--muted);
  }
  .past .day-head {
    border-bottom-color: var(--line);
  }
  .none {
    margin: 0;
    padding: 0.5rem 0.25rem;
    font-size: 11px;
    color: var(--muted);
  }

  /* ─── 月の升目 ─── */
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
  /* 今日は日付を黒地に（黄はページ見出しのマーカーだけ） */
  .today .cell-day {
    padding: 0 3px;
    background: var(--fg);
    color: var(--bg);
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
  /* カタログの出来事がある日: 升目の下に無彩色の小さな点 */
  .cell-catalog {
    width: 4px;
    height: 4px;
    margin-top: auto;
    border-radius: 50%;
    background: var(--muted);
    flex-shrink: 0;
  }

  @media (max-width: 340px) {
    .seg button,
    .overlay-toggle {
      padding: 0 0.5rem;
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .export {
      transition: none;
    }
  }
</style>
