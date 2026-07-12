<script lang="ts">
  import type { Edition, Status, EditionProgress, Work } from '../db/schema';
  import {
    updateEditionStatus,
    updateEditionProgress,
    updateEdition,
    deleteEdition,
    reassignEditionWork,
  } from '../db/store';
  import MediaBadge from './MediaBadge.svelte';
  import StatusSelect from './StatusSelect.svelte';
  import ProgressFields from './ProgressFields.svelte';

  let {
    edition,
    otherWorks,
    onchange,
  }: {
    edition: Edition;
    otherWorks: Work[];
    onchange: () => void;
  } = $props();

  let titleDraft = $state(edition.title);
  let reassignTarget = $state('');
  let confirmingDelete = $state(false);

  $effect(() => {
    titleDraft = edition.title;
  });

  async function onStatusChange(next: Status) {
    await updateEditionStatus(edition.id, next);
    onchange();
  }

  async function onProgressSave(next: EditionProgress) {
    await updateEditionProgress(edition.id, next);
    onchange();
  }

  async function saveTitle() {
    const trimmed = titleDraft.trim();
    if (!trimmed || trimmed === edition.title) {
      titleDraft = edition.title;
      return;
    }
    await updateEdition(edition.id, { title: trimmed });
    onchange();
  }

  async function onDelete() {
    if (!confirmingDelete) {
      confirmingDelete = true;
      return;
    }
    await deleteEdition(edition.id);
    onchange();
  }

  async function onReassign() {
    if (!reassignTarget) return;
    await reassignEditionWork(edition.id, reassignTarget);
    reassignTarget = '';
    onchange();
  }
</script>

<div class="flex flex-col gap-2 p-2" style="border: 1px solid var(--line);">
  <div class="flex items-center justify-between gap-2">
    <MediaBadge mediaType={edition.mediaType} />
    <StatusSelect value={edition.status} onchange={onStatusChange} />
  </div>

  <input
    class="w-full px-1.5 py-1 text-sm"
    style="border: 1px solid var(--line); background: var(--bg); color: var(--fg); min-width: 0;"
    bind:value={titleDraft}
    onblur={saveTitle}
    onkeydown={(e) => {
      if (e.key === 'Enter') (e.currentTarget as HTMLInputElement).blur();
    }}
  />

  <ProgressFields progress={edition.progress} onsave={onProgressSave} />

  {#if otherWorks.length > 0}
    <div class="flex flex-wrap items-center gap-1 text-[11px]" style="color: var(--muted);">
      <span style="white-space: nowrap;">付け替え:</span>
      <select
        class="min-w-0 flex-1 px-1 py-1 text-[11px]"
        style="border: 1px solid var(--line); background: var(--bg); color: var(--fg);"
        bind:value={reassignTarget}
      >
        <option value="">作品を選択</option>
        {#each otherWorks as w (w.id)}
          <option value={w.id}>{w.title}</option>
        {/each}
      </select>
      <button
        type="button"
        class="px-2 py-1 text-[11px]"
        style="border: 1px solid var(--line); color: var(--fg); white-space: nowrap;"
        disabled={!reassignTarget}
        onclick={onReassign}
      >
        移動
      </button>
    </div>
  {/if}

  <button
    type="button"
    class="self-start px-2 py-1 text-[11px]"
    style={`border: 1px solid var(--line); white-space: nowrap; color: ${confirmingDelete ? 'var(--bg)' : 'var(--muted)'}; background: ${confirmingDelete ? 'var(--fg)' : 'transparent'};`}
    onclick={onDelete}
  >
    {confirmingDelete ? '本当に削除する' : '削除'}
  </button>
</div>
