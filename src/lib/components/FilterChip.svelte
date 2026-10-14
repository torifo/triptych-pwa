<script lang="ts">
  import type { Snippet } from 'svelte';

  let {
    active,
    onclick,
    children,
  }: {
    active: boolean;
    onclick: () => void;
    children: Snippet;
  } = $props();
</script>

<button
  type="button"
  class="px-2 py-1.5 text-[11px]"
  style={`border: 1px solid ${active ? 'var(--fg)' : 'var(--line)'}; white-space: nowrap; color: ${active ? 'var(--bg)' : 'var(--fg)'}; background: ${active ? 'var(--fg)' : 'transparent'};`}
  aria-pressed={active}
  {onclick}
>
  {@render children()}
</button>
