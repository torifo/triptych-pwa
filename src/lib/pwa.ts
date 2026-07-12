// PWA化 + iOSストレージ保全 (FR-007)
//
// iOS Safari は非利用7日でサイトデータ（IndexedDB含む）を削除しうるため、
// - 初回起動時に navigator.storage.persist() を要求し、結果を meta に記録する
// - ホーム画面未追加（display-mode: standalone でない）を検出し、
//   促しバナー表示用のフラグを公開する
// (specs/mvp-core/design.md の Deployment / iOS ストレージ保全 節を参照)

import { db } from './db/schema'

const META_KEY = 'pwaStorage'

export interface PwaStorageMeta {
  persisted: boolean
  requestedAt: string
  isIOS: boolean
  isStandalone: boolean
}

/** UA からiOS系デバイス（iPhone/iPad/iPod、iPadOS の Mac偽装含む）を判定する */
export function detectIOS(nav: Navigator = navigator): boolean {
  const ua = nav.userAgent ?? ''
  const isIOSUA = /iPad|iPhone|iPod/.test(ua)
  // iPadOS 13+ は UA が Mac を名乗るため、タッチ対応の Mac として判定する
  const isIPadOSDesktopUA = /Macintosh/.test(ua) && nav.maxTouchPoints > 1
  return isIOSUA || isIPadOSDesktopUA
}

/** ホーム画面から起動中（display-mode: standalone）かどうかを判定する */
export function detectStandalone(win: Window = window): boolean {
  const nav = win.navigator as Navigator & { standalone?: boolean }
  return win.matchMedia?.('(display-mode: standalone)').matches === true || nav.standalone === true
}

/**
 * 初回起動時に永続ストレージを要求し、結果を meta テーブルへ記録する。
 * 既に記録がある場合は要求を再実行せず、iOS判定/standalone判定のみ都度返す。
 */
export async function initStoragePersistence(): Promise<PwaStorageMeta> {
  const isIOS = detectIOS()
  const isStandalone = detectStandalone()

  const existing = await db.meta.get(META_KEY)
  if (existing) {
    const value = existing.value as PwaStorageMeta
    return { ...value, isIOS, isStandalone }
  }

  let persisted = false
  try {
    if (navigator.storage?.persist) {
      persisted = await navigator.storage.persist()
    }
  } catch {
    persisted = false
  }

  const meta: PwaStorageMeta = {
    persisted,
    requestedAt: new Date().toISOString(),
    isIOS,
    isStandalone,
  }
  await db.meta.put({ key: META_KEY, value: meta })
  return meta
}

/** ホーム画面追加を促すべきか（iOS Safari かつ未追加）を判定する */
export function shouldPromptAddToHomeScreen(meta: Pick<PwaStorageMeta, 'isIOS' | 'isStandalone'>): boolean {
  return meta.isIOS && !meta.isStandalone
}
