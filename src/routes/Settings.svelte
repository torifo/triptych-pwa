<script lang="ts">
  import { onMount } from 'svelte';
  import {
    Check,
    Database,
    Download,
    HardDrive,
    Monitor,
    Moon,
    Palette,
    Plug,
    RefreshCw,
    Sun,
    Upload,
    type LucideIcon,
  } from '@lucide/svelte';
  import { exportAll, importAll, getLastExportAt, PortabilityError } from '../lib/db/portability';
  import { TOKEN_KEY, getApiBase, setApiBase, setApiToken } from '../lib/api';
  import { syncStatus } from '../lib/sync/status.svelte';
  import { formatJstDateTime } from '../lib/catalog/view';
  import { syncKeyLabel } from '../lib/components/labels';
  import {
    EXPORT_STALE_DAYS,
    formatUsage,
    hasStorageApi,
    isExportStale,
    readStorageInfo,
    requestPersistent,
    type StorageInfo,
  } from '../lib/components/settings-view';
  import {
    THEME_LABELS,
    THEME_PREFS,
    applyTheme,
    loadThemePref,
    saveThemePref,
    type ThemePref,
  } from '../lib/theme';

  const THEME_ICONS: Record<ThemePref, LucideIcon> = { light: Sun, dark: Moon, system: Monitor };

  let lastExportAt = $state<string | undefined>(undefined);
  let apiBase = $state(getApiBase());
  let tokenConfigured = $state(!!localStorage.getItem(TOKEN_KEY));
  let tokenDraft = $state('');
  let themePref = $state<ThemePref>(loadThemePref());

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

  async function refreshLastExportAt() {
    lastExportAt = await getLastExportAt();
  }

  async function refreshStorage() {
    storageInfo = await readStorageInfo(storageApi);
  }

  onMount(() => {
    void refreshLastExportAt();
    if (showStorage) void refreshStorage();
  });

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
</script>

{#snippet head(Icon: LucideIcon, id: string, title: string, kicker: string)}
  <div class="shou sec-head">
    <h2 {id}><Icon size={15} strokeWidth={2} aria-hidden="true" />{title}</h2>
    <span class="no" aria-hidden="true">{kicker}</span>
  </div>
{/snippet}

<section class="settings">
  <h1 class="page-title title">Settings<span class="type-label" style="margin-left: 0.8em;">設定とデータ</span></h1>

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

    <div class="theme-group" role="group" aria-label="テーマ">
      {#each THEME_PREFS as pref (pref)}
        {@const Icon = THEME_ICONS[pref]}
        <button type="button" class="theme-btn" aria-pressed={themePref === pref} onclick={() => onSelectTheme(pref)}>
          <Icon size={18} aria-hidden="true" />
          <span>{THEME_LABELS[pref]}</span>
        </button>
      {/each}
    </div>
    {#if themePref === 'system'}
      <p class="note">端末の設定に合わせて、自動で切り替わります。</p>
    {/if}
  </section>
</section>

<style>
  .settings {
    display: flex;
    flex-direction: column;
    gap: 1.75rem;
    padding: 1rem;
    min-width: 0;
  }
  .title {
    margin: 0;
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
  .dim {
    color: var(--muted);
  }
  .note {
    margin: 0;
    font-size: 0.6875rem;
    line-height: 1.5;
    color: var(--muted);
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

  /* ─── インポートの確認 ─── */
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
  .btn:focus-visible,
  .theme-btn:focus-visible {
    outline: 2px solid var(--fg);
    outline-offset: 2px;
  }

  /* ─── テーマの3択 ─── */
  .theme-group {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
  .theme-btn {
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
    transition:
      color 0.2s ease,
      background 0.2s ease;
  }
  .theme-btn + .theme-btn {
    margin-left: -1px;
  }
  .theme-btn:hover {
    color: var(--fg);
  }
  .theme-btn[aria-pressed='true'] {
    z-index: 1;
    border-color: var(--fg);
    background: var(--fg);
    color: var(--bg);
  }
  .theme-btn:focus-visible {
    z-index: 2;
  }

  @media (prefers-reduced-motion: reduce) {
    .theme-btn {
      transition: none;
    }
  }
</style>
