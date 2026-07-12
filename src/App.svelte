<script lang="ts">
  import { onMount } from 'svelte'
  import { route, initRouter, navigate } from './lib/router.svelte'
  import Home from './routes/Home.svelte'
  import Library from './routes/Library.svelte'
  import WorkDetail from './routes/WorkDetail.svelte'
  import Calendar from './routes/Calendar.svelte'
  import Settings from './routes/Settings.svelte'
  import Search from './routes/Search.svelte'
  import { maybeAutoSync } from './lib/sync/engine'
  import { beginSync, completeSync, failSync } from './lib/sync/status.svelte'
  import { db } from './lib/db/schema'
  import { initStoragePersistence, shouldPromptAddToHomeScreen } from './lib/pwa'

  initRouter()

  const A2HS_DISMISSED_KEY = 'triptych:a2hs-dismissed'
  let showA2HSBanner = $state(false)

  function dismissA2HSBanner() {
    showA2HSBanner = false
    try {
      localStorage.setItem(A2HS_DISMISSED_KEY, '1')
    } catch {
      // localStorage不可の環境ではバナーを閉じるのみ（記憶はできない）
    }
  }

  onMount(async () => {
    try {
      const storageMeta = await initStoragePersistence()
      const dismissed = localStorage.getItem(A2HS_DISMISSED_KEY) === '1'
      showA2HSBanner = !dismissed && shouldPromptAddToHomeScreen(storageMeta)
    } catch {
      // ストレージ判定に失敗してもアプリ本体は継続動作させる
    }

    beginSync()
    try {
      const result = await maybeAutoSync()
      if (result.ran) {
        completeSync(new Date().toISOString(), result.errors)
      } else {
        // スロットルによりスキップ: 直近の同期時刻を meta から読み直して表示する
        const metaEntry = await db.meta.get('sync')
        const lastSyncAt = (metaEntry?.value as { lastSyncAt?: string } | undefined)?.lastSyncAt
        if (lastSyncAt) {
          completeSync(lastSyncAt, result.errors)
        } else {
          failSync(result.errors)
        }
      }
    } catch {
      failSync({})
    }
  })

  const navItems: { label: string; path: string; name: typeof route.name }[] = [
    { label: 'HOME', path: '#/', name: 'home' },
    { label: '検索', path: '#/search', name: 'search' },
    { label: 'LIBRARY', path: '#/library', name: 'library' },
    { label: 'WORK', path: '#/work/', name: 'work' },
    { label: 'CALENDAR', path: '#/calendar', name: 'calendar' },
    { label: 'SETTINGS', path: '#/settings', name: 'settings' },
  ]
</script>

<div class="flex min-h-svh flex-col" style="max-width: 480px; margin: 0 auto; background: var(--bg); color: var(--fg);">
  <header
    class="flex items-center gap-2 px-4 py-3"
    style="border-bottom: 1px solid var(--line);"
  >
    <span style="width: 14px; height: 14px; background: var(--fg); display: inline-block;"></span>
    <span class="text-sm font-bold tracking-wide">triptych</span>
  </header>

  {#if showA2HSBanner}
    <div
      class="flex items-center gap-3 px-4 py-2 text-xs"
      style="border-bottom: 1px solid var(--line); background: var(--hi-soft); color: var(--fg);"
    >
      <span class="flex-1">
        ホーム画面に追加すると、データが消えにくくなります（共有ボタン→「ホーム画面に追加」）
      </span>
      <button
        type="button"
        class="shrink-0 text-[10px] tracking-wide underline"
        style="color: var(--muted);"
        onclick={dismissA2HSBanner}
      >
        閉じる
      </button>
    </div>
  {/if}

  <main class="flex-1 overflow-y-auto pb-16">
    {#if route.name === 'home'}
      <Home />
    {:else if route.name === 'library'}
      <Library />
    {:else if route.name === 'work'}
      <WorkDetail id={route.params.id ?? ''} />
    {:else if route.name === 'calendar'}
      <Calendar />
    {:else if route.name === 'settings'}
      <Settings />
    {:else if route.name === 'search'}
      <Search />
    {/if}
  </main>

  <nav
    class="fixed bottom-0 left-0 right-0 flex"
    style="max-width: 480px; margin: 0 auto; border-top: 1px solid var(--line); background: var(--bg);"
  >
    {#each navItems as item}
      <button
        type="button"
        class="flex-1 py-2 text-center text-[10px] tracking-wide"
        style={`color: ${route.name === item.name ? 'var(--fg)' : 'var(--muted)'}; background: ${route.name === item.name ? 'var(--hi-soft)' : 'transparent'};`}
        onclick={() => navigate(item.path)}
      >
        {item.label}
      </button>
    {/each}
  </nav>
</div>
