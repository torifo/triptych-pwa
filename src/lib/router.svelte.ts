// hash-based router (no extra dependency)

export type RouteName = 'home' | 'library' | 'work' | 'calendar' | 'settings'

export interface RouteMatch {
  name: RouteName
  params: Record<string, string>
}

function parseHash(hash: string): RouteMatch {
  const path = hash.replace(/^#/, '') || '/'

  if (path === '/' || path === '') {
    return { name: 'home', params: {} }
  }
  if (path === '/library') {
    return { name: 'library', params: {} }
  }
  if (path === '/calendar') {
    return { name: 'calendar', params: {} }
  }
  if (path === '/settings') {
    return { name: 'settings', params: {} }
  }
  const workMatch = path.match(/^\/work\/([^/]+)$/)
  if (workMatch) {
    return { name: 'work', params: { id: decodeURIComponent(workMatch[1]) } }
  }
  return { name: 'home', params: {} }
}

function currentHash(): string {
  return typeof window !== 'undefined' ? window.location.hash : '#/'
}

export const route = $state<RouteMatch>(parseHash(currentHash()))

export function initRouter() {
  if (typeof window === 'undefined') return
  const sync = () => {
    const next = parseHash(currentHash())
    route.name = next.name
    route.params = next.params
  }
  window.addEventListener('hashchange', sync)
  sync()
}

export function navigate(path: string) {
  if (typeof window === 'undefined') return
  window.location.hash = path
}
