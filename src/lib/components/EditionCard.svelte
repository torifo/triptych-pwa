<script lang="ts">
  import { ArrowRightLeft, ImageIcon, Trash } from '@lucide/svelte';
  import type { Edition, Status, EditionProgress, Work } from '../db/schema';
  import {
    updateEditionStatus,
    updateEditionProgress,
    updateEdition,
    deleteEdition,
    reassignEditionWork,
  } from '../db/store';
  import Cover from './Cover.svelte';
  import MediaBadge from './MediaBadge.svelte';
  import CompletedBadge from './CompletedBadge.svelte';
  import StatusSelect from './StatusSelect.svelte';
  import ProgressFields from './ProgressFields.svelte';
  import ReleaseList from './ReleaseList.svelte';
  import SectionHead from './SectionHead.svelte';
  import SourceRefEditor from './SourceRefEditor.svelte';
  import { isAutoCover, parseCoverUrlInput, saveEditionCover } from './edition-cover';
  import { DEFAULT_PROGRESS_UNIT, MEDIA_TYPE_LABELS } from './labels';

  let {
    edition,
    otherWorks,
    onchange,
  }: {
    edition: Edition;
    otherWorks: Work[];
    onchange: () => void;
  } = $props();

  const COVER_FROM_LABELS = { anilist: 'AniList から自動', catalog: 'カタログから自動' } as const;

  const defaultUnit = $derived(DEFAULT_PROGRESS_UNIT[edition.mediaType]);
  const coverState = $derived(
    !edition.coverUrl
      ? '未設定（文字の表紙）'
      : isAutoCover(edition) && edition.syncHints?.coverFrom
        ? COVER_FROM_LABELS[edition.syncHints.coverFrom]
        : '手で設定',
  );

  // 初期値はプレースホルダ。実際の値は下の $effect が mount 時に同期する
  // （$state の初期化子でプロップを直接参照すると state_referenced_locally 警告になるため）。
  let titleDraft = $state('');
  let reassignTarget = $state('');
  let confirmingDelete = $state(false);
  let editingCover = $state(false);
  let coverDraft = $state('');
  let coverError = $state<string | null>(null);
  let coverBusy = $state(false);

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

  function startCoverEdit() {
    coverDraft = edition.coverUrl ?? '';
    coverError = null;
    editingCover = true;
  }

  async function saveCover(event: Event) {
    event.preventDefault();
    if (coverBusy) return;
    const parsed = parseCoverUrlInput(coverDraft);
    if (!parsed.ok) {
      coverError = parsed.message;
      return;
    }
    // 手で設定済みの表紙と同じなら書かない（自動の表紙を同じ URL で保存した時は、手で固定する）
    if (parsed.url === edition.coverUrl && !edition.syncHints?.coverFrom) {
      editingCover = false;
      return;
    }
    coverBusy = true;
    try {
      await saveEditionCover(edition.id, parsed.url);
      editingCover = false;
      onchange();
    } catch (err) {
      coverError = err instanceof Error ? err.message : '保存できませんでした';
    } finally {
      coverBusy = false;
    }
  }
</script>

<article class="edition" aria-label={`${MEDIA_TYPE_LABELS[edition.mediaType]} ${edition.title}`}>
  <div class="ed-top">
    <div class="ed-head">
      <div class="badges">
        <MediaBadge mediaType={edition.mediaType} />
        {#if edition.completed}
          <CompletedBadge />
        {/if}
      </div>
      <StatusSelect value={edition.status} onchange={onStatusChange} />
    </div>

    <input
      class="ed-title"
      aria-label="Edition タイトル"
      bind:value={titleDraft}
      onblur={saveTitle}
      onkeydown={(e) => {
        if (e.key === 'Enter') (e.currentTarget as HTMLInputElement).blur();
      }}
    />
    <div class="ed-progress">
      <span class="type-label">進捗</span>
      <ProgressFields progress={edition.progress} {defaultUnit} onsave={onProgressSave} />
    </div>
  </div>

  <ReleaseList {edition} {onchange} />

  <SourceRefEditor {edition} {onchange} />

  <section class="manage">
    <SectionHead title="表紙と管理" kicker="EDIT" />

    <div class="cover-row">
      <Cover src={edition.coverUrl} title={edition.title} mediaType={edition.mediaType} size="s" />
      <div class="cover-edit">
        <span class="type-label">表紙</span>
        {#if editingCover}
          <form class="cover-form" onsubmit={saveCover}>
            <input
              class="field"
              type="url"
              inputmode="url"
              placeholder="https://…"
              aria-label="表紙の URL"
              bind:value={coverDraft}
            />
            <button type="submit" class="btn btn-main" disabled={coverBusy}>保存</button>
            <button type="button" class="btn" onclick={() => (editingCover = false)}>やめる</button>
          </form>
          {#if coverError}
            <p class="note" style="color: var(--fg);" role="alert">{coverError}</p>
          {/if}
          <p class="note">
            空にして保存すると表紙を外します{edition.mediaType === 'anime' ? '（次の同期で AniList の表紙が入ることがあります）' : ''}
          </p>
        {:else}
          <p class="note">{coverState}</p>
          <button type="button" class="btn self-start" onclick={startCoverEdit}>
            <ImageIcon size={14} />表紙の URL を変える
          </button>
        {/if}
      </div>
    </div>

    {#if otherWorks.length > 0}
      <div class="reassign">
        <span class="reassign-label"><ArrowRightLeft size={14} />付け替え</span>
        <select class="field" aria-label="付け替え先の作品" bind:value={reassignTarget}>
          <option value="">作品を選択</option>
          {#each otherWorks as w (w.id)}
            <option value={w.id}>{w.title}</option>
          {/each}
        </select>
        <button type="button" class="btn" disabled={!reassignTarget} onclick={onReassign}>移動</button>
      </div>
    {/if}

    <div class="delete-row">
      <button type="button" class="btn" class:btn-invert={confirmingDelete} onclick={onDelete}>
        <Trash size={14} />{confirmingDelete ? '本当に削除する' : 'この Edition を削除'}
      </button>
      {#if confirmingDelete}
        <button type="button" class="btn" onclick={() => (confirmingDelete = false)}>やめる</button>
      {/if}
    </div>
  </section>
</article>

<style>
  .edition {
    display: flex;
    flex-direction: column;
    gap: 1.75rem;
    min-width: 0;
  }
  .ed-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.5rem;
    min-width: 0;
  }
  .badges {
    display: flex;
    align-items: center;
    gap: 0.375rem;
    min-width: 0;
  }
  .ed-top {
    display: flex;
    flex-direction: column;
    gap: 0.6rem;
    min-width: 0;
  }
  .ed-title {
    width: 100%;
    min-width: 0;
    padding: 0.35rem 0;
    border: 0;
    border-bottom: 1px solid var(--line);
    border-radius: 0;
    background: transparent;
    color: var(--fg);
    font-size: 1rem;
    font-weight: 700;
    line-height: 1.35;
  }
  .ed-progress {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    min-width: 0;
  }

  .manage {
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
    min-width: 0;
  }
  .cover-row {
    display: flex;
    align-items: flex-start;
    gap: 0.75rem;
    min-width: 0;
  }
  .cover-edit {
    display: flex;
    flex: 1;
    flex-direction: column;
    gap: 0.35rem;
    min-width: 0;
  }
  .cover-form {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.375rem;
    min-width: 0;
  }
  .cover-form .field {
    flex: 1 1 10rem;
  }
  .note {
    margin: 0;
    font-size: 0.6875rem;
    color: var(--muted);
    overflow-wrap: anywhere;
  }
  .reassign {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.375rem;
    font-size: 0.6875rem;
    color: var(--muted);
    min-width: 0;
  }
  .reassign-label {
    display: inline-flex;
    align-items: center;
    gap: 0.25rem;
    white-space: nowrap;
  }
  .reassign .field {
    flex: 1 1 8rem;
  }
  .delete-row {
    display: flex;
    flex-wrap: wrap;
    gap: 0.375rem;
  }

  .field {
    min-width: 0;
    padding: 0.25rem 0.375rem;
    min-height: 1.75rem;
    font-size: 0.75rem;
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
    padding: 0.125rem 0.5rem;
    min-height: 1.75rem;
    font-size: 0.6875rem;
    border: 1px solid var(--line);
    color: var(--fg);
    background: transparent;
    white-space: nowrap;
  }
  .btn:disabled {
    color: var(--muted);
  }
  .btn-main {
    border-color: var(--fg);
  }
  .btn-invert {
    background: var(--fg);
    color: var(--bg);
    border-color: var(--fg);
  }
</style>
