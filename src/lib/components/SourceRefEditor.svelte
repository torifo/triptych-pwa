<script lang="ts">
  import type { Edition, SourceRef } from '../db/schema';
  import { apiGet, ApiError } from '../api';
  import { addSourceRef, removeSourceRef } from '../db/store';
  import { SOURCE_LABELS } from './labels';
  import {
    directSourceRef,
    isSafeHttpUrl,
    sourceRefKey,
    type ResolvableResult,
  } from './release-view';
  import { sourceRefSyncNote } from './source-ref-sync';

  let { edition, onchange }: { edition: Edition; onchange: () => void } = $props();

  interface ResolveResponse {
    results: ResolvableResult[];
    errors?: { source: string; message: string }[];
  }

  let input = $state('');
  let busy = $state(false);
  let message = $state<{ kind: 'ok' | 'error'; text: string } | null>(null);

  async function onAdd(event?: Event) {
    event?.preventDefault();
    const q = input.trim();
    if (!q || busy) return;
    busy = true;
    message = null;
    try {
      // URL・ncode・ISBN は検索せずに直接解決される（direct: true）
      const data = await apiGet<ResolveResponse>('/api/search', { q });
      const ref = directSourceRef(data.results);
      if (!ref) {
        message = { kind: 'error', text: '解決できませんでした' };
        return;
      }
      if (edition.sourceRefs.some((r) => sourceRefKey(r.source, r.externalId) === sourceRefKey(ref.source, ref.externalId))) {
        message = { kind: 'error', text: '追加済みのソースです' };
        return;
      }
      await addSourceRef(edition.id, ref);
      input = '';
      message = {
        kind: 'ok',
        text: `${SOURCE_LABELS[ref.source]} を追加しました。${sourceRefSyncNote(edition, ref.source)}`,
      };
      onchange();
    } catch (err) {
      message = {
        kind: 'error',
        text: err instanceof ApiError ? `解決できませんでした (${err.status})` : '解決できませんでした',
      };
    } finally {
      busy = false;
    }
  }

  async function onRemove(ref: SourceRef) {
    if (busy) return;
    busy = true;
    message = null;
    try {
      await removeSourceRef(edition.id, ref);
      onchange();
    } finally {
      busy = false;
    }
  }
</script>

<div class="flex flex-col gap-1.5" style="min-width: 0;">
  <span class="type-label">ソース</span>

  {#if edition.sourceRefs.length === 0}
    <p class="text-[11px]" style="color: var(--muted);">ソースが未設定です</p>
  {:else}
    <div style="border-top: 1px solid var(--line); min-width: 0;">
      {#each edition.sourceRefs as ref (sourceRefKey(ref.source, ref.externalId))}
        <div
          class="flex items-center gap-2 py-1"
          style="border-bottom: 1px solid var(--line); min-width: 0;"
        >
          <span class="text-[11px] font-bold" style="white-space: nowrap;">{SOURCE_LABELS[ref.source] ?? ref.source}</span>
          <span class="min-w-0 flex-1 truncate text-[11px]" style="color: var(--muted);" title={ref.externalId}>
            {ref.externalId}
          </span>
          {#if isSafeHttpUrl(ref.url)}
            <a
              class="link text-[11px]"
              href={ref.url}
              target="_blank"
              rel="noopener noreferrer"
              aria-label={`${SOURCE_LABELS[ref.source] ?? ref.source} の ${ref.externalId} を開く`}
            >
              開く
            </a>
          {/if}
          <button
            type="button"
            class="btn"
            disabled={busy}
            aria-label={`${SOURCE_LABELS[ref.source] ?? ref.source} の ${ref.externalId} を削除`}
            onclick={() => onRemove(ref)}
          >
            削除
          </button>
        </div>
      {/each}
    </div>
  {/if}

  <form class="flex items-center gap-1.5" onsubmit={onAdd}>
    <input
      class="field"
      placeholder="URL・ncode・ISBN を貼って追加"
      aria-label="ソースの URL・ncode・ISBN"
      bind:value={input}
    />
    <button type="submit" class="btn btn-main" disabled={!input.trim() || busy}>
      {busy ? '解決中…' : '追加'}
    </button>
  </form>

  {#if message}
    <p
      class="text-[11px]"
      style={`color: ${message.kind === 'ok' ? 'var(--muted)' : 'var(--fg)'};`}
      role={message.kind === 'error' ? 'alert' : 'status'}
    >
      {message.text}
    </p>
  {/if}
</div>

<style>
  .field {
    flex: 1;
    min-width: 0;
    padding: 0.25rem 0.375rem;
    font-size: 0.75rem;
    border: 1px solid var(--line);
    background: var(--bg);
    color: var(--fg);
    border-radius: 0;
  }
  .btn {
    padding: 0.125rem 0.5rem;
    font-size: 0.6875rem;
    border: 1px solid var(--line);
    color: var(--fg);
    background: transparent;
    white-space: nowrap;
  }
  .btn-main {
    padding: 0.25rem 0.5rem;
    border-color: var(--fg);
  }
  .btn:disabled {
    color: var(--muted);
  }
  :global(.dark) .field {
    color-scheme: dark;
  }
  .link {
    white-space: nowrap;
    color: var(--fg);
    text-decoration: underline;
    text-underline-offset: 2px;
  }
</style>
