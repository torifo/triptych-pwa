<script lang="ts">
  import { Pencil, Plus } from '@lucide/svelte';
  import {
    createEdition,
    getWork,
    listEditionsByWork,
    listReleasesByEdition,
    listWorks,
    updateWork,
  } from '../lib/db/store';
  import type { Edition, MediaType, Release, Work } from '../lib/db/schema';
  import { navigate, replaceQuery, route } from '../lib/router.svelte';
  import CoverHero from '../lib/components/CoverHero.svelte';
  import EditionCard from '../lib/components/EditionCard.svelte';
  import MediaBadge from '../lib/components/MediaBadge.svelte';
  import MonoTime from '../lib/components/MonoTime.svelte';
  import OnAirLamp from '../lib/components/OnAirLamp.svelte';
  import SectionHead from '../lib/components/SectionHead.svelte';
  import { workCover } from '../lib/components/cover';
  import { MEDIA_TYPE_LABELS, MEDIA_TYPES } from '../lib/components/labels';
  import { seasonStartByEdition } from '../lib/components/library-view';
  import { broadcastDayJst, onAirEditionIds } from '../lib/components/onair';
  import {
    editionTabs,
    firstAiringTimeToday,
    initialEditionId,
    orderWorkEditions,
    suggestedMediaType,
    workMediaTypes,
  } from '../lib/components/work-detail-view';

  let { id }: { id: string } = $props();

  let work = $state<Work | undefined>(undefined);
  let editions = $state<Edition[]>([]);
  let releases = $state<Release[]>([]);
  let allWorks = $state<Work[]>([]);
  let loaded = $state(false);
  let today = $state(broadcastDayJst());
  /** 開いているタブ。消えた Edition を指していたら先頭などに戻す（selectedId）。 */
  let chosenId = $state<string | undefined>(undefined);

  let editingTitle = $state(false);
  let titleDraft = $state('');

  let addOpen = $state(false);
  let addTitle = $state('');
  let addMediaType = $state<MediaType>('web-novel');
  let adding = $state(false);

  const otherWorks = $derived(allWorks.filter((w) => w.id !== id));
  const seasonStarts = $derived(seasonStartByEdition(releases));
  const ordered = $derived(orderWorkEditions(editions, seasonStarts));
  const tabs = $derived(work ? editionTabs(ordered, work.title, seasonStarts) : []);
  const hasTabs = $derived(tabs.length >= 2);
  const onAirIds = $derived(onAirEditionIds(releases, new Map(editions.map((e) => [e.id, e])), today));
  const airingTime = $derived(firstAiringTimeToday(releases, onAirIds, today));
  const cover = $derived(work ? workCover(work, editions) : undefined);
  const mediaTypes = $derived(workMediaTypes(editions));
  const selectedId = $derived(
    chosenId && ordered.some((e) => e.id === chosenId) ? chosenId : initialEditionId(ordered, undefined, onAirIds),
  );
  const selected = $derived(ordered.find((e) => e.id === selectedId));

  async function refresh() {
    const [nextWork, nextEditions, works] = await Promise.all([getWork(id), listEditionsByWork(id), listWorks()]);
    const nextReleases = (await Promise.all(nextEditions.map((e) => listReleasesByEdition(e.id)))).flat();
    // まとめて入れて、途中の状態（Release 抜きの並び・タブ）を描かない
    work = nextWork;
    editions = nextEditions;
    releases = nextReleases;
    allWorks = works;
    today = broadcastDayJst();
  }

  $effect(() => {
    id;
    loaded = false;
    chosenId = undefined;
    editingTitle = false;
    addOpen = false;
    refresh().then(() => {
      // URL で指定された Edition（Library の棚から開いた時）→ 今日放送のある Edition → 先頭
      chosenId = initialEditionId(ordered, route.query.edition, onAirIds);
      loaded = true;
    });
  });

  const tabDomId = (editionId: string) => `edition-tab-${editionId}`;

  function selectTab(editionId: string) {
    chosenId = editionId;
    // 戻ってきた時に同じタブを開けるよう URL に残す（履歴は増やさない）
    replaceQuery({ ...route.query, edition: editionId });
  }

  const TAB_KEYS = ['ArrowRight', 'ArrowLeft', 'Home', 'End'];

  function onTabKeydown(event: KeyboardEvent, index: number) {
    if (!TAB_KEYS.includes(event.key)) return;
    event.preventDefault();
    const last = tabs.length - 1;
    const next =
      event.key === 'Home'
        ? 0
        : event.key === 'End'
          ? last
          : (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
    const tab = tabs[next];
    if (!tab) return;
    selectTab(tab.id);
    const el = document.getElementById(tabDomId(tab.id));
    el?.focus();
    el?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  }

  function focusOnMount(node: HTMLElement) {
    node.focus();
  }

  function startTitleEdit() {
    if (!work) return;
    titleDraft = work.title;
    editingTitle = true;
  }

  async function saveTitle(event: Event) {
    event.preventDefault();
    if (!work) return;
    const trimmed = titleDraft.trim();
    if (trimmed && trimmed !== work.title) {
      await updateWork(work.id, { title: trimmed });
      await refresh();
    }
    editingTitle = false;
  }

  function openAdd() {
    addMediaType = suggestedMediaType(editions);
    addTitle = work?.title ?? '';
    addOpen = true;
  }

  async function onAddEdition(event: Event) {
    event.preventDefault();
    const title = addTitle.trim();
    if (!title || adding) return;
    adding = true;
    try {
      const created = await createEdition({ workId: id, mediaType: addMediaType, title });
      addTitle = '';
      addOpen = false;
      await refresh();
      selectTab(created.id);
    } finally {
      adding = false;
    }
  }
</script>

{#if loaded && !work}
  <section class="flex flex-col gap-4 p-4">
    <p class="text-sm" style="color: var(--muted);">作品が見つかりません</p>
    <button
      type="button"
      class="self-start px-2 py-1 text-xs"
      style="border: 1px solid var(--fg); color: var(--fg); white-space: nowrap;"
      onclick={() => navigate('#/library')}
    >
      Library へ戻る
    </button>
  </section>
{:else if work}
  <div class="work">
    <CoverHero src={cover?.url} title={work.title} mediaType={cover?.mediaType} onAir={onAirIds.size > 0}>
      {#if mediaTypes.length > 0}
        <div class="hero-media" aria-label="媒体">
          {#each mediaTypes as mediaType (mediaType)}
            <MediaBadge {mediaType} />
          {/each}
        </div>
      {/if}
      {#if onAirIds.size > 0}
        <div class="hero-onair">
          <OnAirLamp label="ON AIR" />
          {#if airingTime}
            <MonoTime text={`今日 ${airingTime}`} muted />
          {/if}
        </div>
      {/if}
      {#if !editingTitle}
        <button type="button" class="hero-edit" onclick={startTitleEdit}>
          <Pencil size={13} />題名を編集
        </button>
      {/if}
    </CoverHero>

    {#if editingTitle}
      <form class="title-edit" onsubmit={saveTitle}>
        <input
          class="field title-field"
          aria-label="作品タイトル"
          bind:value={titleDraft}
          {@attach focusOnMount}
          onkeydown={(e) => {
            if (e.key === 'Escape') editingTitle = false;
          }}
        />
        <div class="title-actions">
          <button type="submit" class="btn btn-main">保存</button>
          <button type="button" class="btn" onclick={() => (editingTitle = false)}>やめる</button>
        </div>
      </form>
    {/if}

    <div class="body">
      {#if hasTabs}
        <div class="tabs" role="tablist" aria-label="Edition">
          {#each tabs as tab, i (tab.id)}
            {@const active = tab.id === selectedId}
            <button
              type="button"
              role="tab"
              id={tabDomId(tab.id)}
              class="tab"
              class:active
              aria-selected={active}
              aria-controls="edition-panel"
              tabindex={active ? 0 : -1}
              onclick={() => selectTab(tab.id)}
              onkeydown={(e) => onTabKeydown(e, i)}
            >
              <span class="mono tab-media">
                {tab.media}
                {#if onAirIds.has(tab.id)}<OnAirLamp />{/if}
              </span>
              <span class="tab-sub">{[tab.detail, tab.status].filter(Boolean).join(' · ')}</span>
            </button>
          {/each}
        </div>
      {/if}

      {#if selected}
        <div
          id="edition-panel"
          class="panel"
          role={hasTabs ? 'tabpanel' : undefined}
          aria-labelledby={hasTabs ? tabDomId(selected.id) : undefined}
        >
          {#key selected.id}
            <EditionCard edition={selected} {otherWorks} onchange={refresh} />
          {/key}
        </div>
      {:else if loaded}
        <p class="empty">Edition がまだありません。原作・漫画・アニメを追加すると、ここで追えます。</p>
      {/if}

      <section class="add">
        {#if addOpen}
          <form class="add-form" onsubmit={onAddEdition}>
            <SectionHead title="Edition を追加" kicker="NEW" />
            <div class="add-fields">
              <select class="field add-media" aria-label="メディア種別" bind:value={addMediaType}>
                {#each MEDIA_TYPES as mt (mt)}
                  <option value={mt}>{MEDIA_TYPE_LABELS[mt]}</option>
                {/each}
              </select>
              <input
                class="field add-title"
                placeholder="Edition タイトル"
                aria-label="追加する Edition のタイトル"
                bind:value={addTitle}
              />
            </div>
            <div class="add-actions">
              <button type="submit" class="btn btn-main" disabled={!addTitle.trim() || adding}>追加</button>
              <button type="button" class="btn" onclick={() => (addOpen = false)}>やめる</button>
            </div>
          </form>
        {:else}
          <button type="button" class="btn add-open" onclick={openAdd}>
            <Plus size={14} />Edition を追加
          </button>
        {/if}
      </section>
    </div>
  </div>
{/if}

<style>
  .work {
    display: flex;
    flex-direction: column;
    min-width: 0;
  }

  /* ─── ヘッダー（CoverHero の中身） ─── */
  .hero-media {
    display: flex;
    flex-wrap: wrap;
    gap: 0.25rem;
  }
  .hero-onair {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.5rem;
    font-size: 0.6875rem;
  }
  .hero-edit {
    align-self: flex-start;
    display: inline-flex;
    align-items: center;
    gap: 0.25rem;
    min-height: 1.75rem;
    padding: 0 0.125rem;
    font-size: 0.6875rem;
    color: var(--muted);
    background: transparent;
    text-decoration: underline;
    text-underline-offset: 3px;
    white-space: nowrap;
  }
  .hero-edit:hover {
    color: var(--fg);
  }

  .title-edit {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.5rem;
    padding: 0.75rem 1rem;
    border-bottom: 1px solid var(--line);
    min-width: 0;
  }
  .title-field {
    flex: 1 1 12rem;
    font-size: 0.9375rem;
    font-weight: 700;
  }
  .title-actions {
    display: flex;
    gap: 0.375rem;
  }

  /* ─── 本文 ─── */
  .body {
    display: flex;
    flex-direction: column;
    gap: 1.25rem;
    padding: 0 1rem 2rem;
    min-width: 0;
  }
  .panel {
    min-width: 0;
  }
  .body > :first-child:not(.tabs) {
    margin-top: 1.25rem;
  }
  .empty {
    margin: 0;
    font-size: 0.75rem;
    color: var(--muted);
  }

  /* ─── Edition のタブ ─── */
  .tabs {
    display: flex;
    margin: 0 -1rem;
    padding: 0 1rem;
    overflow-x: auto;
    overscroll-behavior-x: contain;
    border-bottom: 1px solid var(--line);
    scrollbar-width: none;
  }
  .tabs::-webkit-scrollbar {
    display: none;
  }
  .tab {
    flex: 0 0 auto;
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 0.1rem;
    max-width: 11rem;
    min-height: 2.75rem;
    padding: 0.55rem 0.85rem 0.5rem;
    color: var(--muted);
    background: transparent;
    text-align: left;
    box-shadow: inset 0 -3px 0 transparent;
    transition:
      color 0.2s ease,
      box-shadow 0.2s ease;
  }
  .tab:first-child {
    padding-left: 0;
  }
  .tab:hover {
    color: var(--fg);
  }
  .tab.active {
    color: var(--fg);
    box-shadow: inset 0 -3px 0 var(--fg);
  }
  .tab:focus-visible {
    outline: 2px solid var(--fg);
    outline-offset: -2px;
  }
  .tab-media {
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
    font-size: 0.75rem;
    font-weight: 500;
    letter-spacing: 0.12em;
    white-space: nowrap;
  }
  .tab-sub {
    max-width: 100%;
    font-size: 0.625rem;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  @media (prefers-reduced-motion: reduce) {
    .tab {
      transition: none;
    }
  }

  /* ─── Edition の追加 ─── */
  .add {
    padding-top: 0.5rem;
  }
  .add-form {
    display: flex;
    flex-direction: column;
    gap: 0.6rem;
    min-width: 0;
  }
  .add-fields {
    display: flex;
    flex-wrap: wrap;
    gap: 0.375rem;
    min-width: 0;
  }
  .add-media {
    flex: 0 0 auto;
  }
  .add-title {
    flex: 1 1 10rem;
  }
  .add-actions {
    display: flex;
    gap: 0.375rem;
  }
  .add-open {
    width: 100%;
    justify-content: center;
    min-height: 2.5rem;
    border-style: dashed;
    color: var(--muted);
  }
  .add-open:hover {
    color: var(--fg);
    border-color: var(--fg);
  }

  .field {
    min-width: 0;
    min-height: 2rem;
    padding: 0.25rem 0.5rem;
    font-size: 0.8125rem;
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
    white-space: nowrap;
  }
  .btn:disabled {
    color: var(--muted);
  }
  .btn-main {
    border-color: var(--fg);
  }
</style>
