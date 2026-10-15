<script lang="ts">
  // 時刻・日付・カウントダウン・話数を等幅で出す。
  let { text, muted = false, strong = false }: { text: string; muted?: boolean; strong?: boolean } = $props();
</script>

<span class="mono" class:muted class:strong>{text}</span>

<style>
  span {
    white-space: nowrap;
  }
  .muted {
    color: var(--muted);
  }
  .strong {
    font-weight: 500;
  }
</style>
