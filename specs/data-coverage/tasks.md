# triptych Data Coverage Tasks

## Implementation Plan

### Wave 0（先行・メイン）
- [x] **W0**: 共有契約の型だけを反映する
  - What: `backend/dto.py` に design.md の DTO を追加・拡張。`src/lib/db/schema.ts` に v2 の型と Dexie v2 スキーマ宣言（upgrade 処理の中身は F1）
  - Done when: 既存テスト（pytest 117 / vitest 54）が green のまま、`npm run check` 0 errors
  - Depends on: none

### Wave 1（並列・W0 の後）
- [x] **B1**: AniList（JST・朝5時区切り・精度・relations・resolve-source・isAdult 除外・refresh・ステータス・25 req/min）
  - Files: `backend/adapters/anilist.py`, `backend/api/anilist.py`, テスト・fixture
  - Done when: 深夜放送の日付と `25:30` 表記、年のみ startDate の Release、無職転生・薬屋の relations から新期が出ることが fixture テストで通る
- [x] **B2**: NDL / openBD（books 絞り・全件→日付降順・978-4・NDC 振り分け・line・除外語・特装版除外・近刊/既刊の ISBN 重複排除・openBD フォールバック・精度）
  - Files: `backend/adapters/{ndl,openbd}.py`, `backend/api/{ndl,openbd}.py`, テスト・fixture
  - Done when: 本好き・転スラの実データ fixture で、本編の最新巻と近刊が正しい日付・精度で出て、ジュニア文庫・翻訳・アンソロジー・特装版が除外される
- [x] **B3**: なろう / カクヨム / 検索 / health（検索パラメータ・end 反転修正・WebNovelDTO・カクヨム JST・カクヨム検索・NDL 書籍検索・URL/ncode/ISBN 直接解決・health 記録）
  - Files: `backend/adapters/{narou,kakuyomu}.py`, `backend/api/{narou,kakuyomu,search}.py`, `backend/app.py`, テスト
  - Done when: 「転生したらスライムだった件」でなろう本物が上位に出る、本好き n4830bu が completed=true、URL 貼り付けで直接解決、health にエラーが記録される
- [x] **B4**: しょぼいカレンダー（Crawl-delay 60 キュー・TID 解決・番組・配信判定・配信開始 Release）
  - Files: `backend/adapters/syoboi.py`, `backend/api/syoboi.py`, テスト・fixture
  - Done when: キャッシュなしで `queued` を返し、キュー処理後に番組と配信開始が返る。60 秒に1回を超えないことがテストで確認できる
- [x] **F1**: データ層（Dexie v2 upgrade・mergeRelease v2・手動 Release CRUD・sourceRef 追加削除・JST 日付ヘルパー）
  - Files: `src/lib/db/store.ts`, `src/lib/dates.ts`, テスト（schema.ts の upgrade 関数の中身もここ）
  - Done when: 誤データ削除・既定値補完の移行テスト、ISBN キーのマージで日付が往復しない、手動 Release を守る、JST 0〜9 時の「今日」が正しい

### Wave 2（並列・Wave 1 の後）
- [x] **F2**: 同期・検知（mediaType 別ルーティング・NDL 新刊・しょぼかる配信開始・ローリング最新話・current 不変・完結反映・再試行・relations/sequel 検知・他 Work 重複排除・refresh 透過）
  - Files: `src/lib/sync/{engine,detect}.ts`, テスト
  - Depends on: B1〜B4（契約）, F1
- [x] **F3**: UI（Home の JST と新着絞り込み・Calendar の精度対応・WorkDetail/EditionCard の sourceRef 編集と Release 編集と完結表示と放送/配信予定・Search の NDL/カクヨム/直接解決/登録済み・Settings の検知エラー）
  - Files: `src/routes/*`, `src/lib/components/*`
  - Depends on: F1（store API）, B3（検索の返り値）

### Wave 3（最終）
- [ ] **V**: 統合検証（実 API で本好き・薬屋・転スラ・無職転生を登録→同期→検知、深夜放送の日付、ブラウザで全ビュー、テスト全 green）
  - Depends on: Wave 2

## Progress
- Total: 9 tasks | Completed: 8 | In Progress: 1（V）
