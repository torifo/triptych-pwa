<script lang="ts">
  import { onDestroy, onMount, untrack } from 'svelte';
  import { ApiError } from '../lib/api';
  import {
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
    animeStatusLabel,
    formatJstDateTime,
    formatNext,
    latestHarvestAt,
    yearRange,
  } from '../lib/catalog/view';
  import CatalogKindBadge from '../lib/components/CatalogKindBadge.svelte';
  import FilterChip from '../lib/components/FilterChip.svelte';
  import { replaceQuery, route } from '../lib/router.svelte';

  const BUILD_COMMAND = 'python -m backend.catalog build';
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
</script>

<section class="flex flex-col gap-3 p-4">
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
          <span style="white-space: nowrap;">{count(stats.works)} 作品</span>
          <span style="white-space: nowrap;"> · {count(stats.entries)} 件</span>
          {#if harvestedAt}
            <span style="white-space: nowrap;"> · 最終収集 {formatJstDateTime(harvestedAt)}</span>
          {/if}
        {:else if statsError}
          <span role="status">件数を取得できませんでした</span>
        {/if}
      </p>
      <div class="flex items-center gap-1">
        {#each EXPORT_FORMATS as format (format)}
          <button
            type="button"
            class="px-2 py-1 text-[11px] disabled:opacity-40"
            style="border: 1px solid var(--fg); color: var(--fg); white-space: nowrap; letter-spacing: 0.06em;"
            disabled={exporting !== null}
            aria-label={`カタログ全件を ${format.toUpperCase()} でダウンロード`}
            onclick={() => exportAs(format)}
          >
            {exporting === format ? '書き出し中…' : format.toUpperCase()}
          </button>
        {/each}
      </div>
    </div>
    {#if exportError}
      <p class="text-[11px]" style="color: var(--muted);" role="status">{exportError}</p>
    {/if}

    <input
      class="w-full px-0 py-1.5"
      style="border: 0; border-bottom: 1px solid var(--fg); background: var(--bg); color: var(--fg); font-size: 1.05rem; outline: none; min-width: 0;"
      type="search"
      placeholder="題名・別名・読み"
      aria-label="作品を題名・別名・読みで検索"
      value={qText}
      oninput={(e) => onSearchInput(e.currentTarget.value)}
      onkeydown={(e) => {
        if (e.key === 'Enter' && !e.isComposing) searchDebouncer.flush();
      }}
    />

    <div class="flex flex-col gap-2">
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
            class="px-1.5 py-1 text-xs"
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

      <div class="flex items-center gap-2">
        <span class="type-label shrink-0" style="width: 3.2rem;">並び</span>
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
        {#if filtered}
          <button
            type="button"
            class="ml-auto px-2 py-1 text-[11px]"
            style="border: 1px solid var(--line); color: var(--muted); white-space: nowrap;"
            onclick={clearFilters}
          >
            絞り込みを解除
          </button>
        {/if}
      </div>
    </div>

    <div bind:this={listTop} style="scroll-margin-top: 3.5rem;"></div>

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
        <span style="white-space: nowrap;">{count(result.total)} 件</span>
        {#if pages > 1}
          <span style="white-space: nowrap;"> · {current} / {pages} ページ</span>
        {/if}
        {#if loading}
          <span style="white-space: nowrap;"> · 読み込み中…</span>
        {/if}
      </p>

      <div
        class="flex flex-col"
        style={`border-top: 1px solid var(--line); opacity: ${loading ? 0.6 : 1};`}
        aria-busy={loading}
      >
        {#if result.items.length === 0}
          <p class="py-6 text-center text-xs" style="color: var(--muted);">該当する作品がありません</p>
        {/if}
        {#each result.items as item (item.id)}
          {@const next = formatNext(item)}
          {@const years = yearRange(item.firstYear, item.lastYear)}
          {@const status = animeStatusLabel(item.animeStatus)}
          <a
            href={`#/catalog/${encodeURIComponent(item.id)}`}
            class="row-hover block py-2.5"
            style="border-bottom: 1px solid var(--line); min-width: 0;"
          >
            <div class="flex items-baseline justify-between gap-2">
              <span class="min-w-0 flex-1 text-sm font-medium" style="overflow-wrap: anywhere;">{item.title}</span>
              {#if years}
                <span class="shrink-0 text-[11px]" style="color: var(--muted); white-space: nowrap;">{years}</span>
              {/if}
            </div>
            <div class="mt-1 flex flex-wrap items-center gap-x-1.5 gap-y-1">
              {#each KIND_ORDER.filter((k) => item.kinds.includes(k)) as kind (kind)}
                <CatalogKindBadge {kind} />
              {/each}
              {#if status}
                <span class="text-[11px]" style="color: var(--muted); white-space: nowrap;">{status}</span>
              {/if}
              {#if next}
                <span class="text-[11px]" style="white-space: nowrap;">
                  <span class="type-label" style="margin-right: 0.4em;">NEXT</span>{next.date}
                </span>
                {#if next.label}
                  <span class="text-[11px]" style="color: var(--muted);">{next.label}</span>
                {/if}
              {/if}
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
        <nav class="flex items-center justify-between gap-2 pt-1" aria-label="ページ送り">
          <button
            type="button"
            class="px-3 py-1.5 text-xs disabled:opacity-40"
            style="border: 1px solid var(--fg); color: var(--fg); white-space: nowrap;"
            disabled={current <= 1 || loading}
            onclick={() => goPage(current - 1)}
          >
            ‹ 前へ
          </button>
          <span class="text-[11px]" style="color: var(--muted); white-space: nowrap;">{current} / {pages}</span>
          <button
            type="button"
            class="px-3 py-1.5 text-xs disabled:opacity-40"
            style="border: 1px solid var(--fg); color: var(--fg); white-space: nowrap;"
            disabled={current >= pages || loading}
            onclick={() => goPage(current + 1)}
          >
            次へ ›
          </button>
        </nav>
      {/if}
    {:else if loading}
      <p class="py-6 text-center text-xs" style="color: var(--muted);">読み込み中…</p>
    {/if}
  {/if}
</section>
