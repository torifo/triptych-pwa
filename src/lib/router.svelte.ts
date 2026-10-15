// hash-based router (no extra dependency)

export type RouteName = 'home' | 'library' | 'work' | 'calendar' | 'settings' | 'search' | 'catalog'

export interface RouteMatch {
  name: RouteName
  params: Record<string, string>
  /** ハッシュ内のクエリ（`#/catalog?kind=anime` の `kind=anime`）。 */
  query: Record<string, string>
}

function parseQuery(search: string): Record<string, string> {
  const query: Record<string, string> = {}
  if (!search) return query
  for (const [key, value] of new URLSearchParams(search)) {
    if (!(key in query)) query[key] = value
  }
  return query
}

function parseHash(hash: string): RouteMatch {
  const body = hash.replace(/^#/, '') || '/'
  const queryAt = body.indexOf('?')
  const path = queryAt < 0 ? body : body.slice(0, queryAt)
  const query = queryAt < 0 ? {} : parseQuery(body.slice(queryAt + 1))

  if (path === '/' || path === '') {
    return { name: 'home', params: {}, query }
  }
  if (path === '/library') {
    return { name: 'library', params: {}, query }
  }
  if (path === '/search') {
    return { name: 'search', params: {}, query }
  }
  if (path === '/calendar') {
    return { name: 'calendar', params: {}, query }
  }
  if (path === '/settings') {
    return { name: 'settings', params: {}, query }
  }
  if (path === '/catalog') {
    return { name: 'catalog', params: {}, query }
  }
  const catalogMatch = path.match(/^\/catalog\/([^/]+)$/)
  if (catalogMatch) {
    return { name: 'catalog', params: { id: decodeURIComponent(catalogMatch[1]) }, query }
  }
  const workMatch = path.match(/^\/work\/([^/]+)$/)
  if (workMatch) {
    return { name: 'work', params: { id: decodeURIComponent(workMatch[1]) }, query }
  }
  return { name: 'home', params: {}, query }
}

function currentHash(): string {
  return typeof window !== 'undefined' ? window.location.hash : '#/'
}

export const route = $state<RouteMatch>(parseHash(currentHash()))

/** redirect: URL にハッシュが無い起動の時に開く画面のハッシュ（preferences の startRedirect で決める）。履歴は増やさない。 */
export function initRouter(redirect?: string | null) {
  if (typeof window === 'undefined') return
  if (redirect) window.history.replaceState(window.history.state, '', redirect)
  const sync = () => {
    const next = parseHash(currentHash())
    route.name = next.name
    route.params = next.params
    route.query = next.query
  }
  window.addEventListener('hashchange', sync)
  sync()
}

export function navigate(path: string) {
  if (typeof window === 'undefined') return
  window.location.hash = path
}

/**
 * 現在のパスはそのままに、ハッシュのクエリだけを履歴を増やさず書き換える。
 * 絞り込みの状態を URL に載せ、行を開いて戻った時に復元するために使う。
 * replaceState は hashchange を起こさないので、route.query は自分で更新する。
 */
export function replaceQuery(query: Record<string, string>) {
  if (typeof window === 'undefined') return
  const body = window.location.hash.replace(/^#/, '') || '/'
  const queryAt = body.indexOf('?')
  const path = queryAt < 0 ? body : body.slice(0, queryAt)
  const search = new URLSearchParams(query).toString()
  const next = `#${path}${search ? `?${search}` : ''}`
  if (next !== window.location.hash) {
    window.history.replaceState(window.history.state, '', next)
  }
  route.query = { ...query }
}
