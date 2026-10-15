<script lang="ts">
  import { onMount } from 'svelte'
  import {
    BookOpen,
    CalendarDays,
    House,
    Library as LibraryIcon,
    Search as SearchIcon,
    Settings as SettingsIcon,
    type LucideIcon,
  } from '@lucide/svelte'
  import { route, initRouter, navigate } from './lib/router.svelte'
  import Home from './routes/Home.svelte'
  import Library from './routes/Library.svelte'
  import WorkDetail from './routes/WorkDetail.svelte'
  import Calendar from './routes/Calendar.svelte'
  import Settings from './routes/Settings.svelte'
  import Search from './routes/Search.svelte'
  import Catalog from './routes/Catalog.svelte'
  import CatalogDetail from './routes/CatalogDetail.svelte'
  import { maybeAutoSync, type SyncErrors } from './lib/sync/engine'
  import { beginSync, completeSync, failSync, restoreSync, syncStatus } from './lib/sync/status.svelte'
  import { autoSyncPlan, isAutoSyncDue, preferences, startRedirect, type AutoSyncMode } from './lib/preferences'
  import { db, type Edition, type Release } from './lib/db/schema'
  import { listEditions, listReleases } from './lib/db/store'
  import { onAirCount } from './lib/components/home-view'
  import OnAirLamp from './lib/components/OnAirLamp.svelte'
  import { initStoragePersistence, shouldPromptAddToHomeScreen } from './lib/pwa'

  // ハッシュの無い起動（ホーム画面のアイコンなど）だけ、設定の「起動時に開く画面」へ
  initRouter(startRedirect(window.location.hash, preferences.get('startScreen')))

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

    if (autoSyncPlan(preferences.get('autoSync')).onLaunch) await autoSync()
    else await restoreLastSync()
  })

  // 自動同期を最後に試みた時刻。全ソース失敗で最終同期が進まなくても、1時間ごとより頻繁には試さない
  let lastAutoAttempt: number | null = null

  async function autoSync() {
    if (syncStatus.running) return
    lastAutoAttempt = Date.now()
    beginSync()
    try {
      const result = await maybeAutoSync()
      // 全ソース失敗・スロットルでスキップした時も、result.lastSyncAt は前回の同期時刻のまま返る
      let lastSyncAt = result.lastSyncAt
      if (!lastSyncAt) {
        const metaEntry = await db.meta.get('sync')
        lastSyncAt = (metaEntry?.value as { lastSyncAt?: string } | undefined)?.lastSyncAt
      }
      if (lastSyncAt) {
        completeSync(lastSyncAt, result.errors)
      } else {
        failSync(result.errors)
      }
    } catch {
      failSync({})
    }
  }

  /** 手動のみの時: 同期はせず、前回の同期の時刻とエラーだけを出す。 */
  async function restoreLastSync() {
    try {
      const value = (await db.meta.get('sync'))?.value as { lastSyncAt?: string; lastSyncErrors?: SyncErrors } | undefined
      restoreSync(value?.lastSyncAt ?? null, value?.lastSyncErrors ?? {})
    } catch {
      // 表示だけなので、読めなくても本体は動かす
    }
  }

  // 「1時間ごと」: 開いている間（画面が見えている時）だけ、1分ごとに期限を見て同期する。設定の変更はその場で効かせる
  const AUTO_SYNC_CHECK_MS = 60_000
  let autoSyncMode = $state<AutoSyncMode>(preferences.get('autoSync'))
  onMount(() => preferences.subscribe((prefs) => (autoSyncMode = prefs.autoSync)))

  $effect(() => {
    const { repeatMs } = autoSyncPlan(autoSyncMode)
    if (repeatMs === null) return
    const check = () => {
      if (document.visibilityState === 'hidden') return
      const now = Date.now()
      if (!isAutoSyncDue(syncStatus.lastSyncAt, now, repeatMs)) return
      if (lastAutoAttempt !== null && now - lastAutoAttempt < repeatMs) return
      void autoSync()
    }
    const id = setInterval(check, AUTO_SYNC_CHECK_MS)
    document.addEventListener('visibilitychange', check)
    return () => {
      clearInterval(id)
      document.removeEventListener('visibilitychange', check)
    }
  })

  // ヘッダーの ON AIR n。同期の前後と画面の移動で読み直し、放送日の切り替わり（朝5時）は時計で拾う
  let headerReleases = $state<Release[]>([])
  let headerEditions = $state<Edition[]>([])
  let clock = $state(new Date())
  const onAir = $derived(onAirCount(headerReleases, new Map(headerEditions.map((e) => [e.id, e])), clock))

  async function loadOnAir() {
    try {
      const [releases, editions] = await Promise.all([listReleases(), listEditions()])
      headerReleases = releases
      headerEditions = editions
      clock = new Date()
    } catch {
      // ヘッダーの表示だけなので、読めなくても本体は動かす
    }
  }

  $effect(() => {
    void route.name
    void route.params.id
    void syncStatus.running
    void loadOnAir()
  })

  $effect(() => {
    const id = setInterval(() => (clock = new Date()), 60_000)
    return () => clearInterval(id)
  })

  const navItems: { label: string; path: string; name: typeof route.name; icon: LucideIcon }[] = [
    { label: 'HOME', path: '#/', name: 'home', icon: House },
    { label: '検索', path: '#/search', name: 'search', icon: SearchIcon },
    { label: 'LIBRARY', path: '#/library', name: 'library', icon: LibraryIcon },
    { label: 'CATALOG', path: '#/catalog', name: 'catalog', icon: BookOpen },
    { label: 'CALENDAR', path: '#/calendar', name: 'calendar', icon: CalendarDays },
    { label: 'SETTINGS', path: '#/settings', name: 'settings', icon: SettingsIcon },
  ]
</script>

<div class="flex min-h-svh flex-col" style="max-width: 480px; margin: 0 auto; background: var(--bg); color: var(--fg);">
  <header
    class="sticky top-0 z-50 flex items-center gap-2.5 px-4 py-3"
    style="border-bottom: 1px solid var(--fg); background: var(--bg);"
  >
    <span aria-hidden="true" style="width: 15px; height: 15px; background: var(--fg); display: inline-block;"></span>
    <span class="text-sm font-bold" style="letter-spacing: 0.18em;">triptych</span>
    {#if onAir > 0}
      <a href="#/" class="onair-link" aria-label={`今日放送・配信のある作品 ${onAir}`}>
        <OnAirLamp label={`ON AIR ${onAir}`} />
      </a>
    {/if}
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

  <main class="flex-1 overflow-y-auto" style="padding-bottom: calc(4.5rem + env(safe-area-inset-bottom));">
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
    {:else if route.name === 'catalog'}
      {#if route.params.id}
        <CatalogDetail id={route.params.id} />
      {:else}
        <Catalog />
      {/if}
    {/if}
  </main>

  <nav
    class="bottom-nav fixed bottom-0 left-0 right-0"
    style="max-width: 480px; margin: 0 auto; border-top: 1px solid var(--line); background: var(--bg); padding-bottom: env(safe-area-inset-bottom);"
    aria-label="メインナビゲーション"
  >
    {#each navItems as item (item.path)}
      {@const active = route.name === item.name}
      <button
        type="button"
        class="bottom-nav-item"
        class:active
        aria-current={active ? 'page' : undefined}
        onclick={() => navigate(item.path)}
      >
        <item.icon size={18} strokeWidth={active ? 2.25 : 1.75} aria-hidden="true" />
        <span class="hi-underline" class:active>{item.label}</span>
      </button>
    {/each}
  </nav>
</div>

<style>
  .onair-link {
    display: inline-flex;
    align-items: center;
    margin-left: auto;
    padding: 0.25rem 0;
  }
</style>
