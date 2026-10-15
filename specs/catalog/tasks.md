# triptych Catalog Tasks

## Implementation Plan

### Wave 0（先行・メイン）
- [x] **C0**: 共有契約（`backend/catalog/{schema.sql,db.py,runtime.py,text.py,dto.py,__main__.py}`、`src/lib/catalog/types.ts`、`.gitignore` に catalog.db）
  - Done when: `backend/tests/catalog/test_db.py` green、`python -m backend.catalog stats` が未作成を表示

### Wave 1（並列・C0 の後。ファイルは重ならない）
- [x] **H1**: AniList 収集器（年→月の自動分割・人気上位の補完・関係・レート残数を見た待ち）
  - Files: `backend/catalog/harvest/anilist.py`, `backend/tests/catalog/test_harvest_anilist.py`, fixture
- [x] **H2**: しょぼかる・NDL 収集器（全番組1回・Crawl-delay 60 を collector と共有／ndc 別の新刊・近刊全件）
  - Files: `backend/catalog/harvest/{syoboi,ndl}.py`, テスト, fixture
- [x] **H3**: なろう・カクヨム収集器（ジャンル別上位）
  - Files: `backend/catalog/harvest/{narou,kakuyomu}.py`, テスト, fixture
- [x] **L1**: 紐付け（関係による統合・題名照合・union-find・cluster_summary・FTS）
  - Files: `backend/catalog/link.py`, `backend/catalog/text.py`（拡張のみ）, テスト
- [x] **A1**: API と書き出し（`/api/catalog/*`・CSV/JSON）
  - Files: `backend/api/catalog.py`, `backend/catalog/export.py`, `backend/app.py`（ルーター登録の1行のみ）, テスト
- [x] **U1**: カタログ画面（一覧・詳細・Library への追加・書き出しボタン・ナビの WORK→カタログ）
  - Files: `src/routes/{Catalog,CatalogDetail}.svelte`, `src/lib/catalog/*`（types.ts を除く）, `src/lib/router.svelte.ts`, `src/App.svelte`, `src/lib/components/*`（新規のみ）

### Wave 2
- [x] **R**: 初回の全件収集（バックグラウンド）→ 紐付け → 書き出し
- [x] **V**: 統合検証（件数・代表作品のまとまり・画面からの追加・375px/ダーク）

## Progress
- Total: 9 tasks | Completed: 9 | In Progress: 0
