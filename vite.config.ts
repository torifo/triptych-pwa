import { defineConfig } from 'vite'
import { svelte } from '@sveltejs/vite-plugin-svelte'
import tailwindcss from '@tailwindcss/vite'
import { VitePWA } from 'vite-plugin-pwa'

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    tailwindcss(),
    svelte(),
    VitePWA({
      registerType: 'autoUpdate',
      // /api は collector 直結・ステートレスなプロキシのためキャッシュ対象外
      // (runtimeCaching 未設定 = デフォルトでネットワークのみ、App Shell だけを precache)
      manifest: {
        name: 'triptych',
        short_name: 'triptych',
        description: 'ローカルファーストの原作・漫画・アニメ横断トラッカー',
        display: 'standalone',
        theme_color: '#0a0a0a',
        background_color: '#ffffff',
        icons: [
          {
            src: 'icon-192.png',
            sizes: '192x192',
            type: 'image/png',
          },
          {
            src: 'icon-512.png',
            sizes: '512x512',
            type: 'image/png',
          },
          {
            src: 'icon-512.png',
            sizes: '512x512',
            type: 'image/png',
            purpose: 'maskable',
          },
        ],
      },
    }),
  ],
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8000',
    },
  },
})
