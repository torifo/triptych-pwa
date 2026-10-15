import { mount } from 'svelte'
import './app.css'
import App from './App.svelte'
import { initTheme } from './lib/theme'

// 保存したテーマはマウント前に反映する（設定画面を開かなくても効く。白い画面の一瞬のちらつきを減らす）
initTheme()

const app = mount(App, {
  target: document.getElementById('app')!,
})

export default app
