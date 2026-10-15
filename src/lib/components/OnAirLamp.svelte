<script lang="ts">
  // 放送局の ON AIR ランプ。label を渡すとランプの横に等幅で出す（例 'ON AIR 2'）。
  let { label, ring = false }: { label?: string; ring?: boolean } = $props();
</script>

<span class="onair">
  <span class="dot" class:ring aria-hidden="true"></span>
  {#if label}
    <span class="mono label">{label}</span>
  {:else}
    <span class="sr-only">放送中</span>
  {/if}
</span>

<style>
  .onair {
    display: inline-flex;
    align-items: center;
    gap: 0.3rem;
    line-height: 1;
  }
  .dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--onair);
    flex-shrink: 0;
    animation: onair-pulse 2s ease-in-out infinite;
  }
  /* 表紙の上では地の色の縁で画像から浮かせる */
  .dot.ring {
    width: 8px;
    height: 8px;
    box-shadow: 0 0 0 2px var(--bg);
  }
  .label {
    font-size: 0.66rem;
    font-weight: 500;
    letter-spacing: 0.08em;
    color: var(--fg);
    white-space: nowrap;
  }
  @keyframes onair-pulse {
    0%,
    100% {
      opacity: 1;
    }
    50% {
      opacity: 0.35;
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .dot {
      animation: none;
    }
  }
</style>
