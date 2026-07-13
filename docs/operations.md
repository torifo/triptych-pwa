# 運用ドキュメント

triptych の collector が外部ソースへどうアクセスするか、同期・検知がどう動くか、トラブル時に何を見ればよいかをまとめます。設計の背景は [`specs/mvp-core/design.md`](../specs/mvp-core/design.md) を参照してください。

## 外部ソース一覧と収集マナー

collector は `backend/adapters/` 配下のソースごとに独立したアダプターを持ち、各自がレート制御・キャッシュ・User-Agent明示を担当します。ユーザーデータは一切保持せず、外部レスポンスのキャッシュのみをSQLite（`backend/cache.db`）に持ちます。

| ソース | 用途 | レート制限 | キャッシュTTL | 備考 |
|---|---|---|---|---|
| AniList (`anilist.py`) | アニメメタデータ・relations（原作候補）・放送予定・検知 | 60 req/min（自主制限） | 検索1h / 詳細6h | GraphQL API |
| なろう小説API (`narou.py`) | Web小説メタデータ・更新日 | 1 req/s | 検索1h / 詳細6h | 公式API |
| openBD (`openbd.py`) | ISBN引きの書籍情報・発売日 | 2 req/s | 詳細6h | ISBN検索専用（タイトル検索は非対応） |
| NDL Search (`ndl.py`) | シリーズ新刊の探索（→ISBNをopenBDで詳細化） | 1 req/s | 検索1h | 国会図書館サーチ OpenSearch API |
| しょぼいカレンダー (`syoboi.py`) | 放送日詳細（AniList補完） | 1 req/s | 詳細6h | 公開API |
| カクヨム (`kakuyomu.py`) | 話数・最終更新（API非提供のためスクレイピング） | 1 req/s + 同時実行1（`asyncio.Lock`） | 24h | robots.txt遵守。生HTMLは返さず正規化DTOのみ返す |

共通ルール:

- **User-Agent**: 全リクエストで `triptych/0.1 (personal tracker)` を明示します。
- **robots.txt**: スクレイピング系（カクヨム）はアダプター初期化後の初回アクセス時に一度 `robots.txt` を取得・パースし、`can_fetch` が偽なら以降のリクエストを `SourceUnavailable` として拒否します（`backend/adapters/kakuyomu.py` の `_ensure_robots_allowed`）。
- **レート制御の実装**: `backend/ratelimit.py` の `TokenBucket`（`rate`/`capacity` トークンバケット）を各アダプターが1インスタンスずつ保持します。API系は `acquire()` でブロッキング待機、スクレイピング系はさらに `asyncio.Lock` で同時実行数を1に絞ります。
- **キャッシュの実装**: `backend/cache.py` の `TTLCache`（SQLite backed）。`get()` は期限切れエントリを遅延削除し、ミス扱いにします。手動プル時のみ `?refresh=1` で強制更新できます（キャッシュを無視して再取得）。
- **タイトル検索の分業**: openBDはISBN引き専用のため、シリーズ新刊のタイトル探索はNDL Searchが担い、得られたISBNをopenBDで詳細化する2段構成です（`GET /api/ndl/series?title=`）。

## 同期の仕組み

実装: `src/lib/sync/engine.ts`

- **トリガー**: アプリ起動時、および Settings 等からの手動プル。
- **1hスロットル**: 前回同期から `THROTTLE_MS = 1時間` 未満の場合、起動時の自動同期はスキップされます（`engine.ts` の `THROTTLE_MS` 定数）。手動プルは `force` フラグでスロットルを無視して実行できます。
- **対象は追跡中Editionのみ**: `status ∈ {plan, active, paused}` の Edition だけが同期対象です。`done` / `dropped` は同期しません（全件同期による外部APIレート圧迫を避けるため）。
- **アニメReleaseの粒度（次話ローリング）**: 全話をReleaseとして残すとカレンダーが毎週ノイズで埋まるため、放送済み話は残しません。AniListの `nextAiringEpisode` を毎回の同期で上書きする形で「次に来る話」だけを追跡します（season-start + 次話ローリングのみ）。
- **日付変更の履歴**: Releaseの日付が変更（延期等）された場合は `dateHistory` に旧日付を追記して保持します。
- **失敗時の扱い**: ソース単位で処理を握りつぶさず、Sync Engineはそのソースをスキップして他ソースの同期を続行します。エラーは `meta.lastSyncErrors` 相当としてアプリ内に記録され、Settings画面の「同期」セクションに非侵襲的に表示されます（後述）。

## 検知の仕組み（アニメ化検知）

実装: `src/lib/sync/detect.ts`

- **対象**: 追跡中の原作系Edition（web-novel / light-novel / manga）。
- **24h/Workスロットル**: `DETECTION_THROTTLE_MS = 24時間`。Work単位で最後に検知を実行した時刻から24時間経っていない場合はスキップします。追跡作品数が増えても同期時間とAniListのレートを圧迫しない設計です。
- **照合スコアの閾値**: `DETECTION_MATCH_THRESHOLD = 0.85`。AniListの `title.native`（日本語原題）とsynonymsを用いたタイトル照合スコアがこの閾値未満の候補は検知結果として採用しません（design.mdに数値の明記はなく、MVP初期値としてコード側で定数化しています）。
- **却下（rejected）の除外**: 検知結果をユーザーが却下すると `DetectionEvent.state` が `rejected` になり、同一 `(workId, externalId)` の組は以降の照合から永続的に除外されます。誤検知を許容しつつ再提示を防ぐ仕組みです。
- **accept時の挙動**: 検知を承認するとアニメEditionが自動生成され、放送日が確定していればReleaseとしてカレンダーに追加されます。

## トラブルシュート

### collector が未起動のとき

- **フロントエンドの挙動**: 全ビューはIndexedDBのローカルデータのみで描画されるため、collectorが止まっていても閲覧・手動編集（ステータス変更・進捗入力・手動Work/Edition作成）は問題なく続行できます。
- **同期・検索・検知が失敗する**: 同期ボタン操作や検索操作を行うと、collectorへの接続に失敗した旨がエラーとして表示されます（Settings画面の「同期」セクション、または操作直後のエラーメッセージ）。
- **復旧方法**: `npm run api`（開発時）または本番相当の `uvicorn backend.app:app ...` を起動し直し、必要であれば手動プルで同期をやり直してください。

### 外部ソースが障害・レート制限中のとき

- Sync Engineはソース単位でスキップして処理を継続するため、他ソースの同期は影響を受けません。
- **見え方**: Settings画面の「同期」セクションに、最終同期時刻と、失敗したソースごとのエラーメッセージ・発生時刻が一覧表示されます（`src/routes/Settings.svelte` の `syncErrorEntries`）。エラーが無い場合は「エラーなし」と表示されます。
- 既存のキャッシュ済みデータ・ローカルデータでの表示は継続されるため、ユーザー操作をブロックすることはありません。

### スクレイピング先（カクヨム）のHTML構造が変わったとき

カクヨムアダプター（`backend/adapters/kakuyomu.py`）は、ページに埋め込まれたNext.jsのデータ島（`<script id="__NEXT_DATA__">` 内のJSON、Apolloキャッシュ形式）をパースして正規化しています。生HTMLの正規表現スキャンには依存していません。

構造変更が起きた場合の直し方:

1. **症状の切り分け**: `_extract_next_data()` がNoneを返す（`__NEXT_DATA__` タグ自体が無い）のか、`_parse_work_page()` 内で `state[f"Work:{query_work_id}"]` 等のキー参照が `KeyError` になる（JSON構造は健在だがフィールド名やネスト構造が変わった）のかを切り分けます。いずれの場合も `SourceUnavailable("kakuyomu", ...)` として扱われ、ユーザー影響は非侵襲的なエラー表示に留まります。
2. **fixtureの更新**: 実際のカクヨム作品ページのHTMLを取得し、`backend/adapters/`配下のfixture（録画済みレスポンス）を差し替えます。生HTML／scriptをそのままテストに含めても構いません（クライアントへは正規化DTOのみ返すため問題ありません）。
3. **アダプターの修正**: 新しいJSON構造に合わせて `_parse_work_page()` のキー参照（`work.get(...)` や `_resolve_ref()` によるApollo参照解決）を更新します。
4. **テストで確認**: `.venv/bin/pytest` を実行し、更新したfixtureに対する正規化テストが通ることを確認します。
5. **robots.txt / レートへの影響がないか確認**: 構造変更とは別に、対象パスの `robots.txt` 許可状況が変わっていないかも合わせて確認してください（`_ensure_robots_allowed()` が起動後最初のアクセス時に一度だけキャッシュする点に注意 — robots.txt側の変更を反映するにはプロセス再起動が必要です）。

同様の考え方は、将来 API非提供の別ソースをスクレイピングで追加する場合にも適用できます（正規化DTOへの変換・robots.txt確認・レート制御・fixtureテストの4点セット）。
