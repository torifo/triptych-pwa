import { mount } from 'svelte'
import './app.css'
import App from './App.svelte'
import { initTheme } from './lib/theme'
import { registerSW } from 'virtual:pwa-register'
import { checkForUpdatesOnReturn } from './lib/pwa-update'

// 保存したテーマはマウント前に反映する（設定画面を開かなくても効く。白い画面の一瞬のちらつきを減らす）
initTheme()

const app = mount(App, {
  target: document.getElementById('app')!,
})

// 新しい版の SW が有効になったら自動で読み込み直す（autoUpdate）。2回読み込まなくても反映される
registerSW({
  immediate: true,
  onRegisteredSW(_url, registration) {
    if (registration) checkForUpdatesOnReturn(registration, document)
  },
})

export default app
