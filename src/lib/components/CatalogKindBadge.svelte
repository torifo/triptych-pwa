<script lang="ts">
  import { KIND_BADGES } from '../catalog/view';
  import type { CatalogKind } from '../catalog/types';

  let { kind }: { kind: CatalogKind } = $props();
</script>

<span
  class="inline-block px-1.5 py-0.5 text-[10px] font-bold tracking-wide"
  style="border: 1px solid var(--fg); color: var(--fg); white-space: nowrap; line-height: 1.4;"
>
  {KIND_BADGES[kind]}
</span>
