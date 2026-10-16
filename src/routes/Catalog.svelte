<script lang="ts">
  import { BookOpen, ChevronDown, ChevronLeft, ChevronRight, Download, Search, SlidersHorizontal, Tv } from '@lucide/svelte';
  import { todayJst } from '../lib/dates';
  import { currentSeason } from '../lib/components/home-view';
  import { onDestroy, onMount, untrack } from 'svelte';
  import { ApiError } from '../lib/api';
  import {
    BUILD_COMMAND,
    downloadCatalogExport,
    fetchCatalogStats,
    fetchCatalogWorks,
    type ExportFormat,
  } from '../lib/catalog/api';
  import {
    PAGE_SIZE,
    SEARCH_DEBOUNCE_MS,
    catalogListHash,
    createDebouncer,
    hasActiveFilter,
    parseCatalogQuery,
    rememberListHash,
    serializeCatalogQuery,
  } from '../lib/catalog/query';
  import type { CatalogQuery, CatalogSort, CatalogStats, CatalogWorkPage } from '../lib/catalog/types';
  import {
    KIND_LABELS,
    KIND_ORDER,
    SEASON_FILTERS,
    SORT_OPTIONS,
    STATUS_FILTERS,
    activeFilterLabels,
    animeStatusLabel,
    formatJstDateTime,
    formatNext,
    isAnimeAiring,
    latestHarvestAt,
    yearRange,
  } from '../lib/catalog/view';
  import { summaryMediaType } from '../lib/components/catalog-cover';
  import CatalogKindBadge from '../lib/components/CatalogKindBadge.svelte';
  import Cover from '../lib/components/Cover.svelte';
  import FilterChip from '../lib/components/FilterChip.svelte';
  import MonoTime from '../lib/components/MonoTime.svelte';
  import { replaceQuery, route } from '../lib/router.svelte';
  import { viewport } from '../lib/viewport.svelte';

  const EXPORT_FORMATS: ExportFormat[] = ['csv', 'json'];

  // 絞り込みの状態は URL（ハッシュのクエリ）が正。行を開いて戻っても復元される。
  const query = $derived(parseCatalogQuery(route.query));

  let stats = $state<CatalogStats | null>(null);
  let statsError = $state(false);
  let result = $state<CatalogWorkPage | null>(null);
  let loading = $state(true);
  let loadError = $state<string | null>(null);
  let worksMissing = $state(false);
  let exporting = $state<ExportFormat | null>(null);
  let exportError = $state<string | null>(null);

  let qText = $state('');
  let yearText = $state('');
  // 絞り込みの塊は既定で畳む（一覧を早く見せる）。条件は要約の行に出る。
  let filterOpen = $state(false);
  let listTop = $state<HTMLElement | undefined>(undefined);

  const searchDebouncer = createDebouncer(SEARCH_DEBOUNCE_MS);
  const yearDebouncer = createDebouncer(SEARCH_DEBOUNCE_MS);
  onDestroy(() => {
    searchDebouncer.cancel();
    yearDebouncer.cancel();
  });

  const notBuilt = $derived(stats?.ready === false || worksMissing);
  const harvestedAt = $derived(stats?.ready ? latestHarvestAt(stats) : null);
  const pages = $derived(result ? Math.max(1, Math.ceil(result.total / Math.max(1, result.per))) : 1);
  const filtered = $derived(hasActiveFilter(query));
  const filterLabels = $derived(activeFilterLabels(query));
  const hasAnime = $derived(query.has?.includes('anime') ?? false);

  function update(patch: Partial<CatalogQuery>) {
    const next: CatalogQuery = { ...query, ...patch };
    if (!('page' in patch)) next.page = 1;
    replaceQuery(serializeCatalogQuery(next));
  }

  function clearFilters() {
    searchDebouncer.cancel();
    yearDebouncer.cancel();
    replaceQuery(serializeCatalogQuery({ sort: query.sort }));
  }

  // よく使う見え方への近道（押した状態でもう一度押すと解除）
  const season = currentSeason(todayJst());
  const AIRING_PRESET: CatalogQuery = { status: 'RELEASING', year: season.year, season: season.season, sort: 'popularity' };
  const BOOKS_PRESET: CatalogQuery = { kind: 'book', sort: 'upcoming' };
  const queryKey = (q: CatalogQuery) => JSON.stringify(Object.entries(serializeCatalogQuery(q)).sort());
  const isPreset = (preset: CatalogQuery) => queryKey({ ...query, page: undefined, q: undefined }) === queryKey(preset);
  const airingOn = $derived(isPreset(AIRING_PRESET));
  const booksOn = $derived(isPreset(BOOKS_PRESET));

  function applyPreset(preset: CatalogQuery, on: boolean) {
    searchDebouncer.cancel();
    yearDebouncer.cancel();
    replaceQuery(serializeCatalogQuery(on ? { sort: query.sort } : preset));
  }

  function toggleHasAnime() {
    const rest = (query.has ?? []).filter((k) => k !== 'anime');
    update({ has: hasAnime ? rest : [...rest, 'anime'] });
  }

  function onSearchInput(value: string) {
    qText = value;
    searchDebouncer.run(() => update({ q: value.trim() || undefined }));
  }

  function onYearInput(value: string) {
    yearText = value;
    const text = value.trim();
    if (text === '') {
      yearDebouncer.run(() => update({ year: undefined }));
    } else if (/^\d{4}$/.test(text)) {
      yearDebouncer.run(() => update({ year: Number(text) }));
    } else {
      yearDebouncer.cancel(); // 入力の途中（2桁など）は検索しない
    }
  }

  function goPage(page: number) {
    update({ page: Math.min(pages, Math.max(1, page)) });
    queueMicrotask(() => listTop?.scrollIntoView?.({ block: 'start' }));
  }

  // URL が外から変わった時（ナビの CATALOG を押した等）だけ入力欄を合わせる。自分で変えた分は触らない。
  let seenUrlQ: string | undefined;
  $effect(() => {
    const urlQ = query.q ?? '';
    untrack(() => {
      if (urlQ === seenUrlQ) return;
      seenUrlQ = urlQ;
      if (urlQ !== qText.trim()) {
        searchDebouncer.cancel();
        qText = urlQ;
      }
    });
  });
  let seenUrlYear: string | undefined;
  $effect(() => {
    const urlYear = query.year === undefined ? '' : String(query.year);
    untrack(() => {
      if (urlYear === seenUrlYear) return;
      seenUrlYear = urlYear;
      if (urlYear !== yearText.trim()) {
        yearDebouncer.cancel();
        yearText = urlYear;
      }
    });
  });

  // 詳細から戻るリンクが、絞り込みを保ったまま一覧へ戻れるように覚えておく。
  $effect(() => {
    rememberListHash(catalogListHash(query));
  });

  onMount(async () => {
    try {
      stats = await fetchCatalogStats();
    } catch {
      statsError = true;
    }
  });

  let requestSeq = 0; // 古い応答で新しい結果を上書きしない
  $effect(() => {
    const current = query;
    const seq = ++requestSeq;
    loading = true;
    fetchCatalogWorks({ ...current, per: PAGE_SIZE })
      .then((page) => {
        if (seq !== requestSeq) return;
        result = page;
        loadError = null;
        worksMissing = false;
      })
      .catch((err: unknown) => {
        if (seq !== requestSeq) return;
        if (err instanceof ApiError && err.status === 404) {
          worksMissing = true;
          loadError = null;
          return;
        }
        loadError =
          err instanceof ApiError ? `カタログを取得できませんでした (${err.status})` : 'カタログを取得できませんでした';
      })
      .finally(() => {
        if (seq === requestSeq) loading = false;
      });
  });

  async function exportAs(format: ExportFormat) {
    if (exporting) return;
    exporting = format;
    exportError = null;
    try {
      await downloadCatalogExport(format);
    } catch (err) {
      exportError =
        err instanceof ApiError ? `書き出しに失敗しました (${err.status})` : '書き出しに失敗しました';
    } finally {
      exporting = null;
    }
  }

  function count(n: number): string {
    return n.toLocaleString('ja-JP');
  }

  const SKELETON_ROWS = [0, 1, 2, 3, 4, 5];
</script>

<section class="flex flex-col gap-3 page">
  <h1 class="page-title" style="margin-bottom: 0.4rem;">
    カタログ<span class="type-label" style="margin-left: 0.8em;">CATALOG</span>
  </h1>

  {#if notBuilt}
    <div class="flex flex-col gap-2 p-3" style="border: 1px solid var(--fg);" data-testid="catalog-not-built">
      <p class="text-sm font-bold">カタログ未作成</p>
      <p class="text-xs" style="color: var(--muted);">collector 側で次のコマンドを実行すると作られます。</p>
      <code
        class="block px-2 py-1.5 text-xs"
        style="border: 1px solid var(--line); overflow-x: auto; white-space: nowrap; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;"
        >{BUILD_COMMAND}</code
      >
    </div>
  {:else}
    <div class="flex flex-wrap items-center justify-between gap-x-3 gap-y-1.5">
      <p class="text-[11px]" style="color: var(--muted);">
        {#if stats?.ready}
          <span style="white-space: nowrap;"><span class="mono">{count(stats.works)}</span> 作品</span>
          <span style="white-space: nowrap;"> · <span class="mono">{count(stats.entries)}</span> 件</span>
          {#if harvestedAt}
            <span style="white-space: nowrap;"> · 最終収集 <span class="mono">{formatJstDateTime(harvestedAt)}</span></span>
          {/if}
        {:else if statsError}
          <span role="status">件数を取得できませんでした</span>
        {/if}
      </p>
      <div class="flex items-center gap-1">
        {#each EXPORT_FORMATS as format (format)}
          <button
            type="button"
            class="inline-flex items-center gap-1 px-2 py-1 text-[11px] disabled:opacity-40"
            style="border: 1px solid var(--fg); color: var(--fg); white-space: nowrap; letter-spacing: 0.06em;"
            disabled={exporting !== null}
            aria-label={`カタログ全件を ${format.toUpperCase()} でダウンロード`}
            onclick={() => exportAs(format)}
          >
            <Download size={12} aria-hidden="true" />
            {exporting === format ? '書き出し中…' : format.toUpperCase()}
          </button>
        {/each}
      </div>
    </div>
    {#if exportError}
      <p class="text-[11px]" style="color: var(--muted);" role="status">{exportError}</p>
    {/if}

    <div class="controls">
      <div class="search-field">
        <span class="search-icon" aria-hidden="true"><Search size={16} /></span>
        <input
          class="w-full py-1.5"
          style="border: 0; border-bottom: 1px solid var(--fg); background: var(--bg); color: var(--fg); font-size: 1.05rem; outline: none; min-width: 0; padding-left: 1.6rem; padding-right: 0;"
          type="search"
          placeholder="題名・別名・読み"
          aria-label="作品を題名・別名・読みで検索"
          value={qText}
          oninput={(e) => onSearchInput(e.currentTarget.value)}
          onkeydown={(e) => {
            if (e.key === 'Enter' && !e.isComposing) searchDebouncer.flush();
          }}
        />
      </div>

      <div class="presets" role="group" aria-label="よく使う見え方">
        <button type="button" class="preset" aria-pressed={airingOn} onclick={() => applyPreset(AIRING_PRESET, airingOn)}>
          <Tv size={14} aria-hidden="true" />今期の放送中
        </button>
        <button type="button" class="preset" aria-pressed={booksOn} onclick={() => applyPreset(BOOKS_PRESET, booksOn)}>
          <BookOpen size={14} aria-hidden="true" />これから出る本
        </button>
      </div>

      <div class="toolbar">
        <button
          type="button"
          class="filter-toggle"
          class:on={filterOpen || filterLabels.length > 0}
          aria-expanded={filterOpen}
          aria-controls="catalog-filters"
          onclick={() => (filterOpen = !filterOpen)}
        >
          <SlidersHorizontal size={14} aria-hidden="true" />
          <span>絞り込み</span>
          {#if filterLabels.length > 0}
            <span class="mono filter-count">{filterLabels.length}</span>
          {/if}
          <span class="chev" class:open={filterOpen} aria-hidden="true"><ChevronDown size={14} /></span>
        </button>
        <div class="sort">
          <span class="type-label">並び</span>
          <select
            class="min-w-0 px-1.5 py-1 text-xs"
            style="border: 1px solid var(--line); background: var(--bg); color: var(--fg);"
            aria-label="並べ替え"
            value={query.sort ?? 'popularity'}
            onchange={(e) => update({ sort: e.currentTarget.value as CatalogSort })}
          >
            {#each SORT_OPTIONS as o (o.value)}
              <option value={o.value}>{o.label}</option>
            {/each}
          </select>
        </div>
      </div>
    </div>

    {#if filtered}
      <div class="summary">
        <p class="summary-text">
          {#each filterLabels as label (label)}
            <span class="summary-item">{label}</span>
          {:else}
            <span>検索語のみ</span>
          {/each}
        </p>
        <button
          type="button"
          class="shrink-0 px-2 py-1 text-[11px]"
          style="border: 1px solid var(--line); color: var(--muted); white-space: nowrap;"
          onclick={clearFilters}
        >
          絞り込みを解除
        </button>
      </div>
    {/if}

    {#if filterOpen}
      <div id="catalog-filters" class="panel flex flex-col gap-2">
        <div class="flex items-start gap-2">
          <span class="type-label shrink-0 pt-2" style="width: 3.2rem;">種別</span>
          <div class="flex min-w-0 flex-1 flex-wrap gap-1">
            {#each KIND_ORDER as kind (kind)}
              <FilterChip
                active={query.kind === kind}
                onclick={() => update({ kind: query.kind === kind ? undefined : kind })}
              >
                {KIND_LABELS[kind]}
              </FilterChip>
            {/each}
            <FilterChip active={hasAnime} onclick={toggleHasAnime}>アニメ化あり</FilterChip>
          </div>
        </div>

        <div class="flex items-start gap-2">
          <span class="type-label shrink-0 pt-2" style="width: 3.2rem;">状態</span>
          <div class="flex min-w-0 flex-1 flex-wrap gap-1">
            {#each STATUS_FILTERS as s (s.value)}
              <FilterChip
                active={query.status === s.value}
                onclick={() => update({ status: query.status === s.value ? undefined : s.value })}
              >
                {s.label}
              </FilterChip>
            {/each}
          </div>
        </div>

        <div class="flex items-start gap-2">
          <span class="type-label shrink-0 pt-2" style="width: 3.2rem;">年・季</span>
          <div class="flex min-w-0 flex-1 flex-wrap items-center gap-1">
            <input
              class="mono px-1.5 py-1 text-xs"
              style="border: 1px solid var(--line); background: var(--bg); color: var(--fg); width: 4.5rem;"
              type="text"
              inputmode="numeric"
              maxlength="4"
              placeholder="2026"
              aria-label="開始年（4桁）"
              value={yearText}
              oninput={(e) => onYearInput(e.currentTarget.value)}
            />
            {#each SEASON_FILTERS as s (s.value)}
              <FilterChip
                active={query.season === s.value}
                onclick={() => update({ season: query.season === s.value ? undefined : s.value })}
              >
                {s.label}
              </FilterChip>
            {/each}
          </div>
        </div>
      </div>
    {/if}

    <div bind:this={listTop} class="list-top"></div>

    {#if loadError}
      <div class="flex items-center justify-between gap-2 py-2" style="border-top: 1px solid var(--line);">
        <p class="text-xs" style="color: var(--muted);" role="status">{loadError}</p>
        <button
          type="button"
          class="shrink-0 px-2 py-1 text-[11px]"
          style="border: 1px solid var(--line); color: var(--muted); white-space: nowrap;"
          onclick={() => update({ page: query.page })}
        >
          再読み込み
        </button>
      </div>
    {/if}

    {#if result}
      {@const current = result.page}
      <p class="text-[11px]" style="color: var(--muted);" aria-live="polite">
        <span style="white-space: nowrap;"><span class="mono">{count(result.total)}</span> 件</span>
        {#if pages > 1}
          <span style="white-space: nowrap;"> · <span class="mono">{current} / {pages}</span> ページ</span>
        {/if}
        {#if loading}
          <span style="white-space: nowrap;"> · 読み込み中…</span>
        {/if}
      </p>

      <div
        class="results flex flex-col"
        style={`border-top: 1px solid var(--line); opacity: ${loading ? 0.6 : 1};`}
        aria-busy={loading}
      >
        {#if result.items.length === 0}
          <p class="empty py-6 text-center text-xs" style="color: var(--muted);">該当する作品がありません</p>
        {/if}
        {#each result.items as item (item.id)}
          {@const next = formatNext(item)}
          {@const years = yearRange(item.firstYear, item.lastYear)}
          {@const status = animeStatusLabel(item.animeStatus)}
          <a href={`#/catalog/${encodeURIComponent(item.id)}`} class="row-hover catalog-row">
            <!-- 表紙の幅は props で決まり CSS から変えられないので、PC だけ fill（格子の幅いっぱい）にする -->
            <Cover
              src={item.cover}
              title={item.title}
              mediaType={summaryMediaType(item.kinds)}
              size={viewport.wide ? 'fill' : 's'}
              onAir={isAnimeAiring(item.animeStatus)}
            />
            <div class="catalog-body">
              <div class="catalog-head flex items-baseline justify-between gap-2">
                <span class="catalog-title min-w-0 flex-1 text-sm font-medium lg:line-clamp-2" style="overflow-wrap: anywhere;">{item.title}</span>
                {#if years}
                  <span class="text-[11px]"><MonoTime text={years} muted /></span>
                {/if}
              </div>
              <div class="catalog-meta flex flex-wrap items-center gap-x-1.5 gap-y-1">
                {#each KIND_ORDER.filter((k) => item.kinds.includes(k)) as kind (kind)}
                  <CatalogKindBadge {kind} />
                {/each}
                {#if status}
                  <span class="text-[11px]" style="color: var(--muted); white-space: nowrap;">{status}</span>
                {/if}
                {#if next}
                  <span class="text-[11px]" style="white-space: nowrap;">
                    <span class="type-label" style="margin-right: 0.4em;">NEXT</span><MonoTime text={next.date} strong />
                  </span>
                  {#if next.label}
                    <span class="text-[11px]" style="color: var(--muted);">{next.label}</span>
                  {/if}
                {/if}
              </div>
            </div>
          </a>
        {/each}
      </div>

      {#if result.total > 0 && result.items.length === 0 && current > 1}
        <button
          type="button"
          class="self-start px-2 py-1 text-xs"
          style="border: 1px solid var(--fg); color: var(--fg); white-space: nowrap;"
          onclick={() => goPage(1)}
        >
          1ページ目へ
        </button>
      {/if}

      {#if pages > 1}
        <nav class="pager flex items-center justify-between gap-2 pt-1" aria-label="ページ送り">
          <button
            type="button"
            class="inline-flex items-center gap-1 px-3 py-1.5 text-xs disabled:opacity-40"
            style="border: 1px solid var(--fg); color: var(--fg); white-space: nowrap;"
            disabled={current <= 1 || loading}
            onclick={() => goPage(current - 1)}
          >
            <ChevronLeft size={14} aria-hidden="true" />前へ
          </button>
          <span class="mono text-[11px]" style="color: var(--muted); white-space: nowrap;">{current} / {pages}</span>
          <button
            type="button"
            class="inline-flex items-center gap-1 px-3 py-1.5 text-xs disabled:opacity-40"
            style="border: 1px solid var(--fg); color: var(--fg); white-space: nowrap;"
            disabled={current >= pages || loading}
            onclick={() => goPage(current + 1)}
          >
            次へ<ChevronRight size={14} aria-hidden="true" />
          </button>
        </nav>
      {/if}
    {:else if loading}
      <p class="py-2 text-center text-xs" style="color: var(--muted);" role="status">読み込み中…</p>
      <div class="results flex flex-col" style="border-top: 1px solid var(--line);" aria-hidden="true">
        {#each SKELETON_ROWS as n (n)}
          <div class="catalog-row">
            <div class="sk-cover"></div>
            <div class="catalog-body">
              <div class="sk-bar" style="width: 70%;"></div>
              <div class="sk-bar catalog-meta" style="width: 40%;"></div>
            </div>
          </div>
        {/each}
      </div>
    {/if}
  {/if}
</section>

<style>
  /* スマホは検索・近道・並びを縦に積む親の flex にそのまま置く（PC だけ1つの帯にする） */
  .controls {
    display: contents;
  }
  .list-top {
    scroll-margin-top: 3.5rem;
  }
  .search-field {
    position: relative;
  }
  .search-icon {
    position: absolute;
    left: 0;
    top: 50%;
    transform: translateY(-50%);
    display: flex;
    color: var(--muted);
    pointer-events: none;
  }

  .toolbar {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 0.5rem 0.75rem;
  }
  .filter-toggle {
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
    padding: 0.35rem 0.55rem;
    font-size: 11px;
    border: 1px solid var(--line);
    color: var(--fg);
    background: transparent;
    white-space: nowrap;
    transition: border-color 0.25s ease;
  }
  .filter-toggle.on {
    border-color: var(--fg);
  }
  .filter-count {
    min-width: 1.15rem;
    padding: 0 0.3rem;
    font-size: 10px;
    font-weight: 500;
    line-height: 1.35rem;
    text-align: center;
    background: var(--fg);
    color: var(--bg);
  }
  .chev {
    display: inline-flex;
    color: var(--muted);
    transition: transform 0.2s ease;
  }
  .chev.open {
    transform: rotate(180deg);
  }
  .sort {
    display: inline-flex;
    align-items: center;
    gap: 0.5rem;
    min-width: 0;
  }

  .summary {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.5rem;
  }
  .summary-text {
    display: flex;
    flex-wrap: wrap;
    flex: 1;
    gap: 0.1rem 0.7rem;
    min-width: 0;
    margin: 0;
    font-size: 11px;
    color: var(--muted);
  }
  .summary-item {
    white-space: nowrap;
  }

  .panel {
    padding: 0.75rem 0;
    border-top: 1px solid var(--line);
    border-bottom: 1px solid var(--line);
  }

  .catalog-row {
    display: flex;
    align-items: flex-start;
    gap: 0.75rem;
    padding: 0.7rem 0;
    border-bottom: 1px solid var(--line);
    min-width: 0;
  }
  /* 表紙 s（幅 48px・高さ 72px）に高さを合わせ、題名は上、媒体・状態・NEXT は下に揃える */
  .catalog-body {
    display: flex;
    flex-direction: column;
    gap: 0.35rem;
    flex: 1;
    min-width: 0;
    min-height: 72px;
  }
  .catalog-meta {
    margin-top: auto;
  }

  .sk-cover {
    width: 48px;
    height: 72px;
    flex-shrink: 0;
    border-radius: 2px;
    background: var(--cover-ph);
  }
  .sk-bar {
    height: 0.8rem;
    border-radius: 2px;
    background: var(--cover-ph);
  }
  /* よく使う見え方への近道 */
  .presets {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
  }
  .preset {
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
    padding: 0.35rem 0.7rem;
    border: 1px solid var(--line);
    font-size: 0.75rem;
    color: var(--fg);
    background: var(--bg);
    white-space: nowrap;
  }
  .preset[aria-pressed='true'] {
    border-color: var(--fg);
    background: var(--fg);
    color: var(--bg);
  }

  /* PC: 絞り込みを1行の帯に、結果を表紙の格子にする */
  @media (min-width: 64rem) {
    .controls {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 0.75rem 1.5rem;
    }
    .search-field {
      flex: 1 1 20rem;
      max-width: 30rem;
    }
    .toolbar {
      margin-left: auto;
      gap: 0.5rem 1.25rem;
    }
    .list-top {
      scroll-margin-top: 4.5rem;
    }

    .results {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
      gap: 1.5rem 1rem;
      padding-top: 1.25rem;
    }
    .results > .empty {
      grid-column: 1 / -1;
    }
    .catalog-row {
      flex-direction: column;
      align-items: stretch;
      gap: 0.5rem;
      padding: 0;
      border-bottom: 0;
    }
    .catalog-row:hover {
      background: none;
    }
    .catalog-row:hover .catalog-title {
      text-decoration: underline;
      text-underline-offset: 3px;
    }
    .catalog-body {
      flex: none;
      gap: 0.3rem;
      min-height: 0;
    }
    .catalog-head {
      flex-direction: column;
      align-items: flex-start;
      gap: 0.1rem;
    }
    .catalog-title {
      flex: none;
    }
    .catalog-meta {
      margin-top: 0;
    }
    .sk-cover {
      width: 100%;
      height: auto;
      aspect-ratio: 2 / 3;
    }
    .pager {
      justify-content: center;
      gap: 1.5rem;
    }
  }
</style>
