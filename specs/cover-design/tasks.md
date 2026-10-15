# triptych Cover Design Tasks

## Implementation Plan

### Wave 0（先行・メイン）
- [x] **V0**: 共有契約（依存の追加、`src/app.css` のトークンと共通クラス、`src/lib/components/{cover.ts,onair.ts,Cover.svelte,OnAirLamp.svelte,MonoTime.svelte,CoverHero.svelte}` とテスト、`src/lib/db/schema.ts` の `EditionSyncHints` 拡張）
  - Done when: vitest green、`npm run check` 0/0

### Wave 1（並列・V0 の後。ファイルは重ならない）
- [x] **V1**: データ（同期で表紙を埋める・番組表を保存・本はカタログの AniList 表紙を借りる）
  - Files: `src/lib/sync/engine.ts`, `src/lib/sync/engine.test.ts`, `src/lib/catalog/register.ts`, `src/lib/catalog/register.test.ts`
- [x] **V2**: Home・ヘッダーとナビ・カレンダー
  - Files: `src/routes/{Home,Calendar}.svelte`, `src/App.svelte`, `src/lib/components/ReleaseRow.svelte`, `src/app.css`（ナビの節のみ）
- [x] **V3**: Library・Work 詳細
  - Files: `src/routes/{Library,WorkDetail}.svelte`, `src/lib/components/{EditionCard,ReleaseList,ProgressFields,SourceRefEditor,StatusSelect}.svelte`, 新規の `src/lib/components/*`（Library・Work 詳細用）
- [x] **V4**: カタログ・検索
  - Files: `src/routes/{Catalog,CatalogDetail,Search}.svelte`, `src/lib/components/{CatalogEntryRow,CatalogKindBadge}.svelte`

### Wave 2
- [x] **V5**: 統合検証（ビルド・375px/320px・ダーク・全画面のスクリーンショット・検証データの削除）→ push

## Progress
- Total: 6 tasks | Completed: 6 | In Progress: 0（実機の Safari・Firefox と「今日」の横並びの目視は未確認）
