<script lang="ts">
  import { onMount } from 'svelte';
  import { LayoutGrid, List, Plus } from '@lucide/svelte';
  import { listEditions, listReleases, listWorks, createWork } from '../lib/db/store';
  import type { Edition, Release, Work, MediaType, Status } from '../lib/db/schema';
  import { navigate } from '../lib/router.svelte';
  import Cover from '../lib/components/Cover.svelte';
  import FilterChip from '../lib/components/FilterChip.svelte';
  import { MEDIA_TYPE_LABELS, MEDIA_TYPES, STATUS_LABELS, STATUSES } from '../lib/components/labels';
  import {
    editionDisambiguators,
    libraryCover,
    loadLibraryLayout,
    progressRatio,
    progressText,
    saveLibraryLayout,
    seasonStartByEdition,
    sortLibraryEditions,
    type LibraryLayout,
  } from '../lib/components/library-view';
  import { broadcastDayJst, onAirEditionIds } from '../lib/components/onair';

  let editions = $state<Edition[]>([]);
  let works = $state<Work[]>([]);
  let releases = $state<Release[]>([]);
  let loaded = $state(false);
  let today = $state(broadcastDayJst());
  let statusFilter = $state<Status | 'all'>('all');
  let mediaTypeFilter = $state<MediaType | 'all'>('all');
  // 既定は表紙の棚。端末に保存した選択があればそれ（localStorage が使えなければ棚）
  let layout = $state<LibraryLayout>(loadLibraryLayout());
  let showCreate = $state(false);
  let newTitle = $state('');
  let creating = $state(false);

  const workMap = $derived(new Map(works.map((w) => [w.id, w])));
  const workTitleOf = (workId: string) => workMap.get(workId)?.title ?? '';

  const editionsByWork = $derived.by(() => {
    const map = new Map<string, Edition[]>();
    for (const edition of editions) {
      const list = map.get(edition.workId) ?? [];
      list.push(edition);
      map.set(edition.workId, list);
    }
    return map;
  });

  // 今日（放送日・朝5時区切り）に放送・配信がある Edition。表紙にランプを付ける
  const onAirIds = $derived(onAirEditionIds(releases, new Map(editions.map((e) => [e.id, e])), today));

  // 同じ Work・同じ媒体の Edition を見分ける副題は、絞り込み前の全 Edition で決める（絞り込みで表示が変わらないように）
  const seasonStarts = $derived(seasonStartByEdition(releases));
  const subtitles = $derived(editionDisambiguators(editions, workTitleOf, seasonStarts));

  const filtered = $derived(
    sortLibraryEditions(
      editions
        .filter((e) => statusFilter === 'all' || e.status === statusFilter)
        .filter((e) => mediaTypeFilter === 'all' || e.mediaType === mediaTypeFilter),
      workTitleOf,
      seasonStarts,
    ),
  );

  async function refresh() {
    const [nextEditions, nextWorks, nextReleases] = await Promise.all([listEditions(), listWorks(), listReleases()]);
    editions = nextEditions;
    works = nextWorks;
    releases = nextReleases;
    today = broadcastDayJst();
    loaded = true;
  }

  onMount(refresh);

  function setLayout(next: LibraryLayout) {
    layout = next;
    saveLibraryLayout(next);
  }

  function coverOf(edition: Edition) {
    return libraryCover(edition, workMap.get(edition.workId), editionsByWork.get(edition.workId) ?? []);
  }

  function titleOf(edition: Edition): string {
    return workMap.get(edition.workId)?.title ?? '(不明な作品)';
  }

  /** 開いた Edition のタブで Work 詳細を開く */
  function workHref(edition: Edition): string {
    return `#/work/${encodeURIComponent(edition.workId)}?edition=${encodeURIComponent(edition.id)}`;
  }

  function focusOnMount(node: HTMLElement) {
    node.focus();
  }

  async function onCreateWork(event?: Event) {
    event?.preventDefault();
    const title = newTitle.trim();
    if (!title || creating) return;
    creating = true;
    try {
      const work = await createWork({ title });
      newTitle = '';
      showCreate = false;
      navigate(`#/work/${work.id}`);
    } finally {
      creating = false;
    }
  }
</script>

<section class="library">
  <div class="lib-head">
    <h1 class="page-title">Library<span class="type-label" style="margin-left: 0.8em;">追跡中の作品</span></h1>
    <div class="view-toggle" role="group" aria-label="表示の切り替え">
      <button
        type="button"
        class="toggle"
        aria-pressed={layout === 'shelf'}
        aria-label="表紙の棚で表示"
        title="棚"
        onclick={() => setLayout('shelf')}
      >
        <LayoutGrid size={18} />
      </button>
      <button
        type="button"
        class="toggle"
        aria-pressed={layout === 'list'}
        aria-label="一覧で表示"
        title="一覧"
        onclick={() => setLayout('list')}
      >
        <List size={18} />
      </button>
    </div>
  </div>

  <div class="filters">
    <div class="filter-row">
      <span class="type-label filter-label">状態</span>
      <div class="chips">
        <FilterChip active={statusFilter === 'all'} onclick={() => (statusFilter = 'all')}>すべて</FilterChip>
        {#each STATUSES as s (s)}
          <FilterChip active={statusFilter === s} onclick={() => (statusFilter = s)}>{STATUS_LABELS[s]}</FilterChip>
        {/each}
      </div>
    </div>
    <div class="filter-row">
      <span class="type-label filter-label">媒体</span>
      <div class="chips">
        <FilterChip active={mediaTypeFilter === 'all'} onclick={() => (mediaTypeFilter = 'all')}>すべて</FilterChip>
        {#each MEDIA_TYPES as m (m)}
          <FilterChip active={mediaTypeFilter === m} onclick={() => (mediaTypeFilter = m)}>
            {MEDIA_TYPE_LABELS[m]}
          </FilterChip>
        {/each}
      </div>
    </div>
  </div>

  <div class="shou toolbar">
    <p class="count" aria-live="polite">
      <span class="mono count-num">{filtered.length}</span>
      {#if filtered.length !== editions.length}
        <span class="mono count-total">/ {editions.length}</span>
      {/if}
      <span class="no">EDITIONS</span>
    </p>
    <button type="button" class="btn btn-main" aria-expanded={showCreate} onclick={() => (showCreate = !showCreate)}>
      <Plus size={14} />新規作品
    </button>
  </div>

  {#if showCreate}
    <form class="create" onsubmit={onCreateWork}>
      <input
        class="field"
        placeholder="作品タイトル"
        aria-label="作品タイトル"
        bind:value={newTitle}
        {@attach focusOnMount}
        onkeydown={(e) => {
          if (e.key === 'Escape') showCreate = false;
        }}
      />
      <button type="submit" class="btn btn-main" disabled={!newTitle.trim() || creating}>作成</button>
    </form>
  {/if}

  {#if loaded && editions.length === 0}
    <div class="empty-lib">
      <p class="empty">まだ作品がありません。カタログや検索から追加できます。</p>
      <div class="empty-actions">
        <a class="btn btn-main" href="#/catalog">カタログを見る</a>
        <a class="btn" href="#/search">検索する</a>
      </div>
    </div>
  {:else if loaded && filtered.length === 0}
    <p class="empty">該当する Edition がありません</p>
  {:else if layout === 'shelf'}
    <ul class="shelf">
      {#each filtered as edition (edition.id)}
        {@const cover = coverOf(edition)}
        {@const ratio = progressRatio(edition.progress)}
        {@const subtitle = subtitles.get(edition.id)}
        <li class="shelf-cell">
          <a class="shelf-item" href={workHref(edition)}>
            <span class="shelf-cover" aria-hidden="true">
              <Cover
                src={cover.url}
                title={cover.title}
                mediaType={cover.mediaType}
                size="fill"
                onAir={onAirIds.has(edition.id)}
              />
              {#if ratio !== undefined}
                <span class="progress"><span class="progress-fill" style:width={`${ratio * 100}%`}></span></span>
              {/if}
            </span>
            <span class="shelf-title">{titleOf(edition)}</span>
            {#if subtitle}
              <span class="shelf-sub">{subtitle}</span>
            {/if}
            <span class="meta">
              <span>{STATUS_LABELS[edition.status]}</span>
              <span class="mono">{MEDIA_TYPE_LABELS[edition.mediaType]}</span>
            </span>
            {#if onAirIds.has(edition.id)}
              <span class="sr-only">今日放送</span>
            {/if}
          </a>
        </li>
      {/each}
    </ul>
  {:else}
    <ul class="list">
      {#each filtered as edition (edition.id)}
        {@const cover = coverOf(edition)}
        {@const subtitle = subtitles.get(edition.id)}
        <li>
          <a class="list-row" href={workHref(edition)}>
            <span class="list-cover" aria-hidden="true">
              <Cover
                src={cover.url}
                title={cover.title}
                mediaType={cover.mediaType}
                size="xs"
                onAir={onAirIds.has(edition.id)}
              />
            </span>
            <span class="list-text">
              <span class="list-title">{titleOf(edition)}</span>
              {#if subtitle}
                <span class="list-sub">{subtitle}</span>
              {/if}
              <span class="meta">
                <span>{STATUS_LABELS[edition.status]}</span>
                <span class="mono">{MEDIA_TYPE_LABELS[edition.mediaType]}</span>
                {#if onAirIds.has(edition.id)}
                  <span class="sr-only">今日放送</span>
                {/if}
              </span>
            </span>
            {#if edition.progress}
              <span class="mono list-progress">{progressText(edition.progress)}</span>
            {/if}
          </a>
        </li>
      {/each}
    </ul>
  {/if}
</section>

<style>
  .library {
    display: flex;
    flex-direction: column;
    gap: 1rem;
    padding: 1rem;
    min-width: 0;
  }

  /* ─── 見出しと表示の切り替え ─── */
  .lib-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.75rem;
    min-width: 0;
  }
  .lib-head .page-title {
    margin: 0;
    min-width: 0;
  }
  .view-toggle {
    display: inline-flex;
    flex-shrink: 0;
  }
  .toggle {
    position: relative;
    display: grid;
    place-items: center;
    width: 2.25rem;
    height: 2.25rem;
    border: 1px solid var(--line);
    color: var(--muted);
    background: transparent;
    transition:
      color 0.2s ease,
      background 0.2s ease;
  }
  .toggle + .toggle {
    margin-left: -1px;
  }
  .toggle:hover {
    color: var(--fg);
  }
  .toggle[aria-pressed='true'] {
    z-index: 1;
    border-color: var(--fg);
    background: var(--fg);
    color: var(--bg);
  }
  .toggle:focus-visible {
    z-index: 2;
    outline: 2px solid var(--fg);
    outline-offset: 2px;
  }

  /* ─── 絞り込み ─── */
  .filters {
    display: flex;
    flex-direction: column;
    gap: 0.4rem;
  }
  .filter-row {
    display: flex;
    align-items: flex-start;
    gap: 0.5rem;
    min-width: 0;
  }
  .filter-label {
    width: 3.2rem;
    flex-shrink: 0;
    padding-top: 0.45rem;
  }
  .chips {
    display: flex;
    flex: 1;
    flex-wrap: wrap;
    gap: 0.25rem;
    min-width: 0;
  }

  /* ─── 件数と作成 ─── */
  .toolbar {
    align-items: center;
    margin: 0.25rem 0 0;
    padding-bottom: 0.5rem;
  }
  .count {
    display: flex;
    align-items: baseline;
    gap: 0.4rem;
    margin: 0;
    min-width: 0;
  }
  .count-num {
    font-size: 1.125rem;
    font-weight: 500;
    line-height: 1;
  }
  .count-total {
    font-size: 0.75rem;
    color: var(--muted);
  }
  .create {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    min-width: 0;
  }
  .create .field {
    flex: 1;
  }

  /* ─── 棚（表紙の 3 列） ─── */
  .shelf {
    list-style: none;
    margin: 0;
    padding: 0;
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 1.25rem 0.75rem;
  }
  .shelf-cell {
    min-width: 0;
  }
  .shelf-item {
    display: flex;
    flex-direction: column;
    gap: 0.15rem;
    min-width: 0;
    color: var(--fg);
    text-decoration: none;
  }
  .shelf-item:focus-visible {
    outline: 2px solid var(--fg);
    outline-offset: 3px;
  }
  .shelf-cover {
    position: relative;
    display: block;
    margin-bottom: 0.35rem;
    transition: transform 0.25s ease;
  }
  .shelf-item:hover .shelf-cover {
    transform: translateY(-2px);
  }
  .shelf-item:hover .shelf-title {
    text-decoration: underline;
    text-underline-offset: 2px;
  }
  /* 進捗は表紙の下端に細い線で（全体数が分かる時だけ） */
  .progress {
    position: absolute;
    left: 0;
    right: 0;
    bottom: 0;
    height: 3px;
    background: color-mix(in srgb, var(--bg) 70%, transparent);
  }
  .progress-fill {
    display: block;
    height: 100%;
    background: var(--fg);
  }
  .shelf-title {
    font-size: 0.75rem;
    font-weight: 700;
    line-height: 1.35;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .shelf-sub,
  .list-sub {
    font-size: 0.625rem;
    color: var(--muted);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .meta {
    display: flex;
    gap: 0.4rem;
    font-size: 0.625rem;
    color: var(--muted);
    overflow: hidden;
    white-space: nowrap;
  }

  /* ─── 一覧（表紙 xs 付きの行） ─── */
  .list {
    list-style: none;
    margin: 0;
    padding: 0;
  }
  .list-row {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    padding: 0.5rem 0;
    border-bottom: 1px solid var(--line);
    color: var(--fg);
    text-decoration: none;
    min-width: 0;
    transition: background 0.2s ease;
  }
  .list-row:hover {
    background: color-mix(in srgb, var(--fg) 4%, transparent);
  }
  .list-row:focus-visible {
    outline: 2px solid var(--fg);
    outline-offset: -2px;
  }
  .list-cover {
    display: block;
    flex-shrink: 0;
  }
  .list-text {
    display: flex;
    flex: 1;
    flex-direction: column;
    gap: 0.05rem;
    min-width: 0;
  }
  .list-title {
    font-size: 0.875rem;
    line-height: 1.35;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .list-progress {
    flex-shrink: 0;
    font-size: 0.75rem;
    color: var(--muted);
    white-space: nowrap;
  }

  /* ─── 空のとき ─── */
  .empty {
    margin: 0;
    padding: 1.5rem 0;
    font-size: 0.75rem;
    text-align: center;
    color: var(--muted);
  }
  .empty-lib {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 0.25rem;
    padding-bottom: 1.5rem;
  }
  .empty-actions {
    display: flex;
    flex-wrap: wrap;
    justify-content: center;
    gap: 0.5rem;
  }

  .field {
    min-width: 0;
    min-height: 2rem;
    padding: 0.25rem 0.5rem;
    font-size: 0.875rem;
    border: 1px solid var(--line);
    border-radius: 0;
    background: var(--bg);
    color: var(--fg);
  }
  :global(.dark) .field {
    color-scheme: dark;
  }
  .btn {
    display: inline-flex;
    align-items: center;
    gap: 0.25rem;
    min-height: 2rem;
    padding: 0.125rem 0.75rem;
    font-size: 0.75rem;
    border: 1px solid var(--line);
    color: var(--fg);
    background: transparent;
    text-decoration: none;
    white-space: nowrap;
  }
  .btn:disabled {
    color: var(--muted);
  }
  .btn-main {
    border-color: var(--fg);
  }

  @media (prefers-reduced-motion: reduce) {
    .toggle,
    .shelf-cover,
    .list-row {
      transition: none;
    }
    .shelf-item:hover .shelf-cover {
      transform: none;
    }
  }
</style>
