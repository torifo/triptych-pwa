<script lang="ts">
  import type { MediaType } from '../db/schema';
  import { MEDIA_TYPE_LABELS } from './labels';

  let { mediaType }: { mediaType: MediaType } = $props();
</script>

<span
  class="inline-block px-1.5 py-0.5 text-[10px] font-bold tracking-wide"
  style="border: 1px solid var(--fg); color: var(--fg); white-space: nowrap; line-height: 1.4;"
>
  {MEDIA_TYPE_LABELS[mediaType]}
</span>
