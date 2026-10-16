# triptych Responsive Layout Tasks

## Implementation Plan

### Wave 0（先行・メイン）
- [x] **R0**: 枠（PC の上部タブ・全幅・`.page`・`viewport.svelte.ts`）
  - Files: `src/App.svelte`, `src/app.css`, `src/lib/viewport.svelte.ts`
  - Done when: 1280px でヘッダーにタブ・下のタブ無し、375px で今と同じ。vitest green・`npm run check` 0/0

### Wave 1（並列・R0 の後。ファイルは重ならない）
- [x] **R1**: カレンダー（分類・タブ・段・升目の件数・PC の升目と週）
  - Files: `src/routes/Calendar.svelte`, `src/lib/calendar/*`, `src/lib/components/{calendar-view.ts,calendar-view.test.ts,CatalogDayEvents.svelte,CatalogEventRow.svelte}`, 新規の `src/lib/components/*`（カレンダー用）
- [x] **R2**: Home・Library
  - Files: `src/routes/{Home,Library}.svelte`, Home・Library だけが使う `src/lib/components/*`
- [x] **R3**: Work 詳細・カタログ・カタログ詳細・検索
  - Files: `src/routes/{WorkDetail,Catalog,CatalogDetail,Search}.svelte`, それらだけが使う `src/lib/components/*`
- [x] **R4**: 設定
  - Files: `src/routes/Settings.svelte`

### Wave 2
- [x] **R5**: 統合検証（ビルド・1280/1024/375/320px・ダーク・全画面のスクリーンショット）→ push

## Progress
- Total: 6 tasks | Completed: 6 | In Progress: 0（Safari・Firefox の実機と、スマホからの接続方法は未着手）
