<script lang="ts">
  import { getWork, listEditionsByWork, listWorks, createEdition, updateWork } from '../lib/db/store';
  import type { Work, Edition, MediaType } from '../lib/db/schema';
  import { navigate } from '../lib/router.svelte';
  import EditionCard from '../lib/components/EditionCard.svelte';
  import { MEDIA_TYPE_LABELS } from '../lib/components/labels';

  let { id }: { id: string } = $props();

  let work = $state<Work | undefined>(undefined);
  let editions = $state<Edition[]>([]);
  let allWorks = $state<Work[]>([]);
  let loaded = $state(false);
  let titleDraft = $state('');

  interface Column {
    key: string;
    label: string;
    mediaTypes: MediaType[];
  }

  const columns: Column[] = [
    { key: 'source', label: '原作', mediaTypes: ['web-novel', 'light-novel'] },
    { key: 'manga', label: '漫画', mediaTypes: ['manga'] },
    { key: 'anime', label: 'アニメ', mediaTypes: ['anime'] },
  ];

  let addFormOpen = $state<Record<string, boolean>>({});
  let addFormTitle = $state<Record<string, string>>({});
  let addFormMediaType = $state<Record<string, MediaType>>({});

  const otherWorks = $derived(allWorks.filter((w) => w.id !== id));

  async function refresh() {
    work = await getWork(id);
    editions = await listEditionsByWork(id);
    allWorks = await listWorks();
    if (work) titleDraft = work.title;
  }

  $effect(() => {
    id;
    loaded = false;
    refresh().then(() => {
      loaded = true;
    });
  });

  async function saveTitle() {
    if (!work) return;
    const trimmed = titleDraft.trim();
    if (!trimmed || trimmed === work.title) {
      titleDraft = work.title;
      return;
    }
    await updateWork(work.id, { title: trimmed });
    await refresh();
  }

  function editionsFor(column: Column): Edition[] {
    return editions.filter((e) => column.mediaTypes.includes(e.mediaType));
  }

  function toggleAddForm(column: Column) {
    addFormOpen[column.key] = !addFormOpen[column.key];
    if (addFormOpen[column.key] && !addFormMediaType[column.key]) {
      addFormMediaType[column.key] = column.mediaTypes[0];
    }
  }

  async function onAddEdition(column: Column) {
    const title = (addFormTitle[column.key] ?? '').trim();
    if (!title) return;
    const mediaType = addFormMediaType[column.key] ?? column.mediaTypes[0];
    await createEdition({ workId: id, mediaType, title });
    addFormTitle[column.key] = '';
    addFormOpen[column.key] = false;
    await refresh();
  }
</script>

<section class="flex flex-col gap-4 p-4">
  {#if loaded && !work}
    <p class="text-sm" style="color: var(--muted);">作品が見つかりません</p>
    <button
      type="button"
      class="self-start px-2 py-1 text-xs"
      style="border: 1px solid var(--fg); color: var(--fg); white-space: nowrap;"
      onclick={() => navigate('#/library')}
    >
      Library へ戻る
    </button>
  {:else if work}
    <div class="flex flex-col gap-1">
      <input
        class="w-full px-0 py-1.5 font-bold"
        style="border: 0; border-bottom: 1px solid var(--line); background: var(--bg); color: var(--fg); font-size: 1.5rem; letter-spacing: -0.02em; line-height: 1.2; outline: none;"
        aria-label="作品タイトル"
        bind:value={titleDraft}
        onblur={saveTitle}
        onkeydown={(e) => {
          if (e.key === 'Enter') (e.currentTarget as HTMLInputElement).blur();
        }}
      />
    </div>

    <div class="grid grid-cols-1 gap-4 md:grid-cols-3">
      {#each columns as column (column.key)}
        <div class="flex flex-col gap-2" style="min-width: 0;">
          <div class="shou">
            <h2 style="white-space: nowrap;">{column.label}</h2>
            <span class="no">{column.key === 'source' ? 'ORIGIN' : column.key === 'manga' ? 'COMIC' : 'ANIME'}</span>
          </div>

          <div class="flex flex-col gap-2">
            {#each editionsFor(column) as edition (edition.id)}
              <EditionCard {edition} {otherWorks} onchange={refresh} />
            {/each}
          </div>

          {#if addFormOpen[column.key]}
            <div class="flex flex-col gap-1.5 p-2" style="border: 1px solid var(--line);">
              {#if column.mediaTypes.length > 1}
                <select
                  class="px-1.5 py-1 text-xs"
                  style="border: 1px solid var(--line); background: var(--bg); color: var(--fg);"
                  bind:value={addFormMediaType[column.key]}
                >
                  {#each column.mediaTypes as mt (mt)}
                    <option value={mt}>{MEDIA_TYPE_LABELS[mt]}</option>
                  {/each}
                </select>
              {/if}
              <input
                class="px-1.5 py-1 text-sm"
                style="border: 1px solid var(--line); background: var(--bg); color: var(--fg);"
                placeholder="Edition タイトル"
                bind:value={addFormTitle[column.key]}
                onkeydown={(e) => {
                  if (e.key === 'Enter') onAddEdition(column);
                }}
              />
              <button
                type="button"
                class="self-start px-2 py-1 text-xs"
                style="border: 1px solid var(--fg); color: var(--fg); white-space: nowrap;"
                onclick={() => onAddEdition(column)}
              >
                追加
              </button>
            </div>
          {:else}
            <button
              type="button"
              class="px-2 py-1 text-xs"
              style="border: 1px solid var(--line); color: var(--muted); white-space: nowrap;"
              onclick={() => toggleAddForm(column)}
            >
              + Edition追加
            </button>
          {/if}
        </div>
      {/each}
    </div>
  {/if}
</section>
