<script lang="ts">
  import { onDestroy, onMount } from 'svelte';
  import {
    BookOpen,
    CalendarDays,
    Check,
    CheckCheck,
    Database,
    Download,
    ExternalLink,
    Hand,
    HardDrive,
    House,
    Inbox,
    Info,
    Library as LibraryIcon,
    Monitor,
    Moon,
    Palette,
    Plug,
    Power,
    Radar,
    RefreshCw,
    RotateCw,
    Sun,
    Timer,
    Trash,
    Undo2,
    Upload,
    type LucideIcon,
  } from '@lucide/svelte';
  import { exportAll, importAll, getLastExportAt, PortabilityError } from '../lib/db/portability';
  import {
    clearLibrary,
    listEditions,
    listRejectedDetections,
    listReleases,
    listWorks,
    markReleasesSeen,
    reopenDetectionEvent,
    type RejectedDetection,
  } from '../lib/db/store';
  import { TOKEN_KEY, getApiBase, setApiBase, setApiToken } from '../lib/api';
  import { runSync } from '../lib/sync/engine';
  import { beginSync, completeSync, failSync, syncStatus } from '../lib/sync/status.svelte';
  import { BUILD_COMMAND, fetchCatalogStats } from '../lib/catalog/api';
  import type { CatalogStats } from '../lib/catalog/types';
  import { formatJstDateTime } from '../lib/catalog/view';
  import {
    RECENT_JOB_MINUTES,
    describeJobError,
    fetchCurrentJob,
    isAlreadyRunning,
    jobElapsed,
    jobOutcome,
    pollJob,
    startRecentJob,
    tailLines,
    type CatalogJob,
  } from '../lib/catalog/jobs';
  import { syncKeyLabel } from '../lib/components/labels';
  import { unseenReleases } from '../lib/components/release-view';
  import {
    AUTO_SYNC_LABELS,
    AUTO_SYNC_NOTES,
    DATA_SOURCES,
    EXPORT_STALE_DAYS,
    START_SCREEN_LABELS,
    SYNC_SOURCE_LABELS,
    buildInfo,
    canClearLibrary,
    catalogSourceRows,
    formatCount,
    formatUsage,
    hasStorageApi,
    isExportStale,
    readStorageInfo,
    requestPersistent,
    type StorageInfo,
  } from '../lib/components/settings-view';
  import {
    AUTO_SYNC_MODES,
    START_SCREENS,
    SYNC_SOURCES,
    preferences,
    type AutoSyncMode,
    type Preferences,
    type StartScreen,
    type SyncSourceName,
  } from '../lib/preferences';
  import {
    THEME_LABELS,
    THEME_PREFS,
    applyTheme,
    loadThemePref,
    saveThemePref,
    type ThemePref,
  } from '../lib/theme';

  const THEME_ICONS: Record<ThemePref, LucideIcon> = { light: Sun, dark: Moon, system: Monitor };
  const AUTO_SYNC_ICONS: Record<AutoSyncMode, LucideIcon> = { launch: Power, hourly: Timer, manual: Hand };
  const START_SCREEN_ICONS: Record<StartScreen, LucideIcon> = {
    home: House,
    library: LibraryIcon,
    calendar: CalendarDays,
    catalog: BookOpen,
  };
  /** 実行中のジョブで見せるログの行数。 */
  const JOB_LOG_LINES = 6;

  let lastExportAt = $state<string | undefined>(undefined);
  let apiBase = $state(getApiBase());
  let tokenConfigured = $state(!!localStorage.getItem(TOKEN_KEY));
  let tokenDraft = $state('');
  let themePref = $state<ThemePref>(loadThemePref());
  let prefs = $state<Preferences>(preferences.all());

  let exporting = $state(false);
  let importing = $state(false);
  let importPending = $state<{ file: File; text: string } | null>(null);
  let statusMessage = $state<{ kind: 'ok' | 'error'; text: string } | null>(null);

  let fileInput = $state<HTMLInputElement | null>(null);

  // ストレージの節は、ブラウザに API がある時だけ出す（節の出入りで画面が動かないよう、有無は最初に決める）
  const storageApi = typeof navigator === 'undefined' ? undefined : navigator.storage;
  const showStorage = hasStorageApi(storageApi);
  let storageInfo = $state<StorageInfo | null>(null);
  let persistAsking = $state(false);
  let persistDenied = $state(false);

  const exportStale = $derived(isExportStale(lastExportAt));

  // キーはソース名のほか、アニメ化検知の `detect`（「検知」と表示する）
  const syncErrorEntries = $derived(
    Object.entries(syncStatus.errors) as [string, { message: string; occurredAt: string }][],
  );

  const usageText = $derived(storageInfo ? formatUsage(storageInfo) : '…');
  const enabledSourceCount = $derived(SYNC_SOURCES.filter((s) => prefs.syncSources[s]).length);

  // ─── Library（新着・却下した検知・作品数） ───
  let libraryLoaded = $state(false);
  let worksCount = $state(0);
  let unseenIds = $state<string[]>([]);
  let rejected = $state<RejectedDetection[]>([]);
  let workTitles = $state<Map<string, string>>(new Map());

  async function refreshLibrary() {
    const [works, editions, releases, rejectedList] = await Promise.all([
      listWorks(),
      listEditions(),
      listReleases(),
      listRejectedDetections(),
    ]);
    worksCount = works.length;
    workTitles = new Map(works.map((w) => [w.id, w.title]));
    // Home の新着と同じ数え方（追跡中の Edition・毎週動く次回の配信は除く）
    unseenIds = unseenReleases(releases, new Map(editions.map((e) => [e.id, e]))).map((r) => r.id);
    rejected = rejectedList;
    libraryLoaded = true;
  }

  async function refreshLastExportAt() {
    lastExportAt = await getLastExportAt();
  }

  async function refreshStorage() {
    storageInfo = await readStorageInfo(storageApi);
  }

  onMount(() => {
    void refreshLastExportAt();
    void refreshLibrary();
    void refreshStats();
    void restoreCurrentJob();
    if (showStorage) void refreshStorage();
    return preferences.subscribe((next) => (prefs = next));
  });

  // 同期（自動・手動）が終わったら、未読の数と却下の一覧を読み直す
  const syncSeen = { at: syncStatus.lastSyncAt };
  $effect(() => {
    const at = syncStatus.lastSyncAt;
    if (!at || at === syncSeen.at) return;
    syncSeen.at = at;
    void refreshLibrary();
  });

  // ─── 同期 ───

  async function onSyncNow() {
    if (syncStatus.running) return;
    beginSync();
    try {
      const result = await runSync({ force: true });
      // 全ソース失敗時は lastSyncAt が前回のまま返る（未同期なら無い）
      if (result.lastSyncAt) completeSync(result.lastSyncAt, result.errors);
      else failSync(result.errors);
    } catch {
      failSync({});
    }
  }

  function onSelectAutoSync(mode: AutoSyncMode) {
    preferences.set('autoSync', mode);
  }

  function onToggleSource(source: SyncSourceName, e: Event) {
    preferences.setSyncSource(source, (e.currentTarget as HTMLInputElement).checked);
  }

  function onToggleDetection(e: Event) {
    preferences.set('detection', (e.currentTarget as HTMLInputElement).checked);
  }

  // ─── 新着 ───

  let markConfirm = $state(false);
  let marking = $state(false);
  let markMessage = $state<string | null>(null);

  async function confirmMarkAllSeen() {
    if (marking) return;
    marking = true;
    try {
      const count = await markReleasesSeen(unseenIds);
      markMessage = `${count} 件を既読にしました`;
      markConfirm = false;
      await refreshLibrary();
    } catch (err) {
      markMessage = `既読にできませんでした: ${(err as Error).message}`;
    } finally {
      marking = false;
    }
  }

  // ─── アニメ化検知（却下の取り消し） ───

  let reopening = $state<string | null>(null);
  let reopenMessage = $state<string | null>(null);

  async function onReopen(detection: RejectedDetection) {
    if (reopening) return;
    reopening = detection.id;
    reopenMessage = null;
    try {
      await reopenDetectionEvent(detection.id);
      reopenMessage = `「${detection.candidate.title}」を候補に戻しました。Home のアニメ化検知に出ます`;
      await refreshLibrary();
    } catch (err) {
      reopenMessage = `取り消せませんでした: ${(err as Error).message}`;
    } finally {
      reopening = null;
    }
  }

  /** JST の日付だけ（`2026-10-14`）。 */
  function jstDate(iso: string): string {
    return formatJstDateTime(iso).slice(0, 10);
  }

  // ─── カタログ ───

  type StatsState = { status: 'loading' } | { status: 'ready'; stats: CatalogStats } | { status: 'failed' };
  let statsState = $state<StatsState>({ status: 'loading' });
  const sourceRows = $derived(statsState.status === 'ready' ? catalogSourceRows(statsState.stats.sources) : []);

  async function refreshStats() {
    try {
      statsState = { status: 'ready', stats: await fetchCatalogStats() };
    } catch {
      statsState = { status: 'failed' };
    }
  }

  let job = $state<CatalogJob | null>(null);
  let jobStarting = $state(false);
  /** ボタンの下に出す理由。busy は 409（別の更新が実行中）で、失敗としては出さない。 */
  let jobNotice = $state<{ kind: 'error' | 'busy'; text: string } | null>(null);
  let jobNow = $state(Date.now());
  let stopPolling: (() => void) | null = null;

  const jobRunning = $derived(job?.state === 'running');
  const jobResult = $derived(job ? jobOutcome(job) : null);
  const jobLog = $derived(job ? tailLines(job.lines, JOB_LOG_LINES) : []);

  function watchJob() {
    stopPolling?.();
    stopPolling = pollJob({
      onUpdate: (next) => {
        jobNow = Date.now();
        if (next) job = next;
        if (!next || next.state !== 'running') {
          stopPolling = null;
          // collector の再起動でジョブが消えた時は、終わり方が分からない
          if (!next && job?.state === 'running') {
            job = null;
            jobNotice = { kind: 'error', text: '実行中のジョブを見失いました（collector の再起動など）。件数を読み直します' };
          }
          void refreshStats();
        }
      },
      onError: (err, gaveUp) => {
        if (gaveUp) {
          stopPolling = null;
          jobNotice = { kind: 'error', text: `状態を読めなくなりました: ${describeJobError(err)}` };
        }
      },
    });
  }

  /** 開いた時に、走っている（直近の）ジョブがあれば出す。API が無い collector では黙っている。 */
  async function restoreCurrentJob() {
    try {
      const current = await fetchCurrentJob();
      if (!current || job) return;
      job = current;
      jobNow = Date.now();
      if (current.state === 'running') watchJob();
    } catch {
      // ボタンを押した時に理由を出す
    }
  }

  async function onStartJob() {
    if (jobStarting || jobRunning) return;
    jobStarting = true;
    jobNotice = null;
    try {
      job = await startRecentJob();
      jobNow = Date.now();
      if (job.state === 'running') watchJob();
      else void refreshStats();
    } catch (err) {
      if (isAlreadyRunning(err)) {
        // この collector が始めたジョブなら、その状態を追う。ターミナルや前回の実行なら、終わるまで待ってもらう
        const current = await fetchCurrentJob().catch(() => null);
        if (current?.state === 'running') {
          job = current;
          jobNow = Date.now();
          watchJob();
        } else {
          jobNotice = { kind: 'busy', text: describeJobError(err) };
        }
      } else {
        jobNotice = { kind: 'error', text: describeJobError(err) };
      }
    } finally {
      jobStarting = false;
    }
  }

  onDestroy(() => stopPolling?.());

  // ─── データ ───

  function exportFileName(): string {
    const now = new Date();
    const y = now.getFullYear();
    const m = String(now.getMonth() + 1).padStart(2, '0');
    const d = String(now.getDate()).padStart(2, '0');
    return `triptych-export-${y}${m}${d}.json`;
  }

  async function onExport() {
    if (exporting) return;
    exporting = true;
    statusMessage = null;
    try {
      const payload = await exportAll();
      const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = exportFileName();
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      await refreshLastExportAt();
      statusMessage = { kind: 'ok', text: 'エクスポートしました' };
    } catch (err) {
      statusMessage = { kind: 'error', text: `エクスポートに失敗しました: ${(err as Error).message}` };
    } finally {
      exporting = false;
    }
  }

  function onFileSelected(e: Event) {
    const input = e.currentTarget as HTMLInputElement;
    const file = input.files?.[0];
    if (!file) return;
    statusMessage = null;
    const reader = new FileReader();
    reader.onload = () => {
      importPending = { file, text: String(reader.result ?? '') };
    };
    reader.onerror = () => {
      statusMessage = { kind: 'error', text: 'ファイルの読み込みに失敗しました' };
    };
    reader.readAsText(file);
  }

  function cancelImport() {
    importPending = null;
    if (fileInput) fileInput.value = '';
  }

  async function confirmImport() {
    if (!importPending || importing) return;
    importing = true;
    statusMessage = null;
    try {
      await importAll(importPending.text, 'replace');
      importPending = null;
      if (fileInput) fileInput.value = '';
      location.reload();
    } catch (err) {
      const message = err instanceof PortabilityError ? err.message : (err as Error).message;
      statusMessage = { kind: 'error', text: `インポートに失敗しました: ${message}` };
    } finally {
      importing = false;
    }
  }

  // Library を空にする: 1回目でボタンが「本当に消す」に変わり、作品数を打つと押せる
  let clearArmed = $state(false);
  let clearTyped = $state('');
  let clearing = $state(false);
  let clearError = $state<string | null>(null);
  const clearReady = $derived(canClearLibrary(clearTyped, worksCount));

  function cancelClear() {
    clearArmed = false;
    clearTyped = '';
    clearError = null;
  }

  async function onClearLibrary() {
    if (clearing) return;
    if (!clearArmed) {
      clearArmed = true;
      clearError = null;
      return;
    }
    if (!clearReady) return;
    clearing = true;
    try {
      await clearLibrary();
      // 画面のあちこち（ヘッダーの ON AIR など）が古い数を持っているので、インポートと同じく読み直す
      location.reload();
    } catch (err) {
      clearError = `消せませんでした: ${(err as Error).message}`;
      clearing = false;
    }
  }

  async function onRequestPersist() {
    if (persistAsking) return;
    persistAsking = true;
    persistDenied = false;
    try {
      const granted = await requestPersistent(storageApi);
      persistDenied = granted === false;
      await refreshStorage();
    } finally {
      persistAsking = false;
    }
  }

  function onApiBaseBlur() {
    setApiBase(apiBase.trim());
  }

  function onTokenSave() {
    setApiToken(tokenDraft.trim());
    tokenConfigured = !!tokenDraft.trim();
    tokenDraft = '';
  }

  function onTokenClear() {
    setApiToken('');
    tokenConfigured = false;
    tokenDraft = '';
  }

  function onSelectTheme(pref: ThemePref) {
    themePref = pref;
    saveThemePref(pref);
    applyTheme(pref);
  }

  function onSelectStartScreen(screen: StartScreen) {
    preferences.set('startScreen', screen);
  }

  const build = buildInfo();
</script>

{#snippet head(Icon: LucideIcon, id: string, title: string, kicker: string)}
  <div class="shou sec-head">
    <h2 {id}><Icon size={15} strokeWidth={2} aria-hidden="true" />{title}</h2>
    <span class="no" aria-hidden="true">{kicker}</span>
  </div>
{/snippet}

{#snippet sub(title: string)}
  <h3 class="sub-head type-label">{title}</h3>
{/snippet}

{#snippet toggle(id: string, name: string, what: string, checked: boolean, onchange: (e: Event) => void)}
  <label class="toggle-row" for={id}>
    <span class="toggle-text">
      <span class="toggle-name">{name}</span>
      <span class="note">{what}</span>
    </span>
    <input {id} class="switch-input" type="checkbox" role="switch" {checked} {onchange} />
    <span class="switch" aria-hidden="true"></span>
  </label>
{/snippet}

<section class="settings page">
  <h1 class="page-title title">Settings<span class="type-label" style="margin-left: 0.8em;">設定とデータ</span></h1>

  <div class="col">
    <!-- データ -->
    <section class="block" aria-labelledby="sec-data">
      {@render head(Database, 'sec-data', 'データ', 'DATA')}

      <div class="actions">
        <button type="button" class="btn btn-main" disabled={exporting} onclick={onExport}>
          <Download size={14} aria-hidden="true" />{exporting ? 'エクスポート中…' : 'JSONエクスポート'}
        </button>
        <button type="button" class="btn btn-main" onclick={() => fileInput?.click()}>
          <Upload size={14} aria-hidden="true" />インポート
        </button>
        <input
          bind:this={fileInput}
          type="file"
          accept="application/json"
          aria-label="インポートする JSON ファイル"
          hidden
          onchange={onFileSelected}
        />
      </div>

      {#if statusMessage}
        <p class="notice" role={statusMessage.kind === 'error' ? 'alert' : 'status'}>
          {#if statusMessage.kind === 'ok'}
            <Check size={14} aria-hidden="true" />
          {:else}
            <span class="dot" aria-hidden="true"></span>
          {/if}
          <span>{statusMessage.text}</span>
        </p>
      {/if}

      <dl class="facts">
        <div class="fact">
          <dt class="type-label">最終エクスポート</dt>
          <dd>
            {#if lastExportAt}
              <span class="mono">{formatJstDateTime(lastExportAt)}</span>
            {:else}
              <span class="dim">未実施</span>
            {/if}
          </dd>
        </div>
      </dl>
      {#if exportStale}
        <p class="alert">
          <span class="dot" aria-hidden="true"></span>
          <strong>要エクスポート</strong>
          <span class="note">{lastExportAt ? `${EXPORT_STALE_DAYS}日以上前です` : 'まだ書き出していません'}</span>
        </p>
      {/if}

      {#if importPending}
        <div class="confirm" role="group" aria-label="インポートの確認">
          <p class="confirm-text">
            「{importPending.file.name}」で全データを置き換えます。現在のデータは失われます。よろしいですか？
          </p>
          <div class="actions">
            <button type="button" class="btn btn-solid" disabled={importing} onclick={confirmImport}>
              {importing ? 'インポート中…' : '置き換える'}
            </button>
            <button type="button" class="btn" disabled={importing} onclick={cancelImport}>キャンセル</button>
          </div>
        </div>
      {/if}

      <!-- Library を空にする（二段階の確認） -->
      <div class="sub-block">
        {@render sub('Library を空にする')}
        {#if clearArmed}
          <div class="confirm" role="group" aria-label="Library を空にする確認">
            <p class="confirm-text">
              作品 <span class="mono">{formatCount(worksCount)}</span> 件と、その版・発売予定・検知をすべて消します。元に戻せません。
              消す前に「JSONエクスポート」で書き出しておくと、インポートで戻せます。
            </p>
            <div class="field-group">
              <label class="type-label" for="clear-confirm">確かめのため、作品数（{worksCount}）を入力</label>
              <input
                id="clear-confirm"
                class="field mono"
                type="text"
                inputmode="numeric"
                autocomplete="off"
                spellcheck="false"
                bind:value={clearTyped}
              />
            </div>
            <div class="actions">
              <button type="button" class="btn btn-solid" disabled={!clearReady || clearing} onclick={onClearLibrary}>
                <Trash size={14} aria-hidden="true" />{clearing ? '消しています…' : '本当に消す'}
              </button>
              <button type="button" class="btn" disabled={clearing} onclick={cancelClear}>キャンセル</button>
            </div>
          </div>
        {:else}
          <div class="actions">
            <button
              type="button"
              class="btn"
              disabled={!libraryLoaded || worksCount === 0}
              onclick={onClearLibrary}
            >
              <Trash size={14} aria-hidden="true" />Library を空にする
            </button>
          </div>
          <p class="note">
            {#if libraryLoaded && worksCount === 0}
              Library は空です。
            {:else}
              登録した作品をすべて消します。消す前に書き出しておくことを勧めます。
            {/if}
          </p>
        {/if}
        {#if clearError}
          <p class="alert" role="alert"><span class="dot" aria-hidden="true"></span><span>{clearError}</span></p>
        {/if}
      </div>
    </section>

    <!-- ストレージ（API があるブラウザだけ） -->
    {#if showStorage}
      <section class="block" aria-labelledby="sec-storage">
        {@render head(HardDrive, 'sec-storage', 'ストレージ', 'STORAGE')}

        <dl class="facts">
          <div class="fact">
            <dt class="type-label">永続化</dt>
            <dd>
              {#if storageInfo === null}
                <span class="mono dim">…</span>
              {:else if storageInfo.persisted === true}
                済
              {:else if storageInfo.persisted === false}
                未
              {:else}
                <span class="dim">—</span>
              {/if}
            </dd>
          </div>
          <div class="fact">
            <dt class="type-label">使用量</dt>
            <dd class="mono">{usageText}</dd>
          </div>
        </dl>

        {#if storageInfo?.persisted === false && storageInfo.canPersist}
          <div class="actions">
            <button type="button" class="btn btn-main" disabled={persistAsking} onclick={onRequestPersist}>
              {persistAsking ? '確認中…' : '永続化を頼む'}
            </button>
          </div>
          {#if persistDenied}
            <p class="note" role="status">許可されませんでした。ホーム画面に追加すると、データが消えにくくなります。</p>
          {:else}
            <p class="note">永続化すると、ブラウザが容量不足でもデータを自動では消しません。</p>
          {/if}
        {/if}
      </section>
    {/if}

    <!-- 同期 -->
    <section class="block" aria-labelledby="sec-sync">
      {@render head(RefreshCw, 'sec-sync', '同期', 'SYNC')}

      <dl class="facts">
        <div class="fact">
          <dt class="type-label">最終同期</dt>
          <dd>
            {#if syncStatus.running}<span class="note running">実行中</span>{/if}
            {#if syncStatus.lastSyncAt}
              <span class="mono">{formatJstDateTime(syncStatus.lastSyncAt)}</span>
            {:else}
              <span class="dim">未実施</span>
            {/if}
          </dd>
        </div>
      </dl>
      <div class="actions">
        <button type="button" class="btn btn-main" disabled={syncStatus.running} onclick={onSyncNow}>
          <RefreshCw size={14} aria-hidden="true" class={syncStatus.running ? 'spin' : ''} />
          {syncStatus.running ? '同期中…' : '今すぐ同期'}
        </button>
      </div>

      {@render sub('自動同期')}
      <div class="choice-group cols-3" role="group" aria-label="自動同期">
        {#each AUTO_SYNC_MODES as mode (mode)}
          {@const Icon = AUTO_SYNC_ICONS[mode]}
          <button
            type="button"
            class="choice-btn"
            aria-pressed={prefs.autoSync === mode}
            onclick={() => onSelectAutoSync(mode)}
          >
            <Icon size={18} aria-hidden="true" />
            <span>{AUTO_SYNC_LABELS[mode]}</span>
          </button>
        {/each}
      </div>
      <p class="note">{AUTO_SYNC_NOTES[prefs.autoSync]}</p>

      {@render sub('同期するソース')}
      <div class="toggles">
        {#each SYNC_SOURCES as source (source)}
          {@render toggle(
            `sync-source-${source}`,
            SYNC_SOURCE_LABELS[source].name,
            SYNC_SOURCE_LABELS[source].what,
            prefs.syncSources[source],
            (e) => onToggleSource(source, e),
          )}
        {/each}
      </div>
      {#if enabledSourceCount === 0}
        <p class="alert">
          <span class="dot" aria-hidden="true"></span>
          <span>ソースがすべて OFF です。同期しても発売・放送の予定は取りに行きません。</span>
        </p>
      {:else}
        <p class="note">OFF のソースは照会しません。前回までに取り込んだ予定はそのまま残ります。</p>
      {/if}

      {@render sub('アニメ化検知')}
      <div class="toggles">
        {@render toggle(
          'sync-detection',
          'アニメ化検知',
          '同期のあとに、原作や前の期から新しいアニメを探す',
          prefs.detection,
          onToggleDetection,
        )}
      </div>

      {@render sub('ソースごとの最後のエラー')}
      {#if syncErrorEntries.length > 0}
        <ul class="errors">
          {#each syncErrorEntries as [source, error] (source)}
            <li class="error-row">
              <span class="dot" aria-hidden="true"></span>
              <span class="error-body">
                <strong>{syncKeyLabel(source)}</strong>
                <span class="error-msg">{error.message}</span>
                <span class="mono note">{formatJstDateTime(error.occurredAt)}</span>
              </span>
            </li>
          {/each}
        </ul>
      {:else}
        <p class="note">エラーなし</p>
      {/if}
    </section>

    <!-- 新着 -->
    <section class="block" aria-labelledby="sec-unseen">
      {@render head(Inbox, 'sec-unseen', '新着', 'UNSEEN')}

      <dl class="facts">
        <div class="fact">
          <dt class="type-label">未読</dt>
          <dd>
            {#if libraryLoaded}
              <span><span class="mono">{formatCount(unseenIds.length)}</span> 件</span>
            {:else}
              <span class="mono dim">…</span>
            {/if}
          </dd>
        </div>
      </dl>

      {#if markConfirm && unseenIds.length > 0}
        <div class="confirm" role="group" aria-label="すべて既読にする確認">
          <p class="confirm-text">
            未読 <span class="mono">{formatCount(unseenIds.length)}</span> 件をすべて既読にします。Home の新着と NEW の印が消えます。
          </p>
          <div class="actions">
            <button type="button" class="btn btn-solid" disabled={marking} onclick={confirmMarkAllSeen}>
              {marking ? '既読にしています…' : '既読にする'}
            </button>
            <button type="button" class="btn" disabled={marking} onclick={() => (markConfirm = false)}>キャンセル</button>
          </div>
        </div>
      {:else}
        <div class="actions">
          <button
            type="button"
            class="btn btn-main"
            disabled={unseenIds.length === 0}
            onclick={() => {
              markConfirm = true;
              markMessage = null;
            }}
          >
            <CheckCheck size={14} aria-hidden="true" />すべて既読にする
          </button>
        </div>
      {/if}
      {#if markMessage}
        <p class="note" role="status">{markMessage}</p>
      {/if}
    </section>

    <!-- アニメ化検知 -->
    <section class="block" aria-labelledby="sec-detect">
      {@render head(Radar, 'sec-detect', 'アニメ化検知', 'DETECTED')}

      {#if !prefs.detection}
        <p class="note">検知は OFF です（同期の節で切り替えます）。却下の取り消しはできます。</p>
      {/if}

      {@render sub('却下した候補')}
      {#if !libraryLoaded}
        <p class="note mono">…</p>
      {:else if rejected.length === 0}
        <p class="note">却下した候補はありません。</p>
      {:else}
        <ul class="rows">
          {#each rejected as detection (detection.id)}
            {@const workTitle = workTitles.get(detection.workId)}
            <!-- 却下日を残す前に却下したものは、検知した日を出す -->
            {@const when = detection.rejectedAt
              ? { label: '却下', iso: detection.rejectedAt }
              : { label: '検知', iso: detection.createdAt }}
            <li class="row">
              <span class="row-body">
                <span class="row-title">{detection.candidate.title}</span>
                <span class="note">
                  {#if workTitle}<span>{workTitle}</span> · {/if}{when.label} <span class="mono">{jstDate(when.iso)}</span>
                </span>
              </span>
              <button
                type="button"
                class="btn btn-small"
                disabled={reopening !== null}
                aria-label={`「${detection.candidate.title}」の却下を取り消す`}
                onclick={() => onReopen(detection)}
              >
                <Undo2 size={13} aria-hidden="true" />{reopening === detection.id ? '戻しています…' : '取り消す'}
              </button>
            </li>
          {/each}
        </ul>
        <p class="note">取り消すと候補に戻り、Home のアニメ化検知にまた出ます。</p>
      {/if}
      {#if reopenMessage}
        <p class="note" role="status">{reopenMessage}</p>
      {/if}
    </section>
  </div>

  <div class="col">
    <!-- カタログ -->
    <section class="block" aria-labelledby="sec-catalog">
      {@render head(BookOpen, 'sec-catalog', 'カタログ', 'CATALOG')}

      {#if statsState.status === 'loading'}
        <p class="note mono">…</p>
      {:else if statsState.status === 'failed'}
        <p class="alert"><span class="dot" aria-hidden="true"></span><span>件数を取得できませんでした（collector に接続できません）</span></p>
      {:else if !statsState.stats.ready}
        <p class="note">カタログ未作成。collector 側で次のコマンドを実行すると作られます。</p>
        <code class="code mono">{BUILD_COMMAND}</code>
      {:else}
        <dl class="facts">
          <div class="fact">
            <dt class="type-label">作品</dt>
            <dd><span class="mono">{formatCount(statsState.stats.works)}</span></dd>
          </div>
          <div class="fact">
            <dt class="type-label">件数（全ソース）</dt>
            <dd><span class="mono">{formatCount(statsState.stats.entries)}</span></dd>
          </div>
          {#each sourceRows as row (row.source)}
            <div class="fact">
              <dt class="source-name">{row.label}</dt>
              <dd class="source-values">
                <span><span class="mono">{row.entries}</span> 件</span>
                {#if row.harvestedAt}
                  <span class="mono note">{row.harvestedAt}</span>
                {:else}
                  <span class="note">未収集</span>
                {/if}
                {#if row.errorSlices > 0}
                  <span class="error-inline"><span class="dot" aria-hidden="true"></span>失敗 <span class="mono">{row.errorSlices}</span></span>
                {/if}
              </dd>
            </div>
          {/each}
        </dl>
      {/if}

      {@render sub('差分更新')}
      <div class="actions">
        <button
          type="button"
          class="btn btn-main"
          disabled={jobStarting || jobRunning}
          onclick={onStartJob}
        >
          <RotateCw size={14} aria-hidden="true" class={jobRunning ? 'spin' : ''} />
          {jobRunning ? '実行中…' : jobStarting ? '開始しています…' : '差分更新を実行'}
        </button>
        <span class="note">目安 約{RECENT_JOB_MINUTES}分</span>
      </div>
      {#if jobNotice}
        {#if jobNotice.kind === 'busy'}
          <p class="busy" role="status"><Timer size={14} aria-hidden="true" /><span>{jobNotice.text}</span></p>
        {:else}
          <p class="alert" role="alert"><span class="dot" aria-hidden="true"></span><span>{jobNotice.text}</span></p>
        {/if}
      {/if}
      {#if job && jobResult}
        <div class="job" class:job-done={!jobRunning}>
          <p class="job-status" role="status">
            {#if jobResult.kind === 'ok'}
              <Check size={14} aria-hidden="true" />
            {:else if jobResult.kind === 'error'}
              <span class="dot" aria-hidden="true"></span>
            {/if}
            <span>{jobResult.text}</span>
          </p>
          <p class="note">
            開始 <span class="mono">{formatJstDateTime(job.startedAt)}</span>
            · {jobRunning ? '経過' : '所要'} <span class="mono">{jobElapsed(job, jobNow)}</span>
          </p>
          {#if jobLog.length > 0 && (jobRunning || jobResult.kind === 'error')}
            <pre class="log mono">{jobLog.join('\n')}</pre>
          {/if}
        </div>
      {:else}
        <p class="note">新しい刊行・今年以降のアニメ・上位の Web 小説だけを取り直します。終わると件数を読み直します。</p>
      {/if}
    </section>

    <!-- collector 接続 -->
    <section class="block" aria-labelledby="sec-collector">
      {@render head(Plug, 'sec-collector', 'collector 接続', 'COLLECTOR')}

      <div class="field-group">
        <label class="type-label" for="collector-url">接続先 URL</label>
        <input
          id="collector-url"
          class="field"
          type="url"
          inputmode="url"
          autocomplete="off"
          autocapitalize="off"
          spellcheck="false"
          placeholder="http://127.0.0.1:8000"
          bind:value={apiBase}
          onblur={onApiBaseBlur}
        />
        <p class="note">空欄のときは、このアプリと同じ場所に接続します。</p>
      </div>

      <div class="field-group">
        <label class="type-label" for="collector-token">Bearer トークン</label>
        <div class="token-row">
          <input
            id="collector-token"
            type="password"
            autocomplete="off"
            class="field"
            placeholder={tokenConfigured ? '設定済み（変更する場合のみ入力）' : '未設定'}
            bind:value={tokenDraft}
          />
          <button type="button" class="btn btn-main" disabled={!tokenDraft.trim()} onclick={onTokenSave}>保存</button>
          {#if tokenConfigured}
            <button type="button" class="btn btn-quiet" onclick={onTokenClear}>解除</button>
          {/if}
        </div>
      </div>

      <dl class="facts">
        <div class="fact">
          <dt class="type-label">トークン</dt>
          <dd>{#if tokenConfigured}設定済み{:else}<span class="dim">未設定</span>{/if}</dd>
        </div>
      </dl>
    </section>

    <!-- 表示 -->
    <section class="block" aria-labelledby="sec-display">
      {@render head(Palette, 'sec-display', '表示', 'DISPLAY')}

      {@render sub('テーマ')}
      <div class="choice-group cols-3" role="group" aria-label="テーマ">
        {#each THEME_PREFS as pref (pref)}
          {@const Icon = THEME_ICONS[pref]}
          <button type="button" class="choice-btn" aria-pressed={themePref === pref} onclick={() => onSelectTheme(pref)}>
            <Icon size={18} aria-hidden="true" />
            <span>{THEME_LABELS[pref]}</span>
          </button>
        {/each}
      </div>
      {#if themePref === 'system'}
        <p class="note">端末の設定に合わせて、自動で切り替わります。</p>
      {/if}

      {@render sub('起動時に開く画面')}
      <div class="choice-group cols-4" role="group" aria-label="起動時に開く画面">
        {#each START_SCREENS as screen (screen)}
          {@const Icon = START_SCREEN_ICONS[screen]}
          <button
            type="button"
            class="choice-btn"
            aria-pressed={prefs.startScreen === screen}
            onclick={() => onSelectStartScreen(screen)}
          >
            <Icon size={18} aria-hidden="true" />
            <span>{START_SCREEN_LABELS[screen]}</span>
          </button>
        {/each}
      </div>
      <p class="note">ホーム画面のアイコンなどから開いた時に使います。共有されたリンクやリロードでは、その画面のままです。</p>

      {@render sub('深夜の時刻')}
      <div class="choice-group cols-2" role="group" aria-label="深夜の時刻">
        <button
          type="button"
          class="choice-btn"
          aria-pressed={prefs.timeNotation === 'late-night'}
          onclick={() => preferences.set('timeNotation', 'late-night')}
        >
          <span class="mono">25:30</span>
          <span>放送日のまま</span>
        </button>
        <button
          type="button"
          class="choice-btn"
          aria-pressed={prefs.timeNotation === 'clock'}
          onclick={() => preferences.set('timeNotation', 'clock')}
        >
          <span class="mono">翌 1:30</span>
          <span>実際の時刻</span>
        </button>
      </div>
      <p class="note">深夜番組の時刻の書き方です。並びや保存している日付は変わりません。</p>

      {@render sub('週の始まり')}
      <div class="choice-group cols-2" role="group" aria-label="週の始まり">
        <button type="button" class="choice-btn" aria-pressed={prefs.weekStart === 'sun'} onclick={() => preferences.set('weekStart', 'sun')}>
          <span>日曜から</span>
        </button>
        <button type="button" class="choice-btn" aria-pressed={prefs.weekStart === 'mon'} onclick={() => preferences.set('weekStart', 'mon')}>
          <span>月曜から</span>
        </button>
      </div>
      <p class="note">カレンダーの月の升目に使います。</p>
    </section>

    <!-- この app について -->
    <section class="block" aria-labelledby="sec-about">
      {@render head(Info, 'sec-about', 'この app について', 'ABOUT')}

      {@render sub('データの出典')}
      <ul class="rows">
        {#each DATA_SOURCES as source (source.url)}
          <li class="row source-row">
            <span class="row-body">
              <a class="ext-link" href={source.url} target="_blank" rel="noopener noreferrer">
                {source.name}<ExternalLink size={12} aria-hidden="true" />
              </a>
              <span class="note">{source.note}</span>
            </span>
          </li>
        {/each}
      </ul>
      <p class="note">データは collector を通して各サービスから取り、この端末にだけ保存します。</p>

      <dl class="facts">
        <div class="fact">
          <dt class="type-label">版</dt>
          <dd><span class="mono">{build.version}</span></dd>
        </div>
        <div class="fact">
          <dt class="type-label">ビルド</dt>
          <dd>
            {#if build.builtAt}
              <span class="mono">{formatJstDateTime(build.builtAt)}</span>
            {:else}
              <span class="dim">—</span>
            {/if}
          </dd>
        </div>
      </dl>
    </section>
  </div>
</section>

<style>
  /* 余白は .page が持つ（ここで持つと PC の余白を上書きしてしまう） */
  .settings {
    display: flex;
    flex-direction: column;
    gap: 1.75rem;
    min-width: 0;
  }
  .title {
    margin: 0;
  }
  /* 列: スマホでは今まで通り節が縦に1本並ぶだけ */
  .col {
    display: flex;
    flex-direction: column;
    gap: 1.75rem;
    min-width: 0;
  }
  .block {
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
    min-width: 0;
  }

  /* ─── 節見出し（アイコン付きの .shou） ─── */
  .sec-head {
    align-items: center;
    margin-bottom: 0;
    padding-bottom: 0.5rem;
    min-width: 0;
  }
  .sec-head h2 {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    min-width: 0;
    font-size: 0.85rem;
    white-space: nowrap;
  }
  .sec-head :global(svg) {
    flex-shrink: 0;
  }
  /* 節の中の小見出し（活字ラベル） */
  .sub-head {
    margin: 0.5rem 0 -0.25rem;
  }
  .sub-block {
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
    min-width: 0;
  }

  /* ─── 項目と値の行（罫線で区切る） ─── */
  .facts {
    display: flex;
    flex-direction: column;
    margin: 0;
  }
  .fact {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 0.75rem;
    padding: 0.5rem 0;
    border-bottom: 1px solid var(--line);
    font-size: 0.75rem;
    min-width: 0;
  }
  .fact dt {
    flex-shrink: 0;
  }
  .fact dd {
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    justify-content: flex-end;
    gap: 0.25rem 0.5rem;
    margin: 0;
    min-width: 0;
    text-align: right;
    overflow-wrap: anywhere;
  }
  .source-name {
    font-weight: 500;
  }
  .error-inline {
    display: inline-flex;
    align-items: center;
    gap: 0.3rem;
    white-space: nowrap;
  }
  .dim {
    color: var(--muted);
  }
  .note {
    margin: 0;
    font-size: 0.6875rem;
    line-height: 1.5;
    color: var(--muted);
  }
  .code {
    display: block;
    padding: 0.375rem 0.5rem;
    border: 1px solid var(--line);
    font-size: 0.75rem;
    overflow-wrap: anywhere;
  }

  /* ─── 注意（黄は使わず、ON AIR の赤い点と文字で） ─── */
  .dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--onair);
    flex-shrink: 0;
  }
  .alert {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 0.25rem 0.5rem;
    margin: 0;
    font-size: 0.75rem;
    overflow-wrap: anywhere;
  }
  .notice {
    display: flex;
    align-items: flex-start;
    gap: 0.5rem;
    margin: 0;
    padding: 0.5rem 0.625rem;
    border: 1px solid var(--fg);
    font-size: 0.75rem;
    overflow-wrap: anywhere;
  }
  .notice :global(svg) {
    flex-shrink: 0;
    margin-top: 0.15rem;
  }
  .notice .dot {
    margin-top: 0.45rem;
  }
  .errors {
    list-style: none;
    margin: 0;
    padding: 0;
  }
  .error-row {
    display: flex;
    align-items: flex-start;
    gap: 0.5rem;
    padding: 0.5rem 0;
    border-bottom: 1px solid var(--line);
    font-size: 0.75rem;
    min-width: 0;
  }
  .error-row .dot {
    margin-top: 0.45rem;
  }
  .error-body {
    display: flex;
    flex-direction: column;
    gap: 0.1rem;
    min-width: 0;
    overflow-wrap: anywhere;
  }
  .error-msg {
    line-height: 1.5;
  }
  .running {
    color: var(--fg);
  }
  /* 別の更新が実行中（失敗ではないので赤い点は付けない） */
  .busy {
    display: flex;
    align-items: flex-start;
    gap: 0.5rem;
    margin: 0;
    font-size: 0.75rem;
    line-height: 1.5;
    overflow-wrap: anywhere;
  }
  .busy :global(svg) {
    flex-shrink: 0;
    margin-top: 0.1rem;
  }

  /* ─── 一覧の行（却下した候補・出典） ─── */
  .rows {
    list-style: none;
    margin: 0;
    padding: 0;
  }
  .row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.75rem;
    padding: 0.5rem 0;
    border-bottom: 1px solid var(--line);
    font-size: 0.75rem;
    min-width: 0;
  }
  .row-body {
    display: flex;
    flex-direction: column;
    gap: 0.1rem;
    min-width: 0;
    overflow-wrap: anywhere;
  }
  .row-title {
    font-weight: 500;
    line-height: 1.4;
  }
  .ext-link {
    display: inline-flex;
    align-items: center;
    gap: 0.3rem;
    color: var(--fg);
    font-weight: 500;
    text-decoration: underline;
    text-decoration-color: var(--line);
    text-underline-offset: 0.2em;
  }
  .ext-link:hover {
    text-decoration-color: var(--fg);
  }
  .ext-link :global(svg) {
    flex-shrink: 0;
    color: var(--muted);
  }

  /* ─── 確認（インポート・既読・Library を空に） ─── */
  .confirm {
    display: flex;
    flex-direction: column;
    gap: 0.625rem;
    padding: 0.625rem;
    border: 1px solid var(--fg);
  }
  .confirm-text {
    margin: 0;
    font-size: 0.75rem;
    line-height: 1.6;
    overflow-wrap: anywhere;
  }

  /* ─── 差分更新のジョブ ─── */
  .job {
    display: flex;
    flex-direction: column;
    gap: 0.375rem;
    padding: 0.625rem;
    border: 1px solid var(--fg);
    min-width: 0;
  }
  .job-done {
    border-color: var(--line);
  }
  .job-status {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    margin: 0;
    font-size: 0.75rem;
    font-weight: 500;
  }
  .job-status :global(svg) {
    flex-shrink: 0;
  }
  .log {
    margin: 0;
    padding: 0.375rem 0.5rem;
    border: 1px solid var(--line);
    font-size: 0.6875rem;
    line-height: 1.5;
    color: var(--muted);
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }

  /* ─── 入力 ─── */
  .field-group {
    display: flex;
    flex-direction: column;
    gap: 0.35rem;
    min-width: 0;
  }
  .field {
    width: 100%;
    min-width: 0;
    min-height: 2.25rem;
    padding: 0.25rem 0.5rem;
    /* 16px 未満だと iOS が入力時に画面を拡大する */
    font-size: 1rem;
    border: 1px solid var(--line);
    border-radius: 0;
    background: var(--bg);
    color: var(--fg);
  }
  .token-row {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.5rem;
    min-width: 0;
  }
  .token-row .field {
    flex: 1 1 10rem;
    width: auto;
  }

  /* ─── ON/OFF（四角いスイッチ。実体は見えないチェックボックス） ─── */
  .toggles {
    display: flex;
    flex-direction: column;
  }
  .toggle-row {
    position: relative;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.75rem;
    min-height: 2.75rem;
    padding: 0.375rem 0;
    border-bottom: 1px solid var(--line);
    font-size: 0.75rem;
    cursor: pointer;
    min-width: 0;
  }
  .toggle-text {
    display: flex;
    flex-direction: column;
    gap: 0.05rem;
    min-width: 0;
  }
  .toggle-name {
    font-weight: 500;
  }
  .switch-input {
    position: absolute;
    width: 1px;
    height: 1px;
    margin: 0;
    padding: 0;
    overflow: hidden;
    clip: rect(0 0 0 0);
    white-space: nowrap;
    border: 0;
    opacity: 0;
  }
  .switch {
    position: relative;
    flex-shrink: 0;
    width: 2.5rem;
    height: 1.375rem;
    border: 1px solid var(--muted);
    background: transparent;
    transition:
      background 0.2s ease,
      border-color 0.2s ease;
  }
  .switch::after {
    content: '';
    position: absolute;
    top: 3px;
    left: 3px;
    width: calc(1.375rem - 8px);
    height: calc(1.375rem - 8px);
    background: var(--muted);
    transition:
      transform 0.2s ease,
      background 0.2s ease;
  }
  .switch-input:checked + .switch {
    border-color: var(--fg);
    background: var(--fg);
  }
  .switch-input:checked + .switch::after {
    background: var(--bg);
    transform: translateX(calc(2.5rem - 1.375rem));
  }
  .switch-input:focus-visible + .switch {
    outline: 2px solid var(--fg);
    outline-offset: 2px;
  }

  /* ─── ボタン ─── */
  .actions {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.5rem;
  }
  .btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 0.35rem;
    min-height: 2.25rem;
    padding: 0.125rem 0.75rem;
    font-size: 0.75rem;
    border: 1px solid var(--line);
    color: var(--fg);
    background: transparent;
    text-decoration: none;
    white-space: nowrap;
  }
  .btn:disabled {
    opacity: 0.5;
  }
  .btn-main {
    border-color: var(--fg);
  }
  .btn-solid {
    border-color: var(--fg);
    background: var(--fg);
    color: var(--bg);
  }
  .btn-quiet {
    color: var(--muted);
  }
  .btn-small {
    flex-shrink: 0;
    min-height: 2rem;
    padding: 0.125rem 0.5rem;
    border-color: var(--fg);
  }
  .btn:focus-visible,
  .choice-btn:focus-visible,
  .ext-link:focus-visible {
    outline: 2px solid var(--fg);
    outline-offset: 2px;
  }
  .btn :global(.spin) {
    animation: spin 1s linear infinite;
  }
  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
  }

  /* ─── 択一（テーマ・自動同期・起動時の画面） ─── */
  .choice-group {
    display: grid;
  }
  .cols-2 {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .cols-3 {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
  .cols-4 {
    grid-template-columns: repeat(4, minmax(0, 1fr));
  }
  .choice-btn {
    position: relative;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 0.3rem;
    min-height: 3.5rem;
    padding: 0.5rem 0.125rem;
    border: 1px solid var(--line);
    color: var(--muted);
    background: transparent;
    font-size: 0.75rem;
    line-height: 1.3;
    text-align: center;
    text-wrap: balance;
    overflow-wrap: anywhere;
    transition:
      color 0.2s ease,
      background 0.2s ease;
  }
  .choice-btn + .choice-btn {
    margin-left: -1px;
  }
  .choice-btn:hover {
    color: var(--fg);
  }
  .choice-btn[aria-pressed='true'] {
    z-index: 1;
    border-color: var(--fg);
    background: var(--fg);
    color: var(--bg);
  }
  .choice-btn:focus-visible {
    z-index: 2;
  }

  /* PC: 節を左右の列に固定で振る（grid の行揃えだと短い節の下に隙間ができ、CSS columns だと節が列をまたいで動く） */
  @media (min-width: 64rem) {
    .settings {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      column-gap: 3rem;
      row-gap: 1.75rem;
      align-items: start;
    }
    .title {
      grid-column: 1 / -1;
    }
  }

  @media (prefers-reduced-motion: reduce) {
    .choice-btn,
    .switch,
    .switch::after {
      transition: none;
    }
    .btn :global(.spin) {
      animation: none;
    }
  }
</style>
