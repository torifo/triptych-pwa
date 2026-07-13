<script module lang="ts">
  // アプリ起動時（このモジュールが最初に評価された時点）にダークモード設定を復元する。
  // App.svelte はこのコンポーネントを静的 import しているため、Settings 画面を
  // 開いていなくても起動時に一度だけ実行される。
  const DARK_MODE_KEY = 'triptych.darkMode';

  if (typeof document !== 'undefined') {
    const stored = localStorage.getItem(DARK_MODE_KEY);
    if (stored === '1') document.documentElement.classList.add('dark');
  }
</script>

<script lang="ts">
  import { onMount } from 'svelte';
  import { exportAll, importAll, getLastExportAt, PortabilityError } from '../lib/db/portability';
  import { getApiBase, setApiBase, setApiToken } from '../lib/api';
  import { syncStatus } from '../lib/sync/status.svelte';
  import type { Source } from '../lib/db/schema';

  const SOURCE_LABELS: Record<Source, string> = {
    anilist: 'AniList',
    narou: 'なろう',
    openbd: 'openBD',
    ndl: 'NDL Search',
    syoboi: 'しょぼかる',
    kakuyomu: 'カクヨム',
    manual: '手動',
  };

  const EXPORT_STALE_DAYS = 30;
  const TOKEN_KEY = 'triptych.apiToken';

  let lastExportAt = $state<string | undefined>(undefined);
  let apiBase = $state(getApiBase());
  let tokenConfigured = $state(!!localStorage.getItem(TOKEN_KEY));
  let tokenDraft = $state('');
  let darkMode = $state(document.documentElement.classList.contains('dark'));

  let exporting = $state(false);
  let importing = $state(false);
  let importPending = $state<{ file: File; text: string } | null>(null);
  let statusMessage = $state<{ kind: 'ok' | 'error'; text: string } | null>(null);

  let fileInput = $state<HTMLInputElement | null>(null);

  const exportStale = $derived.by(() => {
    if (!lastExportAt) return true;
    const elapsedMs = Date.now() - new Date(lastExportAt).getTime();
    return elapsedMs > EXPORT_STALE_DAYS * 24 * 60 * 60 * 1000;
  });

  const syncErrorEntries = $derived(
    Object.entries(syncStatus.errors) as [Source, { message: string; occurredAt: string }][],
  );

  async function refreshLastExportAt() {
    lastExportAt = await getLastExportAt();
  }

  onMount(refreshLastExportAt);

  function formatDateTime(iso: string): string {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleString('ja-JP');
  }

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

  function onToggleDarkMode() {
    darkMode = !darkMode;
    document.documentElement.classList.toggle('dark', darkMode);
    localStorage.setItem(DARK_MODE_KEY, darkMode ? '1' : '0');
  }
</script>

<section class="flex flex-col gap-5 p-4">
  <h1 class="page-title">Settings<span class="type-label" style="margin-left: 0.8em;">設定とデータ</span></h1>

  {#if statusMessage}
    <p
      class="px-2 py-1.5 text-xs"
      style={`border: 1px solid var(--fg); color: var(--fg); background: ${statusMessage.kind === 'ok' ? 'var(--hi-soft)' : 'transparent'};`}
    >
      {statusMessage.text}
    </p>
  {/if}

  <!-- データ -->
  <div class="flex flex-col gap-2" style="border-top: 1px solid var(--line); padding-top: 12px;">
    <h2 class="text-xs font-bold tracking-wide" style="color: var(--muted);">データ</h2>

    <div class="flex flex-wrap items-center gap-2">
      <button
        type="button"
        class="px-2 py-1 text-xs"
        style="border: 1px solid var(--fg); color: var(--fg); white-space: nowrap;"
        disabled={exporting}
        onclick={onExport}
      >
        {exporting ? 'エクスポート中…' : 'JSONエクスポート'}
      </button>

      <button
        type="button"
        class="px-2 py-1 text-xs"
        style="border: 1px solid var(--fg); color: var(--fg); white-space: nowrap;"
        onclick={() => fileInput?.click()}
      >
        インポート
      </button>
      <input
        bind:this={fileInput}
        type="file"
        accept="application/json"
        class="hidden"
        onchange={onFileSelected}
      />
    </div>

    <p class="text-[11px]" style={`color: ${exportStale ? 'var(--fg)' : 'var(--muted)'};`}>
      最終エクスポート: {lastExportAt ? formatDateTime(lastExportAt) : '未実施'}
      {#if exportStale}
        <span class="font-bold" style="color: var(--fg); background: var(--hi-soft); padding: 0 4px;">
          要エクスポート
        </span>
      {/if}
    </p>

    {#if importPending}
      <div class="flex flex-col gap-2 p-2" style="border: 1px solid var(--fg);">
        <p class="text-xs">
          「{importPending.file.name}」で全データを置き換えます。現在のデータは失われます。よろしいですか？
        </p>
        <div class="flex gap-2">
          <button
            type="button"
            class="px-2 py-1 text-xs"
            style="border: 1px solid var(--fg); color: var(--bg); background: var(--fg); white-space: nowrap;"
            disabled={importing}
            onclick={confirmImport}
          >
            {importing ? 'インポート中…' : '置き換える'}
          </button>
          <button
            type="button"
            class="px-2 py-1 text-xs"
            style="border: 1px solid var(--line); color: var(--fg); white-space: nowrap;"
            disabled={importing}
            onclick={cancelImport}
          >
            キャンセル
          </button>
        </div>
      </div>
    {/if}
  </div>

  <!-- 同期 -->
  <div class="flex flex-col gap-2" style="border-top: 1px solid var(--line); padding-top: 12px;">
    <h2 class="text-xs font-bold tracking-wide" style="color: var(--muted);">同期</h2>
    <p class="text-[11px]" style="color: var(--muted);">
      最終同期: {syncStatus.lastSyncAt ? formatDateTime(syncStatus.lastSyncAt) : '未実施'}
      {#if syncStatus.running}(実行中){/if}
    </p>
    {#if syncErrorEntries.length > 0}
      <div class="flex flex-col gap-1">
        {#each syncErrorEntries as [source, error] (source)}
          <p class="text-[11px]" style="border: 1px solid var(--line); padding: 4px 6px;">
            <span class="font-bold">{SOURCE_LABELS[source]}</span>:
            {error.message}
            <span style="color: var(--muted);"> ({formatDateTime(error.occurredAt)})</span>
          </p>
        {/each}
      </div>
    {:else}
      <p class="text-[11px]" style="color: var(--muted);">エラーなし</p>
    {/if}
  </div>

  <!-- collector 接続 -->
  <div class="flex flex-col gap-2" style="border-top: 1px solid var(--line); padding-top: 12px;">
    <h2 class="text-xs font-bold tracking-wide" style="color: var(--muted);">collector 接続</h2>

    <label class="flex flex-col gap-1 text-[11px]" style="color: var(--muted);">
      接続先 URL
      <input
        class="px-1.5 py-1 text-sm"
        style="border: 1px solid var(--line); background: var(--bg); color: var(--fg);"
        placeholder="http://127.0.0.1:8000（空欄=同一オリジン）"
        bind:value={apiBase}
        onblur={onApiBaseBlur}
      />
    </label>

    <label class="flex flex-col gap-1 text-[11px]" style="color: var(--muted);">
      Bearer トークン
      <div class="flex items-center gap-2">
        <input
          type="password"
          class="min-w-0 flex-1 px-1.5 py-1 text-sm"
          style="border: 1px solid var(--line); background: var(--bg); color: var(--fg);"
          placeholder={tokenConfigured ? '設定済み（変更する場合のみ入力）' : '未設定'}
          bind:value={tokenDraft}
        />
        <button
          type="button"
          class="px-2 py-1 text-xs"
          style="border: 1px solid var(--fg); color: var(--fg); white-space: nowrap;"
          disabled={!tokenDraft.trim()}
          onclick={onTokenSave}
        >
          保存
        </button>
        {#if tokenConfigured}
          <button
            type="button"
            class="px-2 py-1 text-xs"
            style="border: 1px solid var(--line); color: var(--muted); white-space: nowrap;"
            onclick={onTokenClear}
          >
            解除
          </button>
        {/if}
      </div>
    </label>
    <p class="text-[11px]" style="color: var(--muted);">
      状態: {tokenConfigured ? '設定済み' : '未設定'}
    </p>
  </div>

  <!-- 表示 -->
  <div class="flex flex-col gap-2" style="border-top: 1px solid var(--line); padding-top: 12px;">
    <h2 class="text-xs font-bold tracking-wide" style="color: var(--muted);">表示</h2>
    <button
      type="button"
      class="self-start px-2 py-1 text-xs"
      style={`border: 1px solid var(--fg); white-space: nowrap; color: ${darkMode ? 'var(--bg)' : 'var(--fg)'}; background: ${darkMode ? 'var(--fg)' : 'transparent'};`}
      onclick={onToggleDarkMode}
    >
      {darkMode ? 'ダークモード: ON' : 'ダークモード: OFF'}
    </button>
  </div>
</section>
