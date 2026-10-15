<script lang="ts">
  import { onMount } from 'svelte';
  import { listEditions, listReleases, listWorks, createWork } from '../lib/db/store';
  import type { Edition, Release, Work, MediaType, Status } from '../lib/db/schema';
  import { navigate } from '../lib/router.svelte';
  import MediaBadge from '../lib/components/MediaBadge.svelte';
  import { MEDIA_TYPE_LABELS, MEDIA_TYPES, STATUS_LABELS, STATUSES, PROGRESS_UNIT_LABELS } from '../lib/components/labels';
  import {
    editionDisambiguators,
    seasonStartByEdition,
    sortLibraryEditions,
  } from '../lib/components/library-view';

  let editions = $state<Edition[]>([]);
  let works = $state<Work[]>([]);
  let releases = $state<Release[]>([]);
  let statusFilter = $state<Status | 'all'>('all');
  let mediaTypeFilter = $state<MediaType | 'all'>('all');
  let showCreate = $state(false);
  let newTitle = $state('');
  let creating = $state(false);

  const workMap = $derived(new Map(works.map((w) => [w.id, w])));
  const workTitleOf = (workId: string) => workMap.get(workId)?.title ?? '';

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
    editions = await listEditions();
    works = await listWorks();
    releases = await listReleases();
  }

  onMount(refresh);

  async function onCreateWork() {
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

<section class="flex flex-col gap-3 p-4">
  <div class="flex items-center justify-between gap-2">
    <h1 class="page-title">Library<span class="type-label" style="margin-left: 0.8em;">追跡中の作品</span></h1>
    <button
      type="button"
      class="px-2 py-1 text-xs"
      style="border: 1px solid var(--fg); color: var(--fg); white-space: nowrap;"
      onclick={() => (showCreate = !showCreate)}
    >
      + 新規作品
    </button>
  </div>

  {#if showCreate}
    <div class="flex items-center gap-2 p-2" style="border: 1px solid var(--line);">
      <input
        class="min-w-0 flex-1 px-1.5 py-1 text-sm"
        style="border: 1px solid var(--line); background: var(--bg); color: var(--fg);"
        placeholder="作品タイトル"
        aria-label="作品タイトル"
        bind:value={newTitle}
        onkeydown={(e) => {
          if (e.key === 'Enter') onCreateWork();
        }}
      />
      <button
        type="button"
        class="px-2 py-1 text-xs"
        style="border: 1px solid var(--fg); color: var(--fg); white-space: nowrap;"
        disabled={!newTitle.trim() || creating}
        onclick={onCreateWork}
      >
        作成
      </button>
    </div>
  {/if}

  <div class="flex flex-col gap-1.5">
    <div class="flex flex-wrap gap-1 overflow-x-auto">
      <button
        type="button"
        class="px-2 py-1 text-[11px]"
        style={`border: 1px solid var(--line); white-space: nowrap; color: ${statusFilter === 'all' ? 'var(--bg)' : 'var(--fg)'}; background: ${statusFilter === 'all' ? 'var(--fg)' : 'transparent'};`}
        onclick={() => (statusFilter = 'all')}
      >
        すべて
      </button>
      {#each STATUSES as s (s)}
        <button
          type="button"
          class="px-2 py-1 text-[11px]"
          style={`border: 1px solid var(--line); white-space: nowrap; color: ${statusFilter === s ? 'var(--bg)' : 'var(--fg)'}; background: ${statusFilter === s ? 'var(--fg)' : 'transparent'};`}
          onclick={() => (statusFilter = s)}
        >
          {STATUS_LABELS[s]}
        </button>
      {/each}
    </div>

    <div class="flex flex-wrap gap-1 overflow-x-auto">
      <button
        type="button"
        class="px-2 py-1 text-[11px]"
        style={`border: 1px solid var(--line); white-space: nowrap; color: ${mediaTypeFilter === 'all' ? 'var(--bg)' : 'var(--fg)'}; background: ${mediaTypeFilter === 'all' ? 'var(--fg)' : 'transparent'};`}
        onclick={() => (mediaTypeFilter = 'all')}
      >
        全メディア
      </button>
      {#each MEDIA_TYPES as m (m)}
        <button
          type="button"
          class="px-2 py-1 text-[11px]"
          style={`border: 1px solid var(--line); white-space: nowrap; color: ${mediaTypeFilter === m ? 'var(--bg)' : 'var(--fg)'}; background: ${mediaTypeFilter === m ? 'var(--fg)' : 'transparent'};`}
          onclick={() => (mediaTypeFilter = m)}
        >
          {MEDIA_TYPE_LABELS[m]}
        </button>
      {/each}
    </div>
  </div>

  <div class="flex flex-col" style="border-top: 1px solid var(--line);">
    {#if filtered.length === 0}
      <p class="py-6 text-center text-xs" style="color: var(--muted);">該当する Edition がありません</p>
    {/if}
    {#each filtered as edition (edition.id)}
      <button
        type="button"
        class="row-hover flex items-center gap-2 py-2 text-left"
        style="border-bottom: 1px solid var(--line); min-width: 0;"
        onclick={() => navigate(`#/work/${edition.workId}`)}
      >
        <MediaBadge mediaType={edition.mediaType} />
        <span class="flex min-w-0 flex-1 flex-col">
          <span class="truncate text-sm">
            {workMap.get(edition.workId)?.title ?? '(不明な作品)'}
          </span>
          {#if subtitles.get(edition.id)}
            <span class="truncate text-[11px]" style="color: var(--muted);">{subtitles.get(edition.id)}</span>
          {/if}
        </span>
        <span class="text-[11px]" style="color: var(--muted); white-space: nowrap;">
          {STATUS_LABELS[edition.status]}
        </span>
        {#if edition.progress}
          <span class="text-[11px]" style="color: var(--muted); white-space: nowrap;">
            {edition.progress.current}{#if edition.progress.total}/{edition.progress.total}{/if}{PROGRESS_UNIT_LABELS[edition.progress.unit]}
          </span>
        {/if}
      </button>
    {/each}
  </div>
</section>
