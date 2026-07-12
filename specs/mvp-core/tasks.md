# triptych MVP-Core Tasks

## Implementation Plan

### Wave 1 (parallel — no dependencies)

- [ ] **Task 1.1**: プロジェクト骨格（frontend）
  - What: Vite + Svelte 5 + TypeScript + Tailwind 初期化。mono デザイントークン（CSS変数 + class-based dark mode）、5ビューの空ルーティング、375pxモバイルファーストのApp Shell
  - Files: `package.json` `vite.config.ts` `src/app.css` `src/App.svelte` `src/routes/*`
  - Done when: `npm run dev` で5ビューがナビゲーションで行き来できる
  - Depends on: none

- [ ] **Task 1.2**: プロジェクト骨格（collector）
  - What: FastAPI 初期化。`/api/health`、SQLiteキャッシュ層（TTL付きget/set）、token bucket 実装、共通DTOスキーマ（pydantic）、`dist/` 静的配信 + Vite proxy 設定
  - Files: `backend/app.py` `backend/cache.py` `backend/ratelimit.py` `backend/dto.py`
  - Done when: pytest で cache TTL と token bucket が通り、`/api/health` が 200
  - Depends on: none

- [ ] **Task 1.3**: Store層（Dexie スキーマ + CRUD）
  - What: works/editions/releases/detections/meta の5テーブル、型定義（design.md の Data Models）、CRUD・差分マージ（dateHistory追記）・titleNormalized 生成関数
  - Files: `src/lib/db/schema.ts` `src/lib/db/store.ts` `src/lib/normalize.ts`
  - Done when: vitest で CRUD・マージ・正規化のユニットテストが通る
  - Depends on: none

### Wave 2 (after Wave 1)

- [ ] **Task 2.1**: アダプター: anilist（search / media / detect）
  - What: GraphQLクエリ、relations 正規化、title.native+synonyms 照合スコア、nextAiringEpisode → 次話DTO
  - Files: `backend/adapters/anilist.py` + fixtures
  - Done when: 録画fixtureで正規化テストが通る
  - Depends on: Task 1.2

- [ ] **Task 2.2**: アダプター: narou / openbd / ndl
  - What: なろうAPI（ncode検証・部分数・更新日）、openBD（ISBN checksum・発売日）、NDL Search（シリーズ探索→openBD詳細化の2段）
  - Files: `backend/adapters/{narou,openbd,ndl}.py` + fixtures
  - Done when: 各アダプターの正規化テストが通る
  - Depends on: Task 1.2

- [ ] **Task 2.3**: アダプター: syoboi / kakuyomu(scrape)
  - What: しょぼかる放送日、カクヨムスクレイピング（1req/s・同時1・TTL24h・robots.txt確認・UA明示）
  - Files: `backend/adapters/{syoboi,kakuyomu}.py` + fixtures
  - Done when: 正規化テストが通り、レート制御がテストで確認できる
  - Depends on: Task 1.2

- [ ] **Task 2.4**: Library / Work詳細ビュー（ローカルデータのみ）
  - What: status×mediaType フィルタ一覧、triptych 3面レイアウト、ステータス5値変更、progress入力、手動 Work/Edition 作成・編集・リンク付け替え
  - Files: `src/routes/library/*` `src/routes/work/*` `src/lib/components/*`
  - Done when: 手動登録だけで US-001/002 の受け入れ基準（手動系）を満たす
  - Depends on: Task 1.1, 1.3

### Wave 3 (after Wave 2)

- [ ] **Task 3.1**: 横断検索・登録フロー（FR-006）
  - What: `/api/search` 集約エンドポイント + 検索UI → 選択結果から Work+Edition 一括生成（anilist relations による原作候補提示を含む）
  - Files: `backend/api/search.py` `src/routes/search/*`
  - Done when: 検索→登録→triptych表示のE2Eシナリオ①②が通る
  - Depends on: Task 2.1, 2.2, 2.3, 2.4

- [ ] **Task 3.2**: Sync Engine（FR-004）
  - What: 起動時（1h経過時）+手動プル、追跡中Editionのみ差分取得、Release マージ（次話ローリング・dateHistory）、ソース別エラー記録と非侵襲表示
  - Files: `src/lib/sync/engine.ts` `src/lib/sync/errors.ts`
  - Done when: モックcollectorとの結合テスト（障害スキップ含む）が通る
  - Depends on: Task 2.1, 2.2, 2.3（並行可: 3.1）

- [ ] **Task 3.3**: Home / Calendar ビュー（US-003）
  - What: 直近7日強調（--hi）、新着マーク（seenAt）、月/リスト切替カレンダー、延期注記
  - Files: `src/routes/home/*` `src/routes/calendar/*`
  - Done when: E2Eシナリオ③（同期→新刊が新着表示）が通る
  - Depends on: Task 3.2

### Wave 4 (after Wave 3)

- [ ] **Task 4.1**: アニメ化検知（US-004 / FR-005）
  - What: Detection Engine（24h/Workスロットル・照合スコア・rejected除外リスト）、通知欄UI、accept→アニメEdition自動生成、放送日確定→Release追加
  - Files: `src/lib/sync/detect.ts` `src/routes/home/detections.svelte`
  - Done when: E2Eシナリオ④が通る
  - Depends on: Task 3.2, 3.3

- [ ] **Task 4.2**: PWA化 + ストレージ保全（FR-007）
  - What: vite-plugin-pwa（manifest・App Shell precache）、`navigator.storage.persist()`、ホーム画面追加促しUI、オフライン起動確認
  - Files: `vite.config.ts` `public/manifest` `src/lib/pwa.ts`
  - Done when: E2Eシナリオ⑤（オフライン起動→閲覧・編集）が通る
  - Depends on: Task 3.3

- [ ] **Task 4.3**: エクスポート/インポート + Settings
  - What: 全テーブルJSONエクスポート/インポート、同期ログ表示、collector接続先設定、Bearerトークン対応
  - Files: `src/routes/settings/*` `src/lib/db/portability.ts`
  - Done when: E2Eシナリオ⑥（別ブラウザで復元）が通る
  - Depends on: Task 3.2

### Wave 5 (final)

- [ ] **Task 5.1**: 統合検証
  - What: E2Eシナリオ①〜⑥通し、Chrome/Safari(iOS)/Firefox 実機確認、ja-html-lint、FEEDBACK.md 横断教訓チェック（nowrap・overflow-x・整列）、500ms初回描画計測
  - Done when: 全シナリオ green + cross-browser 確認済み
  - Depends on: Wave 4 全て

- [ ] **Task 5.2**: ドキュメント
  - What: README（起動手順・LAN/Tailscale運用・データ保全の注意）、collector の robots.txt/レート方針の明文化
  - Files: `README.md` `docs/operations.md`
  - Done when: README の手順だけで新規環境起動できる
  - Depends on: Task 5.1

## Progress
- Total: 13 tasks | Completed: 0 | In Progress: 0
