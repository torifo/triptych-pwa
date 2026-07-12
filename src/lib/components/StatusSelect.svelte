<script lang="ts">
  import type { Status } from '../db/schema';
  import { STATUS_LABELS, STATUSES } from './labels';

  let {
    value,
    onchange,
  }: {
    value: Status;
    onchange: (next: Status) => void;
  } = $props();
</script>

<select
  class="px-1.5 py-1 text-xs"
  style="border: 1px solid var(--line); background: var(--bg); color: var(--fg); white-space: nowrap;"
  value={value}
  onchange={(e) => onchange((e.currentTarget as HTMLSelectElement).value as Status)}
>
  {#each STATUSES as s (s)}
    <option value={s}>{STATUS_LABELS[s]}</option>
  {/each}
</select>
