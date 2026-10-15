<script lang="ts">
  import { onMount } from 'svelte';
  import { ArrowRight, BookOpen, RefreshCw, Search } from '@lucide/svelte';
  import { listReleases, listEditions, listWorks, updateRelease, listDetectionEvents } from '../lib/db/store';
  import type { Release, Edition, Work, DetectionEvent } from '../lib/db/schema';
  import { formatReleaseDate, todayJst } from '../lib/dates';
  import { navigate } from '../lib/router.svelte';
  import { runSync } from '../lib/sync/engine';
  import { syncStatus, beginSync, completeSync, failSync } from '../lib/sync/status.svelte';
  import { acceptDetection, rejectDetection } from '../lib/sync/detect';
  import { fetchCatalogWorks } from '../lib/catalog/api';
  import type { CatalogWorkSummary } from '../lib/catalog/types';
  import Cover from '../lib/components/Cover.svelte';
  import ReleaseRow from '../lib/components/ReleaseRow.svelte';
  import TodayStrip from '../lib/components/TodayStrip.svelte';
  import {
    buildCoverIndex,
    currentSeason,
    datelineLabel,
    todayItems,
    withoutToday,
    freshOutsideLists,
    type TodayItem,
  } from '../lib/components/home-view';
  import {
    AIRING_STATUS_LABELS,
    DETECTION_VIA_LABELS,
    animeFormatLabel,
    syncKeyLabel,
  } from '../lib/components/labels';
  import { startedStreamServices } from '../lib/components/release-history';
  import { unseenReleases, upcomingReleases } from '../lib/components/release-view';

  const UPCOMING_DAYS = 7;
  const SHELF_SIZE = 12;
  const SHELF_PLACEHOLDERS = 6;

  let releases = $state<Release[]>([]);
  let editions = $state<Edition[]>([]);
  let works = $state<Work[]>([]);
  let detections = $state<DetectionEvent[]>([]);
  // 日付はすべて JST の暦日で扱う（toISOString は UTC になるので使わない）
  let today = $state(todayJst());
  // カウントダウンと放送日の切り替わり（朝5時）のための時計。1分ごとに進める
  let now = $state(new Date());
  let loaded = $state(false);

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

  const covers = $derived(buildCoverIndex(editions, works));
  const todays = $derived(todayItems(releases, editionMap, now));
  // 今日の分は上の横並びに出したので、直近からは外す
  const upcoming = $derived(withoutToday(upcomingReleases(releases, editionMap, today, UPCOMING_DAYS), todays));
  // 直近・「今日」に出ている新着はそちらに NEW の印で出し、新着の欄では繰り返さない
  const freshSplit = $derived(freshOutsideLists(unseenReleases(releases, editionMap), upcoming, todays));
  const fresh = $derived(freshSplit.list);
  const streaming = $derived(startedStreamServices(releases, today));
  const libraryEmpty = $derived(loaded && works.length === 0);

  async function refresh() {
    releases = await listReleases();
    editions = await listEditions();
    works = await listWorks();
    detections = await listDetectionEvents();
    now = new Date();
    today = todayJst(now);
    loaded = true;
    if (works.length === 0 && shelf.status === 'idle') void loadShelf();
  }

  onMount(refresh);

  // 起動時の自動同期（App 側）が終わったら読み直す
  const syncSeen = { at: syncStatus.lastSyncAt };
  $effect(() => {
    const at = syncStatus.lastSyncAt;
    if (!at || at === syncSeen.at) return;
    syncSeen.at = at;
    void refresh();
  });

  onMount(() => {
    const tick = () => {
      now = new Date();
      today = todayJst(now);
    };
    let interval: ReturnType<typeof setInterval> | undefined;
    // 分の変わり目に揃えてから 1 分ごとに進める
    const timeout = setTimeout(() => {
      tick();
      interval = setInterval(tick, 60_000);
    }, 60_000 - (Date.now() % 60_000));
    return () => {
      clearTimeout(timeout);
      if (interval) clearInterval(interval);
    };
  });

  // Library が空の時の「今期の放送中」（カタログ未作成・取得失敗なら枠を出さない）
  type ShelfState =
    | { status: 'idle' | 'loading' | 'failed' }
    | { status: 'ready'; items: CatalogWorkSummary[] };
  let shelf = $state<ShelfState>({ status: 'idle' });
  const season = $derived(currentSeason(today));

  async function loadShelf() {
    shelf = { status: 'loading' };
    try {
      const { year, season: s } = currentSeason(todayJst());
      const page = await fetchCatalogWorks({ status: 'RELEASING', year, season: s, sort: 'popularity', per: SHELF_SIZE });
      shelf = page.items.length > 0 ? { status: 'ready', items: page.items } : { status: 'failed' };
    } catch {
      shelf = { status: 'failed' };
    }
  }

  function openToday(item: TodayItem) {
    const edition = editionMap.get(item.editionId);
    if (edition) navigate(workHref(edition));
  }

  let detectionBusy = $state<string | null>(null);

  async function onAcceptDetection(detection: DetectionEvent) {
    if (detectionBusy) return;
    detectionBusy = detection.id;
    try {
      const { edition } = await acceptDetection(detection.id);
      await refresh();
      navigate(workHref(edition));
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
    if (edition) navigate(workHref(edition));
  }

  /** 押した Edition のタブで Work 詳細を開く。 */
  function workHref(edition: Pick<Edition, 'id' | 'workId'>): string {
    return `#/work/${encodeURIComponent(edition.workId)}?edition=${encodeURIComponent(edition.id)}`;
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

<section class="flex flex-col gap-6 p-4">
  <div class="flex flex-col">
    <h1 class="page-title" style="margin-bottom: 0.75rem;"><span class="hi-marker">追いかける。</span></h1>
    <!-- 紙面の日付の帯: 今日の日付・最終同期・同期の操作 -->
    <div class="dateline">
      <span class="mono dateline-date">{datelineLabel(today)}</span>
      <span class="dateline-sync">
        <span>同期</span>
        <span class="mono">{formatSyncTime(syncStatus.lastSyncAt)}</span>
      </span>
      {#if errorLabels.length > 0}
        <span class="dateline-error">
          <span style="white-space: nowrap;">エラー:</span>
          {errorLabels.join(', ')}
        </span>
      {/if}
      <button
        type="button"
        class="dateline-button"
        disabled={syncing || syncStatus.running}
        onclick={onManualSync}
      >
        <RefreshCw size={12} strokeWidth={2} aria-hidden="true" class={syncing || syncStatus.running ? 'spin' : ''} />
        {syncing || syncStatus.running ? '同期中…' : '今すぐ同期'}
      </button>
    </div>
  </div>

  {#if todays.length > 0}
    <div class="flex flex-col">
      <div class="shou"><h2>今日</h2><span class="no">TODAY</span></div>
      <TodayStrip items={todays} {covers} {now} onopen={openToday} />
    </div>
  {/if}

  {#if pendingDetections.length > 0}
    <div class="flex flex-col gap-2">
      <div class="shou">
        <h2 class="flex items-baseline gap-1.5">
          アニメ化検知
          <span class="mono text-[11px]" style="color: var(--muted); font-weight: 500;">{pendingDetections.length}</span>
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

  {#if libraryEmpty}
    <div class="flex flex-col gap-3">
      <p class="text-xs" style="color: var(--muted);">
        Library はまだ空です。カタログや検索から、追いかける作品を足していきます。
      </p>
      <div class="flex gap-2">
        <a href="#/catalog" class="cta cta-primary">
          <BookOpen size={16} strokeWidth={2} aria-hidden="true" />
          カタログを見る
        </a>
        <a href="#/search" class="cta">
          <Search size={16} strokeWidth={2} aria-hidden="true" />
          検索する
        </a>
      </div>
    </div>

    {#if shelf.status === 'loading' || shelf.status === 'ready'}
      <div class="flex flex-col">
        <div class="shou"><h2>今期の放送中</h2><span class="no">{season.season} {season.year}</span></div>
        <ul class="shelf" aria-busy={shelf.status === 'loading'}>
          {#if shelf.status === 'ready'}
            {#each shelf.items as item (item.id)}
              <li>
                <a href={`#/catalog/${encodeURIComponent(item.id)}`} class="shelf-item">
                  <Cover
                    src={item.cover}
                    title={item.title}
                    mediaType={item.kinds.includes('anime') ? 'anime' : undefined}
                    size="fill"
                  />
                  <span class="shelf-title">{item.title}</span>
                </a>
              </li>
            {/each}
          {:else}
            {#each { length: SHELF_PLACEHOLDERS } as _, i (i)}
              <li><span class="shelf-placeholder"></span></li>
            {/each}
          {/if}
        </ul>
        <div class="more-links">
          <a href={`#/catalog?status=RELEASING&year=${season.year}&season=${season.season}`}>
            今期の放送中をもっと見る<ArrowRight size={14} aria-hidden="true" />
          </a>
          <a href="#/catalog?kind=book&sort=upcoming">
            これから出る本を見る<ArrowRight size={14} aria-hidden="true" />
          </a>
        </div>
      </div>
    {/if}
  {:else if loaded}
    <div class="flex flex-col">
      <div class="shou"><h2>直近</h2><span class="no">NEXT 7 DAYS</span></div>
      {#if upcoming.length === 0}
        <p class="text-xs" style="color: var(--muted);">
          {todays.length > 0 ? '明日から7日以内の予定はありません' : '7日以内の予定はありません'}
        </p>
      {:else}
        <div class="flex flex-col" style="border-top: 1px solid var(--line);">
          {#each upcoming as release (release.id)}
            <ReleaseRow
              {release}
              edition={editionMap.get(release.editionId)}
              workTitle={workTitleFor(release.editionId)}
              streaming={streaming.get(release.editionId)}
              cover={covers.get(release.editionId)}
              accent
              isNew={!release.seenAt}
              onclick={() => openRelease(release, !release.seenAt)}
            />
          {/each}
        </div>
      {/if}
    </div>

    <div class="flex flex-col">
      <div class="shou"><h2>新着</h2><span class="no">UNSEEN</span></div>
      {#if fresh.length === 0}
        <p class="text-xs" style="color: var(--muted);">
          {freshSplit.inline > 0 ? `新しく入った ${freshSplit.inline} 件は、上の一覧に NEW の印で出しています` : '新着はありません'}
        </p>
      {:else}
        <div class="flex flex-col" style="border-top: 1px solid var(--line);">
          {#each fresh as release (release.id)}
            <ReleaseRow
              {release}
              edition={editionMap.get(release.editionId)}
              workTitle={workTitleFor(release.editionId)}
              streaming={streaming.get(release.editionId)}
              cover={covers.get(release.editionId)}
              emphasis={false}
              onclick={() => openRelease(release, true)}
            />
          {/each}
        </div>
      {/if}
    </div>
  {/if}
</section>

<style>
  .dateline {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.25rem 0.75rem;
    padding: 0.375rem 0;
    border-top: 1px solid var(--fg);
    border-bottom: 1px solid var(--line);
    font-size: 11px;
    color: var(--muted);
  }
  .dateline-date {
    color: var(--fg);
    font-weight: 500;
    letter-spacing: 0.04em;
    white-space: nowrap;
  }
  .dateline-sync {
    display: inline-flex;
    gap: 0.35em;
    white-space: nowrap;
  }
  .dateline-error {
    color: var(--fg);
  }
  .dateline-button {
    display: inline-flex;
    align-items: center;
    gap: 0.3rem;
    margin-left: auto;
    padding: 0.125rem 0.5rem;
    border: 1px solid var(--fg);
    color: var(--fg);
    white-space: nowrap;
  }
  .dateline-button:disabled {
    border-color: var(--line);
    color: var(--muted);
  }
  .dateline-button :global(.spin) {
    animation: spin 1s linear infinite;
  }
  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
  }

  /* Library が空の時の誘導 */
  .cta {
    display: inline-flex;
    flex: 1;
    align-items: center;
    justify-content: center;
    gap: 0.4rem;
    min-height: 44px;
    padding: 0 0.75rem;
    border: 1px solid var(--fg);
    color: var(--fg);
    font-size: 0.8125rem;
    font-weight: 700;
    white-space: nowrap;
  }
  .cta-primary {
    background: var(--fg);
    color: var(--bg);
  }

  .shelf {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 1rem 0.75rem;
    list-style: none;
    margin: 0;
    padding: 0;
  }
  .shelf-item {
    display: flex;
    flex-direction: column;
    gap: 0.3rem;
    min-width: 0;
  }
  .shelf-title {
    display: block;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 11px;
    line-height: 1.35;
  }
  .shelf-placeholder {
    display: block;
    aspect-ratio: 2 / 3;
    border-radius: 2px;
    background: var(--cover-ph);
  }

  @media (prefers-reduced-motion: reduce) {
    .dateline-button :global(.spin) {
      animation: none;
    }
  }
  /* 今期の棚の下: カタログのよく使う見え方へ */
  .more-links {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem 1.25rem;
    padding-top: 0.75rem;
  }
  .more-links a {
    display: inline-flex;
    align-items: center;
    gap: 0.25rem;
    font-size: 0.75rem;
    color: var(--fg);
    text-decoration: underline;
    text-underline-offset: 0.2em;
  }
</style>
