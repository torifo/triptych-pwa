<script lang="ts">
  import { ApiError } from '../lib/api';
  import { addEntriesToLibrary } from '../lib/catalog/add';
  import { fetchCatalogWork } from '../lib/catalog/api';
  import { recalledListHash } from '../lib/catalog/query';
  import {
    defaultSelection,
    findExistingWork,
    isRegistered,
    ndlSeriesName,
    registeredKeys,
  } from '../lib/catalog/register';
  import type { CatalogEntry, CatalogKind, CatalogWorkDetail } from '../lib/catalog/types';
  import { KIND_CODES, KIND_LABELS, KIND_ORDER } from '../lib/catalog/view';
  import CatalogEntryRow from '../lib/components/CatalogEntryRow.svelte';
  import CatalogKindBadge from '../lib/components/CatalogKindBadge.svelte';
  import FilterChip from '../lib/components/FilterChip.svelte';
  import type { Edition, Work } from '../lib/db/schema';
  import { listEditions, listWorks } from '../lib/db/store';
  import { navigate } from '../lib/router.svelte';

  let { id }: { id: string } = $props();

  /** 1つの面で最初に並べる行数。超えた分は「ほかN件を表示」で開く。 */
  const ROW_LIMIT = 10;

  let detail = $state<CatalogWorkDetail | null>(null);
  let loading = $state(true);
  let loadError = $state<{ missing: boolean; message: string } | null>(null);
  let works = $state<Work[]>([]);
  let editions = $state<Edition[]>([]);

  let selected = $state<Partial<Record<CatalogKind, boolean>>>({});
  let expanded = $state<Partial<Record<CatalogKind, boolean>>>({});
  let addTarget = $state<'existing' | 'new'>('existing');
  let adding = $state(false);
  let addError = $state<string | null>(null);
  let notice = $state<{ workId: string; text: string } | null>(null);

  const backHref = recalledListHash();

  const keys = $derived(registeredKeys(editions));
  const representatives = $derived(detail ? defaultSelection(detail) : {});
  const faces = $derived(
    KIND_ORDER.flatMap((kind) => {
      const entry = representatives[kind];
      return entry ? [{ kind, entry, registered: isRegistered(entry, keys) }] : [];
    }),
  );
  const checked = $derived(faces.filter((f) => !f.registered && selected[f.kind]));
  const sections = $derived(
    detail
      ? KIND_ORDER.map((kind) => ({ kind, entries: detail!.entries.filter((e) => e.kind === kind) })).filter(
          (s) => s.entries.length > 0,
        )
      : [],
  );
  const existingWork = $derived(
    detail ? findExistingWork(detail.title, detail.entries, works, editions) : undefined,
  );

  async function refreshLibrary() {
    editions = await listEditions();
    works = await listWorks();
  }

  let loadSeq = 0;
  $effect(() => {
    const currentId = id;
    const seq = ++loadSeq;
    loading = true;
    loadError = null;
    detail = null;
    notice = null;
    addError = null;
    Promise.all([fetchCatalogWork(currentId), refreshLibrary()])
      .then(([loaded]) => {
        if (seq !== loadSeq) return;
        detail = loaded;
        // 既定: 各面の代表1件にチェック。登録済みの面は外す。
        const registered = registeredKeys(editions);
        const next: Partial<Record<CatalogKind, boolean>> = {};
        for (const [kind, entry] of Object.entries(defaultSelection(loaded)) as [CatalogKind, CatalogEntry][]) {
          next[kind] = !isRegistered(entry, registered);
        }
        selected = next;
        expanded = {};
        addTarget = 'existing';
      })
      .catch((err: unknown) => {
        if (seq !== loadSeq) return;
        const status = err instanceof ApiError ? err.status : undefined;
        loadError = {
          missing: status === 404,
          message:
            status === 404
              ? '作品が見つかりません（カタログが未作成か、作品 ID が古い可能性があります）'
              : status !== undefined
                ? `作品を取得できませんでした (${status})`
                : '作品を取得できませんでした',
        };
      })
      .finally(() => {
        if (seq === loadSeq) loading = false;
      });
  });

  async function add(list: CatalogEntry[], openWork: boolean) {
    if (adding || !detail || list.length === 0) return;
    adding = true;
    addError = null;
    notice = null;
    try {
      const target = addTarget === 'existing' ? existingWork?.id : undefined;
      const result = await addEntriesToLibrary({ title: detail.title, entries: list, workId: target });
      await refreshLibrary();
      addTarget = 'existing'; // 追加後は、できた Work が既存の追加先になる
      if (result.workId && openWork) {
        navigate(`#/work/${result.workId}`);
      } else if (result.workId && result.added.length > 0) {
        notice = { workId: result.workId, text: `${result.added.length} 件を追加しました` };
      }
    } catch {
      addError = 'Library への追加に失敗しました';
    } finally {
      adding = false;
    }
  }
</script>

<section class="flex flex-col gap-4 p-4">
  <a
    href={backHref}
    class="type-label self-start"
    style="padding: 0.25rem 0;"
    aria-label="カタログの一覧へ戻る">← カタログ</a
  >

  {#if loadError}
    <p class="text-sm" style="color: var(--muted);" role="status">{loadError.message}</p>
  {:else if loading || !detail}
    <p class="py-6 text-center text-xs" style="color: var(--muted);">読み込み中…</p>
  {:else}
    <div class="flex flex-col gap-2">
      <h1 class="page-title" style="font-size: 1.5rem; margin: 0; overflow-wrap: anywhere;">{detail.title}</h1>
      <div class="flex flex-wrap items-center gap-x-1.5 gap-y-1">
        {#each KIND_ORDER.filter((k) => detail!.kinds.includes(k)) as kind (kind)}
          <CatalogKindBadge {kind} />
        {/each}
        <span class="text-[11px]" style="color: var(--muted); white-space: nowrap;">{detail.entries.length} 件</span>
      </div>
    </div>

    <div class="flex flex-col gap-2 p-3" style="border: 1px solid var(--fg);">
      <div class="shou" style="margin-bottom: 0.2rem;">
        <h2 style="white-space: nowrap;">Library に追加</h2>
        <span class="no">ADD</span>
      </div>

      {#if existingWork}
        <div class="flex flex-col gap-1.5">
          <div class="flex flex-wrap gap-1" role="group" aria-label="追加先">
            <FilterChip active={addTarget === 'existing'} onclick={() => (addTarget = 'existing')}>
              既存作品に追加
            </FilterChip>
            <FilterChip active={addTarget === 'new'} onclick={() => (addTarget = 'new')}>
              新しい作品にする
            </FilterChip>
          </div>
          {#if addTarget === 'existing'}
            <p class="text-[11px]" style="color: var(--muted); overflow-wrap: anywhere;">
              追加先: 「{existingWork.title}」
            </p>
          {/if}
        </div>
      {/if}

      {#if faces.length === 0}
        <p class="text-xs" style="color: var(--muted);">追加できる行がありません</p>
      {:else}
        <div class="flex flex-col" style="border-top: 1px solid var(--line);">
          {#each faces as face (face.kind)}
            <label
              class="row-hover flex items-center gap-2 py-2"
              style="border-bottom: 1px solid var(--line); min-width: 0;"
            >
              <input
                type="checkbox"
                style="width: 1rem; height: 1rem; flex-shrink: 0;"
                checked={!face.registered && !!selected[face.kind]}
                disabled={face.registered || adding}
                aria-label={`${KIND_LABELS[face.kind]}「${face.kind === 'book' ? ndlSeriesName(face.entry) : face.entry.title}」を追加する`}
                onchange={(e) => (selected[face.kind] = e.currentTarget.checked)}
              />
              <CatalogKindBadge kind={face.kind} />
              <span class="min-w-0 flex-1 truncate text-sm">
                {face.kind === 'book' ? ndlSeriesName(face.entry) : face.entry.title}
              </span>
              {#if face.registered}
                <span class="shrink-0 text-[11px]" style="color: var(--muted); white-space: nowrap;">登録済み</span>
              {/if}
            </label>
          {/each}
        </div>

        <button
          type="button"
          class="px-3 py-2 text-xs font-medium disabled:opacity-40"
          style="border: 1px solid var(--fg); color: var(--bg); background: var(--fg); white-space: nowrap; letter-spacing: 0.08em;"
          disabled={checked.length === 0 || adding}
          onclick={() => add(checked.map((f) => f.entry), true)}
        >
          {adding ? '追加中…' : checked.length > 0 ? `選んだ ${checked.length} 件を Library に追加` : '追加する面を選んでください'}
        </button>
      {/if}

      {#if addError}
        <p class="text-xs" style="color: var(--muted);" role="status">{addError}</p>
      {/if}
      {#if notice}
        <p class="flex flex-wrap items-center gap-x-3 text-xs" role="status">
          <span>{notice.text}</span>
          <a href={`#/work/${encodeURIComponent(notice.workId)}`} class="underline" style="white-space: nowrap;">
            作品を開く
          </a>
        </p>
      {/if}
    </div>

    {#each sections as section (section.kind)}
      {@const open = expanded[section.kind] || section.entries.length <= ROW_LIMIT}
      {@const rows = open ? section.entries : section.entries.slice(0, ROW_LIMIT)}
      <div class="flex flex-col" style="min-width: 0;">
        <div class="shou" style="margin-bottom: 0;">
          <h2 style="white-space: nowrap;">{KIND_LABELS[section.kind]}</h2>
          <span class="no">{KIND_CODES[section.kind]}</span>
        </div>
        {#if section.kind === 'book'}
          <p class="pt-1.5 text-[11px]" style="color: var(--muted);">
            登録はシリーズ単位です。1冊追加すると同じシリーズの巻は登録済みになります。
          </p>
        {/if}
        {#each rows as entry (entry.id)}
          <CatalogEntryRow
            {entry}
            registered={isRegistered(entry, keys)}
            busy={adding}
            onadd={(e) => add([e], false)}
          />
        {/each}
        {#if !open}
          <button
            type="button"
            class="mt-2 self-start px-2 py-1 text-[11px]"
            style="border: 1px solid var(--line); color: var(--muted); white-space: nowrap;"
            onclick={() => (expanded[section.kind] = true)}
          >
            ほか {section.entries.length - ROW_LIMIT} 件を表示
          </button>
        {/if}
      </div>
    {/each}

    {#if detail.candidates.length > 0}
      <div class="flex flex-col" style="min-width: 0;">
        <div class="shou" style="margin-bottom: 0;">
          <h2 style="white-space: nowrap;">候補</h2>
          <span class="no">CANDIDATES</span>
        </div>
        <p class="pt-1.5 text-[11px]" style="color: var(--muted);">
          題名の照合が弱く、この作品には含めていない行です。
        </p>
        {#each detail.candidates as entry (entry.id)}
          <CatalogEntryRow
            {entry}
            showKind
            registered={isRegistered(entry, keys)}
            busy={adding}
            onadd={(e) => add([e], false)}
          />
        {/each}
      </div>
    {/if}
  {/if}
</section>
