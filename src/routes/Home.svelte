<script lang="ts">
  import { onMount } from 'svelte';
  import { listReleases, listEditions, listWorks, updateRelease, listDetectionEvents } from '../lib/db/store';
  import type { Release, Edition, Work, DetectionEvent } from '../lib/db/schema';
  import { formatReleaseDate, todayJst } from '../lib/dates';
  import { navigate } from '../lib/router.svelte';
  import { runSync } from '../lib/sync/engine';
  import { syncStatus, beginSync, completeSync, failSync } from '../lib/sync/status.svelte';
  import { acceptDetection, rejectDetection } from '../lib/sync/detect';
  import MediaBadge from '../lib/components/MediaBadge.svelte';
  import ReleaseRow from '../lib/components/ReleaseRow.svelte';
  import {
    AIRING_STATUS_LABELS,
    DETECTION_VIA_LABELS,
    animeFormatLabel,
    syncKeyLabel,
  } from '../lib/components/labels';
  import { startedStreamServices } from '../lib/components/release-history';
  import { unseenReleases, upcomingReleases } from '../lib/components/release-view';

  const UPCOMING_DAYS = 7;

  let releases = $state<Release[]>([]);
  let editions = $state<Edition[]>([]);
  let works = $state<Work[]>([]);
  let detections = $state<DetectionEvent[]>([]);
  // 日付はすべて JST の暦日で扱う（toISOString は UTC になるので使わない）
  let today = $state(todayJst());

  const editionMap = $derived(new Map(editions.map((e) => [e.id, e])));
  const workMap = $derived(new Map(works.map((w) => [w.id, w])));

  const pendingDetections = $derived(
    detections
      .filter((d) => d.state === 'pending')
      // relation / sequel 由来を先に、同じ由来の中ではスコアの高い順
      .sort((a, b) => {
        const ra = (a.via ?? 'title') === 'title' ? 1 : 0;
        const rb = (b.via ?? 'title') === 'title' ? 1 : 0;
        return ra - rb || b.matchScore - a.matchScore;
      }),
  );

  const upcoming = $derived(upcomingReleases(releases, editionMap, today, UPCOMING_DAYS));
  const fresh = $derived(unseenReleases(releases, editionMap));
  const streaming = $derived(startedStreamServices(releases, today));

  async function refresh() {
    releases = await listReleases();
    editions = await listEditions();
    works = await listWorks();
    detections = await listDetectionEvents();
    today = todayJst();
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

  function formatSyncTime(iso: string | null): string {
    if (!iso) return '未実行';
    const d = new Date(iso);
    return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  }

  /** 検知候補の放送開始日（精度に従う）。日付が無ければ null。 */
  function candidateStart(detection: DetectionEvent): string | null {
    const { startDate, startPrecision } = detection.candidate;
    return startDate ? formatReleaseDate({ date: startDate, precision: startPrecision }) : null;
  }

  let syncing = $state(false);

  async function onManualSync() {
    if (syncing) return;
    syncing = true;
    beginSync();
    try {
      const result = await runSync({ force: true });
      // 全ソース失敗時は lastSyncAt が前回のまま返る（未同期なら無い）
      if (result.lastSyncAt) completeSync(result.lastSyncAt, result.errors);
      else failSync(result.errors);
      await refresh();
    } catch (err) {
      failSync({});
    } finally {
      syncing = false;
    }
  }

  // `detect`（アニメ化検知）もソースと同じ並びで「検知」と出す
  const errorLabels = $derived(Object.keys(syncStatus.errors).map(syncKeyLabel));
</script>

<section class="flex flex-col gap-4 p-4">
  <h1 class="page-title"><span class="hi-marker">追いかける。</span></h1>

  <div
    class="flex flex-wrap items-center gap-2 px-2 py-1.5 text-[11px]"
    style="border: 1px solid var(--line); color: var(--muted);"
  >
    <span style="white-space: nowrap;">最終同期: {formatSyncTime(syncStatus.lastSyncAt)}</span>
    {#if errorLabels.length > 0}
      <span style="color: var(--fg);">
        <span style="white-space: nowrap;">エラー:</span>
        {errorLabels.join(', ')}
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
          {@const via = detection.via ?? 'title'}
          {@const startLabel = candidateStart(detection)}
          <div
            class="flex flex-col gap-1.5 px-2 py-1.5"
            style="border: 1px solid var(--line); min-width: 0;"
          >
            <div class="flex items-center gap-2" style="min-width: 0;">
              <span class="min-w-0 flex-1 truncate text-sm">{detection.candidate.title}</span>
              <span class="text-[11px]" style="color: var(--muted); white-space: nowrap;">
                {AIRING_STATUS_LABELS[detection.candidate.airingStatus]}
              </span>
            </div>
            <div class="flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px]" style="color: var(--muted);">
              <!-- relation / sequel は確度が高いので実線、タイトル一致は薄い罫線とスコアで区別する -->
              <span
                class="px-1 text-[10px] font-bold"
                style={`white-space: nowrap; line-height: 1.5; border: 1px solid ${via === 'title' ? 'var(--line)' : 'var(--fg)'}; color: ${via === 'title' ? 'var(--muted)' : 'var(--fg)'};`}
              >
                {DETECTION_VIA_LABELS[via]}
              </span>
              {#if detection.candidate.format}
                <span class="type-label">{animeFormatLabel(detection.candidate.format)}</span>
              {/if}
              {#if via === 'title'}
                <span style="white-space: nowrap;">{Math.round(detection.matchScore * 100)}%</span>
              {/if}
              {#if startLabel}
                <span style="white-space: nowrap;">{startLabel}</span>
              {/if}
            </div>
            <div class="flex items-center gap-2" style="min-width: 0;">
              {#if w}
                <span class="min-w-0 flex-1 truncate text-[11px]" style="color: var(--muted);">
                  {via === 'sequel' ? '作品' : '原作'}: {w.title}
                </span>
              {:else}
                <span class="flex-1"></span>
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
          <ReleaseRow
            {release}
            edition={editionMap.get(release.editionId)}
            workTitle={workTitleFor(release.editionId)}
            streaming={streaming.get(release.editionId)}
            accent
            onclick={() => openRelease(release, false)}
          />
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
          <ReleaseRow
            {release}
            edition={editionMap.get(release.editionId)}
            workTitle={workTitleFor(release.editionId)}
            streaming={streaming.get(release.editionId)}
            emphasis={false}
            onclick={() => openRelease(release, true)}
          />
        {/each}
      </div>
    {/if}
  </div>
</section>
