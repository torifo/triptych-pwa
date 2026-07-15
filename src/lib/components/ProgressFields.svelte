<script lang="ts">
  import type { EditionProgress } from '../db/schema';
  import { PROGRESS_UNIT_LABELS } from './labels';

  let {
    progress,
    defaultUnit = 'volume',
    onsave,
  }: {
    progress: EditionProgress | undefined;
    defaultUnit?: EditionProgress['unit'];
    onsave: (next: EditionProgress) => void;
  } = $props();

  // 初期値はプレースホルダ。実際の値は下の $effect が mount 時に同期する
  // （$state の初期化子でプロップを直接参照すると state_referenced_locally 警告になるため）。
  let unit = $state<EditionProgress['unit']>('volume');
  let current = $state<number>(0);
  let total = $state<number | undefined>(undefined);

  $effect(() => {
    unit = progress?.unit ?? defaultUnit;
    current = progress?.current ?? 0;
    total = progress?.total;
  });

  function commit() {
    onsave({ unit, current, total: total === undefined || Number.isNaN(total) ? undefined : total });
  }
</script>

<div class="flex flex-wrap items-center gap-1 text-xs" style="color: var(--fg);">
  <select
    class="px-1 py-1 text-xs"
    style="border: 1px solid var(--line); background: var(--bg); color: var(--fg); white-space: nowrap;"
    value={unit}
    onchange={(e) => {
      unit = (e.currentTarget as HTMLSelectElement).value as EditionProgress['unit'];
      commit();
    }}
  >
    {#each Object.entries(PROGRESS_UNIT_LABELS) as [u, label] (u)}
      <option value={u}>{label}</option>
    {/each}
  </select>
  <input
    type="number"
    min="0"
    inputmode="numeric"
    aria-label="現在の進捗"
    class="w-14 px-1 py-1 text-xs"
    style="border: 1px solid var(--line); background: var(--bg); color: var(--fg);"
    value={current}
    onchange={(e) => {
      current = Number((e.currentTarget as HTMLInputElement).value) || 0;
      commit();
    }}
  />
  <span style="color: var(--muted); white-space: nowrap;">/</span>
  <input
    type="number"
    min="0"
    inputmode="numeric"
    placeholder="全"
    aria-label="全体数"
    class="w-14 px-1 py-1 text-xs"
    style="border: 1px solid var(--line); background: var(--bg); color: var(--fg);"
    value={total ?? ''}
    onchange={(e) => {
      const raw = (e.currentTarget as HTMLInputElement).value;
      total = raw === '' ? undefined : Number(raw);
      commit();
    }}
  />
  <span style="color: var(--muted); white-space: nowrap;">{PROGRESS_UNIT_LABELS[unit]}</span>
</div>
