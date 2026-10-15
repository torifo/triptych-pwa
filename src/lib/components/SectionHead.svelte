<script lang="ts">
  // Work 詳細の Edition の中の節見出し（mono の .shou を小さくしたもの）。右に操作を置ける。
  import type { Snippet } from 'svelte';

  let { title, kicker, actions }: { title: string; kicker?: string; actions?: Snippet } = $props();
</script>

<div class="shou head">
  <h2>
    {title}{#if kicker}<span class="no kicker" aria-hidden="true">{kicker}</span>{/if}
  </h2>
  {#if actions}
    <div class="actions">{@render actions()}</div>
  {/if}
</div>

<style>
  .head {
    align-items: center;
    margin-bottom: 0;
    padding-bottom: 0.4rem;
    min-width: 0;
  }
  .head h2 {
    display: flex;
    align-items: baseline;
    gap: 0.6rem;
    min-width: 0;
    font-size: 0.85rem;
    white-space: nowrap;
  }
  .kicker {
    font-size: 0.6rem;
    letter-spacing: 0.24em;
  }
  .actions {
    display: flex;
    align-items: center;
    gap: 0.375rem;
    flex-shrink: 0;
  }
</style>
