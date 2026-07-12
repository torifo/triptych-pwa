<script lang="ts">
  import { route, initRouter, navigate } from './lib/router.svelte'
  import Home from './routes/Home.svelte'
  import Library from './routes/Library.svelte'
  import WorkDetail from './routes/WorkDetail.svelte'
  import Calendar from './routes/Calendar.svelte'
  import Settings from './routes/Settings.svelte'
  import Search from './routes/Search.svelte'

  initRouter()

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
