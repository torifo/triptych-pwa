<script lang="ts">
  import { onMount } from 'svelte';
  import { listReleases, listEditions, listWorks, updateRelease } from '../lib/db/store';
  import type { Release, Edition, Work } from '../lib/db/schema';
  import { navigate } from '../lib/router.svelte';
  import { runSync } from '../lib/sync/engine';
  import { syncStatus, beginSync, completeSync, failSync } from '../lib/sync/status.svelte';
  import MediaBadge from '../lib/components/MediaBadge.svelte';

  const TRACKED_STATUSES: Edition['status'][] = ['plan', 'active', 'paused'];
  const UPCOMING_DAYS = 7;

  let releases = $state<Release[]>([]);
  let editions = $state<Edition[]>([]);
  let works = $state<Work[]>([]);

  const editionMap = $derived(new Map(editions.map((e) => [e.id, e])));
  const workMap = $derived(new Map(works.map((w) => [w.id, w])));

  function todayStr(): string {
    return new Date().toISOString().slice(0, 10);
  }

  function addDaysStr(base: string, days: number): string {
    const d = new Date(`${base}T00:00:00`);
    d.setDate(d.getDate() + days);
    return d.toISOString().slice(0, 10);
  }

  const upcoming = $derived(
    (() => {
      const today = todayStr();
      const limit = addDaysStr(today, UPCOMING_DAYS);
      return releases
        .filter((r) => {
          const edition = editionMap.get(r.editionId);
          if (!edition || !TRACKED_STATUSES.includes(edition.status)) return false;
          return r.date >= today && r.date <= limit;
        })
        .sort((a, b) => a.date.localeCompare(b.date));
    })(),
  );

  const fresh = $derived(
    releases
      .filter((r) => !r.seenAt)
      .sort((a, b) => a.date.localeCompare(b.date)),
  );

  async function refresh() {
    releases = await listReleases();
    editions = await listEditions();
    works = await listWorks();
  }

  onMount(refresh);

  function workTitleFor(editionId: string): string {
    const edition = editionMap.get(editionId);
    if (!edition) return '(不明な作品)';
    return workMap.get(edition.workId)?.title ?? '(不明な作品)';
  }

  async function openRelease(release: Release, markSeen: boolean) {
    if (markSeen) {
      await updateRelease(release.id, { seenAt: new Date().toISOString() });
      await refresh();
    }
    const edition = editionMap.get(release.editionId);
    if (edition) navigate(`#/work/${edition.workId}`);
  }

  function formatDate(date: string): string {
    const [, m, d] = date.split('-');
    return `${m}/${d}`;
  }

  function formatSyncTime(iso: string | null): string {
    if (!iso) return '未実行';
    const d = new Date(iso);
    return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  }

  let syncing = $state(false);

  async function onManualSync() {
    if (syncing) return;
    syncing = true;
    beginSync();
    try {
      const result = await runSync({ force: true });
      completeSync(new Date().toISOString(), result.errors);
      await refresh();
    } catch (err) {
      failSync({});
    } finally {
      syncing = false;
    }
  }

  const errorSources = $derived(Object.keys(syncStatus.errors));
</script>

<section class="flex flex-col gap-4 p-4">
  <h1 class="text-lg font-bold">Home</h1>

  <div
    class="flex flex-wrap items-center gap-2 px-2 py-1.5 text-[11px]"
    style="border: 1px solid var(--line); color: var(--muted);"
  >
    <span style="white-space: nowrap;">最終同期: {formatSyncTime(syncStatus.lastSyncAt)}</span>
    {#if errorSources.length > 0}
      <span style="white-space: nowrap; color: var(--fg);">
        エラー: {errorSources.join(', ')}
      </span>
    {/if}
    <button
      type="button"
      class="ml-auto px-2 py-0.5 text-[11px]"
      style="border: 1px solid var(--fg); color: var(--fg); white-space: nowrap;"
      disabled={syncing || syncStatus.running}
      onclick={onManualSync}
    >
      {syncing || syncStatus.running ? '同期中…' : '今すぐ同期'}
    </button>
  </div>

  <div class="flex flex-col gap-2">
    <h2 class="text-xs font-bold tracking-wide" style="color: var(--muted);">直近</h2>
    {#if upcoming.length === 0}
      <p class="text-xs" style="color: var(--muted);">7日以内の予定はありません</p>
    {:else}
      <div class="flex flex-col gap-1.5">
        {#each upcoming as release (release.id)}
          {@const edition = editionMap.get(release.editionId)}
          <button
            type="button"
            class="flex items-center gap-2 px-2 py-1.5 text-left"
            style="border: 1px solid var(--hi); background: var(--hi-soft); min-width: 0;"
            onclick={() => openRelease(release, false)}
          >
            <span class="text-[11px] font-bold" style="white-space: nowrap;">{formatDate(release.date)}</span>
            {#if edition}
              <MediaBadge mediaType={edition.mediaType} />
            {/if}
            <span class="min-w-0 flex-1 truncate text-sm">{workTitleFor(release.editionId)}</span>
            <span class="truncate text-[11px]" style="color: var(--muted); max-width: 30%;">{release.label}</span>
          </button>
        {/each}
      </div>
    {/if}
  </div>

  <div class="flex flex-col gap-2">
    <h2 class="text-xs font-bold tracking-wide" style="color: var(--muted);">新着</h2>
    {#if fresh.length === 0}
      <p class="text-xs" style="color: var(--muted);">新着はありません</p>
    {:else}
      <div class="flex flex-col" style="border-top: 1px solid var(--line);">
        {#each fresh as release (release.id)}
          {@const edition = editionMap.get(release.editionId)}
          <button
            type="button"
            class="flex items-center gap-2 py-2 text-left"
            style="border-bottom: 1px solid var(--line); min-width: 0;"
            onclick={() => openRelease(release, true)}
          >
            <span class="text-[11px]" style="color: var(--muted); white-space: nowrap;">{formatDate(release.date)}</span>
            {#if edition}
              <MediaBadge mediaType={edition.mediaType} />
            {/if}
            <span class="min-w-0 flex-1 truncate text-sm">{workTitleFor(release.editionId)}</span>
            <span class="truncate text-[11px]" style="color: var(--muted); max-width: 30%;">{release.label}</span>
          </button>
        {/each}
      </div>
    {/if}
  </div>
</section>
