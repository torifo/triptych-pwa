<script lang="ts">
  // 分類の印。番組は ON AIR の赤い丸、発売は黒い四角、Web は印なし。大きさは周りの文字に合わせる（点滅はさせない）。
  import type { DayCategory } from '../calendar/category';

  let { category }: { category: DayCategory } = $props();
</script>

{#if category !== 'web'}
  <span class="mark" class:program={category === 'program'} class:release={category === 'release'} aria-hidden="true"></span>
{/if}

<style>
  .mark {
    display: inline-block;
    flex-shrink: 0;
    vertical-align: 0.06em;
  }
  .program {
    width: 0.62em;
    height: 0.62em;
    border-radius: 50%;
    background: var(--onair);
  }
  /* 四角は丸より重く見えるので一回り小さく */
  .release {
    width: 0.54em;
    height: 0.54em;
    background: var(--fg);
  }
</style>
