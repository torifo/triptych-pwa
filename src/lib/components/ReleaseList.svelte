<script lang="ts">
  import { ChevronDown, ChevronUp, Pencil, Plus, Trash } from '@lucide/svelte';
  import type { Edition, Release } from '../db/schema';
  import {
    createManualRelease,
    deleteRelease,
    listReleasesByEdition,
    updateRelease,
  } from '../db/store';
  import { formatReleaseDate } from '../dates';
  import {
    buildEditionReleaseView,
    isStreamStarted,
    laterReleases,
    nextEpisodeBand,
    releaseDayFor,
    upcomingBroadcastSlots,
  } from './release-history';
  import { isPostponed, type ReleaseFormValue } from './release-view';
  import OnAirLamp from './OnAirLamp.svelte';
  import ReleaseForm from './ReleaseForm.svelte';
  import SectionHead from './SectionHead.svelte';

  let { edition, onchange }: { edition: Edition; onchange?: () => void } = $props();

  // カウントダウンとランプのために時計を回す（30 秒ごと）
  const TICK_MS = 30_000;

  let releases = $state<Release[]>([]);
  let now = $state(new Date());
  let loaded = $state(false);
  let adding = $state(false);
  let editingId = $state<string | null>(null);
  let confirmingDeleteId = $state<string | null>(null);
  let actionError = $state<string | null>(null);
  let showOlder = $state(false);

  // アニメは放送日（朝5時区切り）で数える。深夜 0 時を過ぎても 25:30 の回は次話のまま
  const today = $derived(releaseDayFor(edition.mediaType, now));
  const view = $derived(buildEditionReleaseView(releases, edition.mediaType, today));
  const band = $derived(nextEpisodeBand(view.nextEpisodes, today, now));
  const casts = $derived(
    edition.mediaType === 'anime' ? upcomingBroadcastSlots(edition.syncHints?.broadcasts, now) : [],
  );
  const streamRows = $derived(view.streams.map((s) => s.release));
  const later = $derived(laterReleases(view));
  const hasWatch = $derived(!!band || casts.length > 0 || streamRows.length > 0);
  const isEmpty = $derived(
    view.nextEpisodes.length + view.streams.length + view.upcoming.length + view.recent.length === 0,
  );

  // 親が Edition を読み直しても id が同じなら、編集中のフォームを閉じない。
  const editionId = $derived(edition.id);

  async function load() {
    const list = await listReleasesByEdition(editionId);
    now = new Date();
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
    showOlder = false;
    load();
  });

  $effect(() => {
    const timer = setInterval(() => (now = new Date()), TICK_MS);
    return () => clearInterval(timer);
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
    onchange?.();
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
    onchange?.();
  }

  async function remove(release: Release) {
    actionError = null;
    try {
      await deleteRelease(release.id);
      confirmingDeleteId = null;
      await load();
      onchange?.();
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

  /** 配信先の行の状態。 */
  function streamTag(release: Release): string | null {
    return isStreamStarted(release, today) ? '配信中' : '配信予定';
  }
</script>

{#snippet rowActions(release: Release)}
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
      class="icon-btn"
      aria-label={`「${release.label}」を編集`}
      title="編集"
      onclick={() => startEdit(release)}
    >
      <Pencil size={15} />
    </button>
    <button
      type="button"
      class="icon-btn"
      aria-label={`「${release.label}」を削除`}
      title="削除"
      onclick={() => (confirmingDeleteId = release.id)}
    >
      <Trash size={15} />
    </button>
  {/if}
{/snippet}

{#snippet syncNote(release: Release)}
  {#if confirmingDeleteId === release.id && release.origin !== 'manual'}
    <p class="note">同期元に残っていれば、次の同期で再取得されます</p>
  {/if}
{/snippet}

{#snippet rows(list: Release[], tagOf?: (release: Release) => string | null)}
  <ul class="rows">
    {#each list as release (release.id)}
      {#if editingId === release.id}
        <li class="editing">
          <ReleaseForm
            mediaType={edition.mediaType}
            {release}
            onsubmit={(value) => saveEdit(release, value)}
            oncancel={() => (editingId = null)}
          />
        </li>
      {:else}
        {@const tag = tagOf?.(release) ?? null}
        <li class="row">
          <div class="row-top">
            <span class="mono when">{formatReleaseDate(release)}</span>
            {#if tag}
              <span class="tag" class:tag-strong={tag === '配信中'}>{tag}</span>
            {/if}
            {#if isPostponed(release)}
              <span class="tag">延期</span>
            {/if}
            {#if release.origin === 'manual'}
              <span class="tag tag-strong">手動</span>
            {/if}
            <span class="row-actions">{@render rowActions(release)}</span>
          </div>
          <p class="row-label">{release.label}</p>
          {@render syncNote(release)}
        </li>
      {/if}
    {/each}
  </ul>
{/snippet}

<div class="releases">
  <SectionHead title="リリース" kicker="RELEASES">
    {#snippet actions()}
      {#if !adding}
        <button type="button" class="btn btn-add" aria-label="リリースを追加" onclick={startAdd}>
          <Plus size={14} />追加
        </button>
      {/if}
    {/snippet}
  </SectionHead>

  {#if adding}
    <div class="editing">
      <ReleaseForm mediaType={edition.mediaType} onsubmit={saveNew} oncancel={() => (adding = false)} />
    </div>
  {/if}

  {#if actionError}
    <p class="note" style="color: var(--fg);" role="alert">{actionError}</p>
  {/if}

  {#if hasWatch}
    <div class="group">
      <span class="type-label">{band ? '次話と配信先' : casts.length > 0 ? '放送と配信先' : '配信先'}</span>

      {#if band}
        {#if editingId === band.release.id}
          <div class="editing">
            <ReleaseForm
              mediaType={edition.mediaType}
              release={band.release}
              onsubmit={(value) => saveEdit(band.release, value)}
              oncancel={() => (editingId = null)}
            />
          </div>
        {:else}
          <div class="band" class:live={band.onAirToday}>
            <div class="band-top">
              {#if band.onAirToday}
                <OnAirLamp />
              {:else}
                <span class="band-dot" aria-hidden="true"></span>
              {/if}
              <span class="mono band-kicker" aria-hidden={band.onAirToday}>{band.onAirToday ? 'ON AIR' : 'NEXT'}</span>
              {#if band.countdown}
                <span class="mono band-count">{band.countdown}</span>
              {/if}
              {#if isPostponed(band.release)}
                <span class="band-tag">延期</span>
              {/if}
              {#if band.release.origin === 'manual'}
                <span class="band-tag">手動</span>
              {/if}
              <span class="row-actions band-actions">{@render rowActions(band.release)}</span>
            </div>
            <div class="band-main">
              <span class="band-label">{band.release.label}</span>
              <span class="mono band-when">{formatReleaseDate(band.release)}</span>
            </div>
          </div>
          {@render syncNote(band.release)}
        {/if}
      {/if}

      {#if casts.length > 0}
        <ul class="casts" aria-label="放送局と時刻">
          {#each casts as cast, i (`${cast.channel}|${cast.date}|${cast.time}|${i}`)}
            <li class="cast">
              <span class="cast-ch">{cast.channel}</span>
              {#if cast.others > 0}
                <span class="cast-others">ほか{cast.others}局</span>
              {/if}
              {#if cast.isStreaming}
                <span class="tag">配信</span>
              {/if}
              <span class="mono cast-when">{formatReleaseDate({ date: cast.date, time: cast.time || undefined })}</span>
            </li>
          {/each}
        </ul>
      {/if}

      {#if streamRows.length > 0}
        {#if band || casts.length > 0}
          <span class="type-label sub-label">配信先</span>
        {/if}
        {@render rows(streamRows, streamTag)}
      {:else if band}
        <p class="note">配信先の情報はまだありません</p>
      {/if}
    </div>
  {/if}

  {#if later.length > 0}
    <div class="group">
      {#if hasWatch || view.recent.length > 0}
        <span class="type-label">予定</span>
      {/if}
      {@render rows(later)}
    </div>
  {/if}

  {#if view.recent.length > 0}
    <div class="group">
      <span class="type-label">直近30日</span>
      {@render rows(view.recent)}
    </div>
  {/if}

  {#if view.older.length > 0}
    <div class="group">
      <button
        type="button"
        class="btn btn-toggle"
        aria-expanded={showOlder}
        onclick={() => (showOlder = !showOlder)}
      >
        {#if showOlder}<ChevronUp size={14} />{:else}<ChevronDown size={14} />{/if}
        {showOlder ? '過去のリリースを隠す' : `過去のリリースを表示（${view.older.length}件）`}
      </button>
      {#if showOlder}
        <span class="type-label">30日より前</span>
        {@render rows(view.older)}
      {/if}
    </div>
  {/if}

  {#if loaded && isEmpty && !adding && !(showOlder && view.older.length > 0)}
    <p class="note">
      {view.older.length > 0 ? '直近30日以内のリリースはありません' : '表示するリリースはありません'}
    </p>
  {/if}
</div>

<style>
  .releases {
    display: flex;
    flex-direction: column;
    gap: 0.875rem;
    min-width: 0;
  }
  .group {
    display: flex;
    flex-direction: column;
    gap: 0.4rem;
    min-width: 0;
  }
  .sub-label {
    margin-top: 0.35rem;
  }
  .note {
    margin: 0;
    font-size: 0.6875rem;
    color: var(--muted);
  }
  .editing {
    padding: 0.375rem 0;
  }

  /* ─── 次話の帯（黒地に白文字。ダークでは --fg/--bg が入れ替わるので白地に黒文字） ─── */
  .band {
    display: flex;
    flex-direction: column;
    gap: 0.3rem;
    padding: 0.55rem 0.75rem 0.65rem;
    background: var(--fg);
    color: var(--bg);
    border-radius: 2px;
    min-width: 0;
  }
  .band-top {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.25rem 0.5rem;
    min-height: 2rem;
  }
  .band-dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    border: 1px solid currentColor;
    opacity: 0.55;
    flex-shrink: 0;
  }
  .band-kicker {
    font-size: 0.625rem;
    font-weight: 500;
    letter-spacing: 0.18em;
    opacity: 0.75;
  }
  .band.live .band-kicker {
    opacity: 1;
  }
  .band-count {
    font-size: 0.75rem;
    font-weight: 500;
  }
  .band-tag {
    padding: 0 0.25rem;
    font-size: 0.625rem;
    line-height: 1.5;
    white-space: nowrap;
    border: 1px solid currentColor;
    opacity: 0.8;
  }
  .band-main {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 0.75rem;
    min-width: 0;
  }
  .band-label {
    min-width: 0;
    font-size: 0.875rem;
    font-weight: 700;
    line-height: 1.35;
    overflow-wrap: anywhere;
  }
  .band-when {
    flex-shrink: 0;
    font-size: 0.8125rem;
    font-weight: 500;
    white-space: nowrap;
  }
  .band-actions .icon-btn {
    color: inherit;
    opacity: 0.7;
  }
  .band-actions .icon-btn:hover {
    opacity: 1;
  }
  .band-actions .btn {
    color: var(--bg);
    border-color: color-mix(in srgb, var(--bg) 45%, transparent);
  }
  .band-actions .btn-invert {
    background: var(--bg);
    color: var(--fg);
    border-color: var(--bg);
  }
  .band .icon-btn:focus-visible,
  .band .btn:focus-visible {
    outline: 2px solid var(--bg);
    outline-offset: -2px;
  }

  /* ─── 局名と時刻（しょぼかるの番組） ─── */
  .casts {
    list-style: none;
    margin: 0;
    padding: 0;
    min-width: 0;
  }
  .cast {
    display: flex;
    align-items: baseline;
    gap: 0.5rem;
    padding: 0.35rem 0;
    border-bottom: 1px solid var(--line);
    font-size: 0.75rem;
    min-width: 0;
  }
  .cast-ch {
    min-width: 0;
    flex: 0 1 auto;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-weight: 500;
  }
  .cast-others {
    font-size: 0.6875rem;
    color: var(--muted);
    white-space: nowrap;
  }
  .cast-when {
    margin-left: auto;
    font-size: 0.75rem;
    white-space: nowrap;
  }

  /* ─── Release の行 ─── */
  .rows {
    list-style: none;
    margin: 0;
    padding: 0;
    border-top: 1px solid var(--line);
    min-width: 0;
  }
  .row {
    display: flex;
    flex-direction: column;
    gap: 0.1rem;
    padding: 0.3rem 0 0.45rem;
    border-bottom: 1px solid var(--line);
    min-width: 0;
  }
  .row-top {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.125rem 0.5rem;
    min-width: 0;
  }
  .when {
    font-size: 0.75rem;
    font-weight: 500;
    white-space: nowrap;
  }
  .row-label {
    margin: 0;
    font-size: 0.75rem;
    overflow-wrap: anywhere;
  }
  .row-actions {
    display: inline-flex;
    align-items: center;
    gap: 0.25rem;
    margin-left: auto;
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
    font-weight: 700;
  }

  /* ─── ボタン ─── */
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
  .btn-add {
    border-color: var(--fg);
  }
  .btn-invert {
    background: var(--fg);
    color: var(--bg);
    border-color: var(--fg);
  }
  .btn-toggle {
    align-self: flex-start;
    color: var(--muted);
  }
  .icon-btn {
    display: inline-grid;
    place-items: center;
    width: 2rem;
    height: 2rem;
    color: var(--muted);
    background: transparent;
    transition: color 0.2s ease;
  }
  .icon-btn:hover {
    color: var(--fg);
  }
  @media (prefers-reduced-motion: reduce) {
    .icon-btn {
      transition: none;
    }
  }
</style>
