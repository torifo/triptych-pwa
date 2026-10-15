# triptych Catalog Design

## Overview

collector 側に、応答キャッシュとは別のカタログ DB（`backend/catalog.db`、SQLite）を持つ。収集は CLI のバッチ（`python -m backend.catalog ...`）で行い、ソースごとの収集器（harvester）が `entries` / `relations` に書き込む。紐付け器（linker）がソースをまたいだ結び付きと「作品」（cluster）を作り、全文検索の索引を作り直す。アプリは新しい API（`/api/catalog/*`）から読む。ユーザーの追跡データ（端末の IndexedDB）とは場所も API も分かれている。

```
CLI build ──> harvesters (anilist / syoboi / narou / kakuyomu / ndl) ──> entries, relations, harvest_state
CLI link  ──> linker ──> links, clusters, cluster_summary, entries_fts
API       ──> /api/catalog/works, /works/{id}, /stats, /export  ──> カタログ画面
CLI export──> local/catalog/catalog-YYYYMMDD.{csv,json}
```

## 共有契約

### スキーマ（`backend/catalog/schema.sql`）

`schema.sql` が正。要点：

- `entries`: 1行 = 1ソースの1作品（本は1冊）。`id` は `"<source>:<externalId>"`。`kind` は `anime | manga | novel | web-novel | book`。日付は精度つき（`start_date` + `start_precision`）。ソース固有の値は `extra`（JSON）
- `relations`: AniList の辺（`from_id` → `to_id`、`relation_type`）。`to_id` がカタログに無くても残す
- `links`: ソースをまたいだ結び付き（`method`・`confidence`）
- `clusters`: entry → 作品 ID。`cluster_summary`: 一覧用の集計
- `entries_fts`: FTS5（trigram）。`text` に題名・別名・読みを入れ、全角半角・大小文字を畳んだ形（NFKC・小文字化。元と違う時だけ）も足す。検索語も同じに畳む（`backend/catalog/text.py` の `fold_for_search` / `search_text`）。索引の中身を変えたら `python -m backend.catalog link` で作り直す
- `harvest_state`: 区切りごとの進み具合（再開用）。`meta`: 最終実行時刻・件数

### 収集器の共通インターフェース（`backend/catalog/harvest/<source>.py`）

```python
async def harvest(
    conn: sqlite3.Connection,
    client: httpx.AsyncClient,
    *,
    clock: Clock,            # backend.catalog.runtime.Clock（now/sleep を差し替え可能）
    limit: int | None = None,  # 取得件数の上限（試運転用）
    log: Callable[[str], None] = print,
    recent: bool = False,    # build --recent の軽い更新（範囲は下の「--recent の範囲」）
) -> HarvestResult
```

- 書き込みは `backend.catalog.db.upsert_entry()` / `replace_relations()` / `mark_slice()` だけを使う
- 区切り（slice）ごとに `harvest_state` を `done` にし、再実行時は `done` の区切りを飛ばす（`--fresh` で全部やり直し）
- `recent=True` は `done` の記録に関わらず、狭めた範囲だけを取り直す。既存の行は upsert するだけで消さない。`--recent` を付けた時だけ `recent=True` を渡す（付けない build は従来と同じ呼び出し）。`--fresh` とは併用できない
- `--recent` の結果は `meta` の `harvest:<source>:recent_at` / `recent_result` に残し、全件の `result` は上書きしない。画面の「最終収集」（`harvest:<source>:at`）は `--recent` でも進める
- User-Agent は `triptych/0.1 (personal tracker)`

### 実装で分かった制約（2026-10-14）

- AniList: `pageInfo.total` は件数に関係なく 5000 を返すので信用しない（短いページか `hasNextPage:false` で終わる）。`startDate_lesser` だけだと開始日の無い作品も混ざるため、`<1960`（日付あり）と `nodate` を分けた。今年+3 より先の作品用に末尾の区切りを足した。null の変数は受け付けないので、区切りごとに使う条件だけでクエリを組む
- NDL: 1つの検索で届くのは先頭 500 件だけ（idx で先へ進めない）。月より細かい日付指定は効かない。月の件数が 500 を超えるので、ISBN の前方一致（`97840`〜`97849`、溢れたら次の桁）で再帰的に割る。`ndc` 検索は目録済みのレコードしか返さないため、目録前の近刊は取れない（Library に入れた作品の近刊は、作品名で引く既存の同期が拾う。取得手段は「今後の課題」）
- しょぼかる: 全 8085 件のうちアニメ系（Cat 1 アニメ・7 OVA・8 映画・10 アニメ(終了/再放送)）の約 6760 件を採用。実写・占有行は除外
- 更新: 取り終えた区切りは再実行で飛ばすため、データを新しくするには次のどちらか
  - `build --recent`（日常用・約8分＋紐付け約15秒）: 新しい本・放送中の作品・上位の Web 小説だけを取り直す。週1回〜新刊が気になる時
  - `build --fresh`（月1回程度・約2時間）: `--recent` が見ない範囲（古い連載の状態、なろう・カクヨムの下位、昔の月の本）まで取り直す

### 区切りと上限

| harvester | 区切り | 1区切りの上限 | 待ち |
|---|---|---|---|
| anilist | (type, 開始年)。100ページ目でまだ続くなら (type, 開始年月) に割る。最後に (type, format) ごとの人気上位 2000 を補完 | 5000 件 | 20 req/min。`X-RateLimit-Remaining` が 3 以下なら次の分まで待つ。429 は Retry-After |
| syoboi | 全番組1回（TitleLookup TID=*） | - | 前回取得から 60 秒以上 |
| narou | (ジャンル)。`type=re&order=hyoka&lim=500&st=1..2000` | 2500 件 | 1 req/s・gzip |
| kakuyomu | (ジャンル)。人気順の検索ページ | 50 ページ（1000 件） | 1 req/s・同時1・robots 確認 |
| ndl | (ndc 913.6 / 726.1)。`mediatype=books&from=<1年前の年月>&cnt=500&idx=` | 取れるだけ | 1 req/s・30 秒タイムアウト |

`build --recent` の範囲（2026-10-15、前日に全件を取った DB で実測: 合計約7分44秒）:

| harvester | 取り直す範囲 | 上限 | 記録 |
|---|---|---|---|
| anilist | 今年〜+3年の年（と、それより先の作品）。放送中・放送予定（RELEASING / NOT_YET_RELEASED）のアニメ全部、予定のマンガ、連載中マンガの人気上位 1000（20 ページ）。最後に、保存済みで放送中・予定のまま今回見えなかった行（終了・中止・休止に変わった作品）を ID 指定（50 件/リクエスト）で取り直す（sweep） | sweep は最大 500 件（アニメ優先、次に人気順）。年が 5000 件の壁に当たったら月に割るのは全件と同じ | 年の区切りは `done` のまま毎回 1 ページ目から。状態の区切りは `recent:<TYPE>:...` |
| syoboi | 前回の実行の 1 日前以降に更新された番組だけ（`TitleLookup&TID=*&LastUpdate=`）。前回の記録が無い・試運転だった時は全番組 | 1 リクエスト | `all` 区切りの更新時刻を進める（件数は全件のまま）。更新が 0 件でも受け付ける（`Code 404`） |
| narou | 21 ジャンル × 総合評価の上位 2 ページ（500 件 × 2） | 1 ジャンル 1000 件 | 専用の `recent:genre:<code>` |
| kakuyomu | 12 ジャンル × 人気順の上位 5 ページ（20 件 × 5） | 1 ジャンル 100 件 | 専用の `recent:genre:<name>` |
| ndl | 先月〜6か月先の月と、その先をまとめた区切り（小説・漫画）。月が 500 件を超えたら ISBN で割るのは全件と同じ | 全件と同じ（取れるだけ） | 全件と同じ区切りを `done` にし直す。昔の月の記録は残す |

### 紐付け（`backend/catalog/link.py`）

1. **AniList の関係**: `ADAPTATION / SOURCE / SEQUEL / PREQUEL / PARENT` を作品統合に使う（`SIDE_STORY / SPIN_OFF / ALTERNATIVE / CHARACTER` は使わない。大きなシリーズが1つに潰れるのを防ぐ）
2. **題名の照合**（正規化は NFKC・小文字・空白記号除去・「第N期 / Season N / 2nd Season」などの期表記除去）:
   - しょぼかる ↔ AniList アニメ: 正規化題名一致かつ開始年 ±1 → 0.95、題名一致のみ → 0.7
   - なろう・カクヨム ↔ AniList 小説/漫画: 正規化題名一致 → 0.9、別名一致 → 0.85、長さ 8 文字以上の前方一致 → 0.6
   - NDL の本 ↔ AniList 小説/漫画: 本のシリーズ名（巻数を除いた題名）が一致 → 0.85（小説は NOVEL、漫画は MANGA にだけ結ぶ）
3. **作品（cluster）**: 関係と、信頼度 0.8 以上の links で union-find。0.8 未満は「候補」として詳細画面にだけ出す
4. `cluster_summary` と `entries_fts` を作り直す

### API（`backend/api/catalog.py`）

| Method | Endpoint | 返り値 |
|---|---|---|
| GET | `/api/catalog/stats` | `CatalogStats`（ソース別件数・作品数・最終収集/紐付け時刻）。DB 未作成なら `{"ready": false}` |
| GET | `/api/catalog/works?q=&kind=&has=&status=&year=&season=&sort=&page=&per=` | `CatalogWorkPage` |
| GET | `/api/catalog/works/{clusterId}` | `CatalogWorkDetail` |
| GET | `/api/catalog/export?format=csv\|json` | 全件のストリーム（Content-Disposition 付き） |

- `kind`: その種別の面を持つ作品だけ。`has`: `anime,manga,novel,web-novel,book` のカンマ区切り（全部持つ）。`status`: アニメの状態（RELEASING / NOT_YET_RELEASED / FINISHED）。`sort`: `popularity`（既定）/ `recent`（新しい開始日）/ `upcoming`（今日以降で近い日付）/ `title`。`per` は最大 100
- DTO は `backend/catalog/dto.py` が正。フロントの型は `src/lib/catalog/types.ts`

### 画面

- ナビの「WORK」（ID なしでは意味の無いタブ）を「カタログ」に置き換える。Work 詳細へは Library・検索・カタログから遷移する
- `#/catalog`: 絞り込み（種別チップ・アニメ化あり・状態・年・シーズン・並べ替え）、検索欄、作品の行（題名・持っている面のバッジ・年・人気・状態）、ページ送り、件数と最終収集日時、CSV / JSON ダウンロード
- `#/catalog/:clusterId`: 面ごと（原作・漫画・アニメ・Web 小説・紙の本）の一覧、各行にソースのリンク、題名照合の行には「題名一致 85%」のような信頼度、「候補」欄。上部に「Library に追加」（面ごとのチェック。既定は各面の代表1件）、行ごとに「追加」。登録済みは無効化
- 追加は既存の `createWork` / `createEdition` を使い、`sourceRefs` にカタログ行のソース参照を入れる。本（book）を選んだ時は文庫・漫画の Edition とし、sourceRef は NDL の `"<kind>:<line>:<シリーズ名>"` 形式に合わせる
- デザインは既存の mono の語彙（.shou・.type-label・.row-hover・活字バッジ）

## Error Handling

- 収集器の1リクエストの失敗は 3 回まで指数バックオフで再試行し、それでも駄目ならその区切りを `error` にして次へ進む（全体は止めない）
- CLI は最後にソース別の件数・エラー区切りを表示する。`--resume` 無しでも `done` の区切りは飛ばす
- API は DB 未作成なら `/stats` で `ready:false`、その他は 404 `{"detail":"catalog not built"}`

## Testing Strategy

- harvester: 録画 fixture と偽の時計で、区切りの分割（5000 件の壁）、再開、待ち（レート・Crawl-delay）、正規化を検証
- linker: 合成データで、関係による統合、照合の信頼度、閾値、期表記の除去、大きなシリーズが SIDE_STORY で潰れないこと
- API: 一時 DB で絞り込み・並べ替え・全文検索（3文字以上は FTS、2文字以下は検索用テキスト `entries_fts.text` の走査。全角半角・大小文字の揺れを吸収すること）・ページ送り・書き出し
- フロント: 行→Edition の変換、登録済み判定、URL クエリの組み立て
- 統合: 実際に全件収集を走らせ、件数・代表作品（薬屋・本好き・転スラ・無職転生）の作品まとまり・画面からの追加を確認

## 今後の課題

### 近刊（NDL 目録前）の取得手段

NDL の目録に載る前の近刊（出版予定）は、`ndc` 検索では取れない。取得手段を調べた結果（未実装）:

| 手段 | 評価 |
|---|---|
| NDL Search の OAI-PMH `set=jpro`（JPRO 由来の出版予定） | **推奨**。登録不要、200 件/ページ、OpenSearch のような 500 件の壁が無い。JPRO のジャンル語と NDC へのリンクで漫画・小説を判定でき、ISBN をキーに `ndl:<isbn>` へ統合できる |
| NDL OpenSearch の `dpid=jpro` | 分類が弱く、ムックなどが小説に混ざる。見送り |
