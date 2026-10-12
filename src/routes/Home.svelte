<script lang="ts">
  import { onMount } from 'svelte';
  import { listReleases, listEditions, listWorks, updateRelease, listDetectionEvents } from '../lib/db/store';
  import type { Release, Edition, Work, DetectionEvent } from '../lib/db/schema';
  import { navigate } from '../lib/router.svelte';
  import { runSync } from '../lib/sync/engine';
  import { syncStatus, beginSync, completeSync, failSync } from '../lib/sync/status.svelte';
  import { acceptDetection, rejectDetection } from '../lib/sync/detect';
  import MediaBadge from '../lib/components/MediaBadge.svelte';

  const TRACKED_STATUSES: Edition['status'][] = ['plan', 'active', 'paused'];
  const UPCOMING_DAYS = 7;

  let releases = $state<Release[]>([]);
  let editions = $state<Edition[]>([]);
  let works = $state<Work[]>([]);
  let detections = $state<DetectionEvent[]>([]);

  const editionMap = $derived(new Map(editions.map((e) => [e.id, e])));
  const workMap = $derived(new Map(works.map((w) => [w.id, w])));

  const pendingDetections = $derived(
    detections
      .filter((d) => d.state === 'pending')
      .sort((a, b) => b.matchScore - a.matchScore),
  );

  const AIRING_STATUS_LABELS: Record<DetectionEvent['candidate']['airingStatus'], string> = {
    NOT_YET_RELEASED: '放送前',
    RELEASING: '放送中',
    FINISHED: '放送済',
    CANCELLED: '中止',
    HIATUS: '休止中',
  };

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
    detections = await listDetectionEvents();
  }

  onMount(refresh);

  let detectionBusy = $state<string | null>(null);

  async function onAcceptDetection(detection: DetectionEvent) {
    if (detectionBusy) return;
    detectionBusy = detection.id;
    try {
      const { edition } = await acceptDetection(detection.id);
      await refresh();
      navigate(`#/work/${edition.workId}`);
    } finally {
      detectionBusy = null;
    }
  }

  async function onRejectDetection(detection: DetectionEvent) {
    if (detectionBusy) return;
    detectionBusy = detection.id;
    try {
      await rejectDetection(detection.id);
      await refresh();
    } finally {
      detectionBusy = null;
    }
  }

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
  <h1 class="page-title"><span class="hi-marker">追いかける。</span></h1>

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

  {#if pendingDetections.length > 0}
    <div class="flex flex-col gap-2">
      <div class="shou">
        <h2 class="flex items-center gap-1.5">
          アニメ化検知
          <span class="inline-block h-1.5 w-1.5" style="background: var(--hi);" aria-hidden="true"></span>
        </h2>
        <span class="no">DETECTED</span>
      </div>
      <div class="flex flex-col gap-1.5">
        {#each pendingDetections as detection (detection.id)}
          {@const w = workMap.get(detection.workId)}
          <div
            class="flex flex-col gap-1.5 px-2 py-1.5"
            style="border: 1px solid var(--line); min-width: 0;"
          >
            <div class="flex items-center gap-2" style="min-width: 0;">
              <span class="min-w-0 flex-1 truncate text-sm">{detection.candidate.title}</span>
              <span class="text-[11px]" style="color: var(--muted); white-space: nowrap;">
                {AIRING_STATUS_LABELS[detection.candidate.airingStatus]}
              </span>
              <span class="text-[11px]" style="color: var(--muted); white-space: nowrap;">
                {Math.round(detection.matchScore * 100)}%
              </span>
            </div>
            <div class="flex items-center gap-2" style="min-width: 0;">
              {#if w}
                <span class="min-w-0 flex-1 truncate text-[11px]" style="color: var(--muted);">
                  原作: {w.title}
                </span>
              {/if}
              <button
                type="button"
                class="px-2 py-0.5 text-[11px]"
                style="border: 1px solid var(--fg); color: var(--fg); white-space: nowrap;"
                disabled={detectionBusy === detection.id}
                onclick={() => onAcceptDetection(detection)}
              >
                リンクする
              </button>
              <button
                type="button"
                class="px-2 py-0.5 text-[11px]"
                style="border: 1px solid var(--line); color: var(--muted); white-space: nowrap;"
                disabled={detectionBusy === detection.id}
                onclick={() => onRejectDetection(detection)}
              >
                却下
              </button>
            </div>
          </div>
        {/each}
      </div>
    </div>
  {/if}

  <div class="flex flex-col gap-2">
    <div class="shou"><h2>直近</h2><span class="no">NEXT 7 DAYS</span></div>
    {#if upcoming.length === 0}
      <p class="text-xs" style="color: var(--muted);">7日以内の予定はありません</p>
    {:else}
      <div class="flex flex-col" style="border-top: 1px solid var(--line);">
        {#each upcoming as release (release.id)}
          {@const edition = editionMap.get(release.editionId)}
          <button
            type="button"
            class="hi-bar row-hover flex items-center gap-2 py-2 pr-2 text-left"
            style="border-bottom: 1px solid var(--line); min-width: 0;"
            onclick={() => openRelease(release, false)}
          >
            <span class="text-[11px] font-bold" style="white-space: nowrap; font-variant-numeric: tabular-nums;">{formatDate(release.date)}</span>
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
    <div class="shou"><h2>新着</h2><span class="no">UNSEEN</span></div>
    {#if fresh.length === 0}
      <p class="text-xs" style="color: var(--muted);">新着はありません</p>
    {:else}
      <div class="flex flex-col" style="border-top: 1px solid var(--line);">
        {#each fresh as release (release.id)}
          {@const edition = editionMap.get(release.editionId)}
          <button
            type="button"
            class="row-hover flex items-center gap-2 py-2 text-left"
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
