<script lang="ts">
  import type { Edition, Release } from '../db/schema';
  import {
    createManualRelease,
    deleteRelease,
    listReleasesByEdition,
    updateRelease,
  } from '../db/store';
  import { formatReleaseDate, todayJst } from '../dates';
  import { groupEditionReleases, isPostponed, type ReleaseFormValue } from './release-view';
  import ReleaseForm from './ReleaseForm.svelte';

  let { edition }: { edition: Edition } = $props();

  let releases = $state<Release[]>([]);
  let today = $state(todayJst());
  let loaded = $state(false);
  let adding = $state(false);
  let editingId = $state<string | null>(null);
  let confirmingDeleteId = $state<string | null>(null);
  let actionError = $state<string | null>(null);

  const groups = $derived(groupEditionReleases(releases, edition.mediaType, today));
  const isEmpty = $derived(
    groups.broadcast.length + groups.upcoming.length + groups.recent.length === 0,
  );

  // 親が Edition を読み直しても id が同じなら、編集中のフォームを閉じない。
  const editionId = $derived(edition.id);

  async function load() {
    const list = await listReleasesByEdition(editionId);
    today = todayJst();
    releases = list;
    loaded = true;
  }

  $effect(() => {
    // Edition が切り替わったら読み直す
    editionId;
    loaded = false;
    adding = false;
    editingId = null;
    confirmingDeleteId = null;
    load();
  });

  async function saveNew(value: ReleaseFormValue) {
    await createManualRelease({
      editionId,
      kind: value.kind,
      label: value.label,
      date: value.date,
      precision: value.precision,
      time: value.time,
    });
    adding = false;
    await load();
  }

  async function saveEdit(release: Release, value: ReleaseFormValue) {
    await updateRelease(release.id, {
      kind: value.kind,
      label: value.label,
      date: value.date,
      precision: value.precision,
      time: value.time,
    });
    editingId = null;
    await load();
  }

  async function remove(release: Release) {
    actionError = null;
    try {
      await deleteRelease(release.id);
      confirmingDeleteId = null;
      await load();
    } catch (err) {
      actionError = err instanceof Error ? err.message : '削除できませんでした';
    }
  }

  function startAdd() {
    editingId = null;
    confirmingDeleteId = null;
    adding = true;
  }

  function startEdit(release: Release) {
    adding = false;
    confirmingDeleteId = null;
    editingId = release.id;
  }
</script>

{#snippet rows(list: Release[], accent: boolean)}
  <div class={accent ? 'hi-bar' : ''} style="border-top: 1px solid var(--line); min-width: 0;">
    {#each list as release (release.id)}
      {#if editingId === release.id}
        <div class="py-1.5 pr-1">
          <ReleaseForm
            mediaType={edition.mediaType}
            {release}
            onsubmit={(value) => saveEdit(release, value)}
            oncancel={() => (editingId = null)}
          />
        </div>
      {:else}
        <div
          class="flex flex-col gap-0.5 py-1.5"
          style="border-bottom: 1px solid var(--line); min-width: 0;"
        >
          <div class="flex flex-wrap items-center gap-x-2 gap-y-0.5">
            <span class="when">{formatReleaseDate(release)}</span>
            {#if release.kind === 'stream-start'}
              <span class="tag">配信</span>
            {/if}
            {#if isPostponed(release)}
              <span class="tag">延期</span>
            {/if}
            {#if release.origin === 'manual'}
              <span class="tag tag-strong">手動</span>
            {/if}
          </div>
          <div class="flex items-start gap-2">
            <span class="min-w-0 flex-1 text-xs" style="overflow-wrap: anywhere;">{release.label}</span>
            {#if confirmingDeleteId === release.id}
              <button
                type="button"
                class="btn btn-invert"
                aria-label={`「${release.label}」を削除する`}
                onclick={() => remove(release)}
              >
                削除する
              </button>
              <button type="button" class="btn" onclick={() => (confirmingDeleteId = null)}>やめる</button>
            {:else}
              <button
                type="button"
                class="btn"
                aria-label={`「${release.label}」を編集`}
                onclick={() => startEdit(release)}
              >
                編集
              </button>
              <button
                type="button"
                class="btn"
                aria-label={`「${release.label}」を削除`}
                onclick={() => (confirmingDeleteId = release.id)}
              >
                削除
              </button>
            {/if}
          </div>
          {#if confirmingDeleteId === release.id && release.origin !== 'manual'}
            <p class="text-[11px]" style="color: var(--muted);">同期元に残っていれば、次の同期で再取得されます</p>
          {/if}
        </div>
      {/if}
    {/each}
  </div>
{/snippet}

<div class="flex flex-col gap-1.5" style="min-width: 0;">
  <div class="flex items-center justify-between gap-2">
    <span class="type-label">リリース</span>
    {#if !adding}
      <button type="button" class="btn" onclick={startAdd}>+ リリースを追加</button>
    {/if}
  </div>

  {#if adding}
    <ReleaseForm
      mediaType={edition.mediaType}
      onsubmit={saveNew}
      oncancel={() => (adding = false)}
    />
  {/if}

  {#if actionError}
    <p class="text-[11px]" style="color: var(--fg);" role="alert">{actionError}</p>
  {/if}

  {#if groups.broadcast.length > 0}
    <div class="flex flex-col gap-1">
      <span class="type-label">今後の放送・配信</span>
      {@render rows(groups.broadcast, true)}
    </div>
  {/if}

  {#if groups.upcoming.length > 0}
    <div class="flex flex-col gap-1">
      {#if groups.broadcast.length > 0 || groups.recent.length > 0}
        <span class="type-label">予定</span>
      {/if}
      {@render rows(groups.upcoming, false)}
    </div>
  {/if}

  {#if groups.recent.length > 0}
    <div class="flex flex-col gap-1">
      <span class="type-label">直近30日</span>
      {@render rows(groups.recent, false)}
    </div>
  {/if}

  {#if loaded && isEmpty && !adding}
    <p class="text-[11px]" style="color: var(--muted);">表示するリリースはありません</p>
  {/if}
  {#if groups.hiddenCount > 0}
    <p class="text-[11px]" style="color: var(--muted);">
      30日より前の {groups.hiddenCount} 件は表示していません
    </p>
  {/if}
</div>

<style>
  .when {
    font-size: 0.6875rem;
    font-weight: 700;
    white-space: nowrap;
    font-variant-numeric: tabular-nums;
  }
  .tag {
    padding: 0 0.25rem;
    font-size: 0.625rem;
    line-height: 1.5;
    white-space: nowrap;
    border: 1px solid var(--line);
    color: var(--muted);
  }
  .tag-strong {
    border-color: var(--fg);
    color: var(--fg);
  }
  .btn {
    padding: 0.125rem 0.5rem;
    font-size: 0.6875rem;
    border: 1px solid var(--line);
    color: var(--fg);
    background: transparent;
    white-space: nowrap;
  }
  .btn-invert {
    background: var(--fg);
    color: var(--bg);
    border-color: var(--fg);
  }
</style>
