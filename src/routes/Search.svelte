<script lang="ts">
  import { onMount } from 'svelte';
  import { apiGet, ApiError } from '../lib/api';
  import { normalizeTitle } from '../lib/normalize';
  import { listWorks, listEditions, createWork, createEdition } from '../lib/db/store';
  import type { Work, Edition, MediaType, Source } from '../lib/db/schema';
  import { navigate } from '../lib/router.svelte';
  import MediaBadge from '../lib/components/MediaBadge.svelte';
  import { SOURCE_LABELS } from '../lib/components/labels';
  import { dedupeResults, registeredSourceKeys, sourceRefKey } from '../lib/components/release-view';

  // collector レスポンス型（backend/dto.py の SearchResult / backend/api/search.py の
  // SearchResponse・OriginCandidate に対応。api.ts 側には型を持たないため、ここで定義する）
  interface RemoteSearchResult {
    source: Source;
    externalId: string;
    mediaType: MediaType;
    title: string;
    cover?: string | null;
    url: string;
    subtitle?: string | null; // 作者・出版社・巻数など
    direct?: boolean; // URL / ncode / ISBN から直接解決された結果
  }
  interface RemoteSearchError {
    source: string;
    message: string;
  }
  interface SearchResponse {
    results: RemoteSearchResult[];
    errors: RemoteSearchError[];
  }
  interface OriginCandidate {
    source: 'anilist';
    externalId: string;
    mediaType: MediaType;
    title: string;
    url: string;
  }

  let query = $state('');
  let loading = $state(false);
  let searched = $state(false);
  let results = $state<RemoteSearchResult[]>([]);
  let sourceErrors = $state<RemoteSearchError[]>([]);
  let requestError = $state<string | null>(null);

  let works = $state<Work[]>([]);
  let editions = $state<Edition[]>([]);
  const workByNormalizedTitle = $derived(new Map(works.map((w) => [w.titleNormalized, w])));
  // どれかの Edition の sourceRefs にある (source, externalId)。登録済みの結果は押せなくする
  const registeredKeys = $derived(registeredSourceKeys(editions));

  // 登録処理中の result を externalId+source キーで管理（多重クリック防止）
  let registeringKey = $state<string | null>(null);

  // アニメ登録直後の「原作候補」提示パネル
  let originWorkId = $state<string | null>(null);
  let originWorkTitle = $state<string>('');
  let originAnimeExternalId = $state<string | null>(null);
  let originCandidates = $state<OriginCandidate[]>([]);
  let originLoading = $state(false);
  let originError = $state<string | null>(null);
  let addingOriginKey = $state<string | null>(null);

  function resultKey(r: RemoteSearchResult): string {
    return `${r.source}:${r.externalId}:${r.mediaType}`;
  }

  function isRegistered(r: RemoteSearchResult): boolean {
    return registeredKeys.has(sourceRefKey(r.source, r.externalId));
  }

  async function refreshWorks() {
    works = await listWorks();
    editions = await listEditions();
  }

  onMount(refreshWorks);

  async function runSearch() {
    const q = query.trim();
    if (!q || loading) return;
    loading = true;
    requestError = null;
    originWorkId = null;
    originCandidates = [];
    originError = null;
    try {
      const data = await apiGet<SearchResponse>('/api/search', { q });
      results = dedupeResults(data.results);
      sourceErrors = data.errors;
      searched = true;
    } catch (err) {
      results = [];
      sourceErrors = [];
      searched = true;
      requestError = err instanceof ApiError ? `検索に失敗しました (${err.status})` : '検索に失敗しました';
    } finally {
      loading = false;
    }
  }

  function existingWorkFor(result: RemoteSearchResult): Work | undefined {
    return workByNormalizedTitle.get(normalizeTitle(result.title));
  }

  async function registerResult(result: RemoteSearchResult, targetWork?: Work) {
    const key = resultKey(result);
    if (registeringKey || isRegistered(result)) return;
    registeringKey = key;
    try {
      const work =
        targetWork ??
        (await createWork({
          title: result.title,
        }));

      const edition = await createEdition({
        workId: work.id,
        mediaType: result.mediaType,
        title: result.title,
        sourceRefs: [{ source: result.source, externalId: result.externalId, url: result.url }],
        coverUrl: result.cover ?? undefined,
        linkSource: 'manual',
      });

      await refreshWorks();

      if (result.mediaType === 'anime') {
        originWorkId = work.id;
        originWorkTitle = work.title;
        originAnimeExternalId = result.externalId;
        await loadOriginCandidates(result.externalId);
      } else {
        navigate(`#/work/${work.id}`);
      }
      void edition;
    } finally {
      registeringKey = null;
    }
  }

  async function loadOriginCandidates(animeExternalId: string) {
    originLoading = true;
    originError = null;
    originCandidates = [];
    try {
      originCandidates = await apiGet<OriginCandidate[]>(
        `/api/search/anilist-origins/${animeExternalId}`,
      );
    } catch (err) {
      originError =
        err instanceof ApiError ? `原作候補の取得に失敗しました (${err.status})` : '原作候補の取得に失敗しました';
    } finally {
      originLoading = false;
    }
  }

  async function addOriginCandidate(candidate: OriginCandidate) {
    if (!originWorkId || addingOriginKey) return;
    const key = `${candidate.source}:${candidate.externalId}`;
    addingOriginKey = key;
    try {
      await createEdition({
        workId: originWorkId,
        mediaType: candidate.mediaType,
        title: candidate.title,
        sourceRefs: [{ source: candidate.source, externalId: candidate.externalId, url: candidate.url }],
        linkSource: 'auto',
      });
      originCandidates = originCandidates.filter(
        (c) => `${c.source}:${c.externalId}` !== key,
      );
      await refreshWorks();
    } finally {
      addingOriginKey = null;
    }
  }

  function finishOriginStep() {
    if (originWorkId) navigate(`#/work/${originWorkId}`);
  }
</script>

<section class="flex flex-col gap-3 p-4">
  <h1 class="page-title">検索<span class="type-label" style="margin-left: 0.8em;">SEARCH &amp; ADD</span></h1>

  <div class="flex items-center gap-2">
    <input
      class="min-w-0 flex-1 px-0 py-1.5"
      style="border: 0; border-bottom: 1px solid var(--fg); background: var(--bg); color: var(--fg); font-size: 1.05rem; outline: none;"
      placeholder="タイトル・URL・ncode・ISBN"
      aria-label="作品タイトル・URL・ncode・ISBN で検索"
      bind:value={query}
      onkeydown={(e) => {
        if (e.key === 'Enter') runSearch();
      }}
    />
    <button
      type="button"
      class="px-3 py-1.5 text-xs font-medium"
      style="border: 1px solid var(--fg); color: var(--bg); background: var(--fg); white-space: nowrap; letter-spacing: 0.08em;"
      disabled={!query.trim() || loading}
      onclick={runSearch}
    >
      {loading ? '検索中…' : '検索'}
    </button>
  </div>

  {#if requestError}
    <p class="text-xs" style="color: var(--muted);">{requestError}</p>
  {/if}

  {#if sourceErrors.length > 0}
    <div class="flex flex-col gap-0.5 p-2 text-[11px]" style="border: 1px solid var(--line); color: var(--muted);">
      {#each sourceErrors as e (e.source)}
        <p style="white-space: nowrap; overflow-x: auto;">
          {SOURCE_LABELS[e.source as Source] ?? e.source} が利用できません: {e.message}
        </p>
      {/each}
    </div>
  {/if}

  {#if originWorkId}
    <div class="flex flex-col gap-2 p-3" style="border: 1px solid var(--fg);">
      <div class="flex items-center justify-between gap-2">
        <h2 class="text-sm font-bold" style="min-width: 0; overflow-wrap: anywhere;">
          「{originWorkTitle}」の原作候補
        </h2>
        <button
          type="button"
          class="px-2 py-1 text-[11px]"
          style="border: 1px solid var(--line); color: var(--muted); white-space: nowrap;"
          onclick={finishOriginStep}
        >
          スキップ
        </button>
      </div>

      {#if originLoading}
        <p class="text-xs" style="color: var(--muted);">原作候補を取得中…</p>
      {:else if originError}
        <p class="text-xs" style="color: var(--muted);">{originError}</p>
      {:else if originCandidates.length === 0}
        <p class="text-xs" style="color: var(--muted);">原作候補は見つかりませんでした</p>
      {:else}
        <div class="flex flex-col" style="border-top: 1px solid var(--line);">
          {#each originCandidates as candidate (candidate.externalId)}
            <div
              class="flex items-center gap-2 py-2"
              style="border-bottom: 1px solid var(--line); min-width: 0;"
            >
              <MediaBadge mediaType={candidate.mediaType} />
              <span class="min-w-0 flex-1 truncate text-sm">{candidate.title}</span>
              <button
                type="button"
                class="px-2 py-1 text-[11px]"
                style="border: 1px solid var(--fg); color: var(--fg); white-space: nowrap;"
                disabled={addingOriginKey === `${candidate.source}:${candidate.externalId}`}
                onclick={() => addOriginCandidate(candidate)}
              >
                追加
              </button>
            </div>
          {/each}
        </div>
      {/if}

      <button
        type="button"
        class="self-start px-2 py-1 text-xs"
        style="border: 1px solid var(--fg); color: var(--fg); white-space: nowrap;"
        onclick={finishOriginStep}
      >
        作品詳細へ
      </button>
    </div>
  {/if}

  <div class="flex flex-col" style="border-top: 1px solid var(--line);">
    {#if searched && results.length === 0 && !requestError}
      <p class="py-6 text-center text-xs" style="color: var(--muted);">該当する結果がありません</p>
    {/if}
    {#each results as result (resultKey(result))}
      {@const existing = existingWorkFor(result)}
      {@const registered = isRegistered(result)}
      {@const busy = registeringKey === resultKey(result)}
      <div
        class="row-hover flex items-start gap-2 py-2"
        style="border-bottom: 1px solid var(--line); min-width: 0;"
      >
        <span class="pt-0.5"><MediaBadge mediaType={result.mediaType} /></span>
        <div class="min-w-0 flex-1">
          <p class="text-sm" style="overflow-wrap: anywhere;">{result.title}</p>
          {#if result.subtitle}
            <p class="truncate text-[11px]" style="color: var(--muted);">{result.subtitle}</p>
          {/if}
          <p class="flex flex-wrap items-center gap-x-2 text-[10px]" style="color: var(--muted);">
            <span style="white-space: nowrap;">{SOURCE_LABELS[result.source] ?? result.source}</span>
            {#if result.direct}
              <span class="type-label">URL から解決</span>
            {/if}
          </p>
        </div>
        <div class="flex shrink-0 flex-col items-stretch gap-1">
          {#if registered}
            <button
              type="button"
              class="px-2 py-1 text-[11px]"
              style="border: 1px solid var(--line); color: var(--muted); white-space: nowrap;"
              disabled
              aria-label={`${result.title} は登録済み`}
            >
              登録済み
            </button>
          {:else if existing}
            <button
              type="button"
              class="px-2 py-1 text-[11px]"
              style="border: 1px solid var(--fg); color: var(--fg); white-space: nowrap;"
              disabled={busy}
              onclick={() => registerResult(result, existing)}
            >
              既存作品に追加
            </button>
            <button
              type="button"
              class="px-2 py-1 text-[11px]"
              style="border: 1px solid var(--line); color: var(--muted); white-space: nowrap;"
              disabled={busy}
              onclick={() => registerResult(result)}
            >
              新規登録
            </button>
          {:else}
            <button
              type="button"
              class="px-2 py-1 text-[11px]"
              style="border: 1px solid var(--fg); color: var(--fg); white-space: nowrap;"
              disabled={busy}
              onclick={() => registerResult(result)}
            >
              登録
            </button>
          {/if}
        </div>
      </div>
    {/each}
  </div>
</section>
