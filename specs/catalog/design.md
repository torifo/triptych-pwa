# triptych Catalog Design

## Overview

collector 側に、応答キャッシュとは別のカタログ DB（`backend/catalog.db`、SQLite）を持つ。収集は CLI のバッチ（`python -m backend.catalog ...`）で行い、ソースごとの収集器（harvester）が `entries` / `relations` に書き込む。紐付け器（linker）がソースをまたいだ結び付きと「作品」（cluster）を作り、全文検索の索引を作り直す。アプリは新しい API（`/api/catalog/*`）から読む。ユーザーの追跡データ（端末の IndexedDB）とは場所も API も分かれている。

```
CLI build ──> harvesters (anilist / syoboi / narou / kakuyomu / ndl / jpro) ──> entries, relations, harvest_state
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
- NDL: 1つの検索で届くのは先頭 500 件だけ（idx で先へ進めない）。月より細かい日付指定は効かない。月の件数が 500 を超えるので、ISBN の前方一致（`97840`〜`97849`、溢れたら次の桁）で再帰的に割る。`ndc` 検索は目録済みのレコードしか返さないため、目録前の近刊は別の収集器 `jpro` が OAI-PMH で取る（下の「目録前の近刊（JPRO）」）
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
| jpro | 更新日（JST）の日 `day:<YYYY-MM-DD>`、新しい順。直近 30 日（`WINDOW_DAYS`）。`resumptionToken` で 200 件ずつ | 1日 2 万件（`MAX_DAY_RECORDS`）。超える日は1ページ目で件数を見て取らない | 1 req/s・90 秒タイムアウト。429 は Retry-After（無ければ 20 秒）、503 も Retry-After |

`build --recent` の範囲（2026-10-15、前日に全件を取った DB で実測: 合計約7分44秒）:

| harvester | 取り直す範囲 | 上限 | 記録 |
|---|---|---|---|
| anilist | 今年〜+3年の年（と、それより先の作品）。放送中・放送予定（RELEASING / NOT_YET_RELEASED）のアニメ全部、予定のマンガ、連載中マンガの人気上位 1000（20 ページ）。最後に、保存済みで放送中・予定のまま今回見えなかった行（終了・中止・休止に変わった作品）を ID 指定（50 件/リクエスト）で取り直す（sweep） | sweep は最大 500 件（アニメ優先、次に人気順）。年が 5000 件の壁に当たったら月に割るのは全件と同じ | 年の区切りは `done` のまま毎回 1 ページ目から。状態の区切りは `recent:<TYPE>:...` |
| syoboi | 前回の実行の 1 日前以降に更新された番組だけ（`TitleLookup&TID=*&LastUpdate=`）。前回の記録が無い・試運転だった時は全番組 | 1 リクエスト | `all` 区切りの更新時刻を進める（件数は全件のまま）。更新が 0 件でも受け付ける（`Code 404`） |
| narou | 21 ジャンル × 総合評価の上位 2 ページ（500 件 × 2） | 1 ジャンル 1000 件 | 専用の `recent:genre:<code>` |
| kakuyomu | 12 ジャンル × 人気順の上位 5 ページ（20 件 × 5） | 1 ジャンル 100 件 | 専用の `recent:genre:<name>` |
| ndl | 先月〜6か月先の月と、その先をまとめた区切り（小説・漫画）。月が 500 件を超えたら ISBN で割るのは全件と同じ | 全件と同じ（取れるだけ） | 全件と同じ区切りを `done` にし直す。昔の月の記録は残す |
| jpro | 前回最後まで終わった実行（`harvest_state` の `run` 行、`cursor` がその日）の1日前から今日までの日。`done` でも取り直す。記録が無ければ全件と同じ（直近 30 日・`done` は飛ばす） | 全件と同じ | 日の区切りは全件と同じ。エラー・`--limit` の実行は `run` を進めない |

### 紐付け（`backend/catalog/link.py`）

1. **AniList の関係**: `ADAPTATION / SOURCE / SEQUEL / PREQUEL / PARENT` を作品統合に使う（`SIDE_STORY / SPIN_OFF / ALTERNATIVE / CHARACTER` は使わない。大きなシリーズが1つに潰れるのを防ぐ）
2. **題名の照合**（正規化は NFKC・小文字・空白記号除去・「第N期 / Season N / 2nd Season」などの期表記除去）:
   - しょぼかる ↔ AniList アニメ: 正規化題名一致かつ開始年 ±1 → 0.95、題名一致のみ → 0.7
   - なろう・カクヨム ↔ AniList 小説/漫画: 正規化題名一致 → 0.9、別名一致 → 0.85、長さ 8 文字以上の前方一致 → 0.6
   - NDL の本 ↔ AniList 小説/漫画: 本のシリーズ名（巻数を除いた題名）が一致 → 0.85（小説は NOVEL、漫画は MANGA にだけ結ぶ）。JPRO の近刊の行も同じ（下の「JPRO の近刊の紐付け」）。目録済みの本は、さらに本体（「 : コミック」などを外した題名）とその頭、もう一方の種類でも、作者が重なる時だけ結ぶ（下の「目録済みの本の本体での紐付け」）
3. **作品（cluster）**: 関係と、信頼度 0.8 以上の links で union-find。0.8 未満は「候補」として詳細画面にだけ出す。どの作品にも結ばれなかった本は (シリーズ名, kindHint, line) ごとに1作品。JPRO の近刊の行は、同じシリーズ名・種類の目録済みの棚があれば、line が同じ・書き換え（空白・大小文字・COMICS/コミックス）・どちらかがもう一方を含む棚へ、棚が1つだけならその棚へ入る（JPRO と NDL で叢書名の書き方が違うため。「ヤンマガKCスペシャル」/「ヤンマガKC」）。ただし両方に作者がいて重ならない棚には入らない（同じ題名の別のアンソロジー「最後の晩餐」など）
4. `cluster_summary` と `entries_fts` を作り直す。近日（`next_label`）は、アニメは「第N話」か題名、本は巻（「5巻」「第五部 女神の化身 12巻」「28巻 エキスパンションパス」。Library の巻の表示と同じく数字の巻に「巻」を足す）。巻の無い本は題名、作品の題名と同じなら出さない

#### JPRO の近刊の紐付け

近刊の題名は巻を題名の中に書く（「シャングリラ・フロンティア(28)エキスパンションパス ~クソゲーハンター、神ゲーに挑まんとす~」「山、買いました10 ~異世界暮らしも悪くない~」）。`text.split_volume_title` が題名をシリーズ名（本体）・巻・版の語に分ける。

- **分け方**: 末尾の巻（「(28)」「1巻」「 2」「Vol.31」「 下」）、または本体の直後の巻（「本体(7) 巻の題」「本体10 ~副題~」「本体 3 ~副題~」「本体【三】巻の題」）より前がシリーズ名。末尾と途中に同じ番号があれば途中で切る（「…なります9~副題~ 9」）。末尾と違う番号の途中の数字は題名の一部（「ファンタシースターオンライン2 es ぎゅ!!! 1」「テルスター’86 4」「天国2.0 3」）。4桁の数字（「赤灯えれじい2024」）は巻にしない。版の語（エキスパンションパス・限定版・特装版・通常版・特別版・同梱版・豪華版）と括弧の注記（(コミック)・(完)・〈新装版〉・[図書館版] など）は外す。部の印から先（「第五部 女神の化身」）も巻の側
- **保存と再計算**: 収集時に `extra.series` / `extra.volume` に入れる。`link` も `extra.jpro.title` から分け直し、違えば `extra.series` / `extra.volume` を書き戻す（古い収集器が入れた行も `link` だけで直る。何度実行しても同じ）
- **自分の題名**: 巻を除いた題名（注記は残す。目録は「… : コミック」「… : 完全版」と書くため）。これが目録済みの棚の名前なら、その巻と同じ作品に入る（AniList との照合も自分の題名だけ。目録済みの巻と離れない）
- **作者で裏を取る形**: 目録済みの棚が無い本は、さらに次の形でも照合する。どれも作者（creators の正規化キー）が重なる時だけ結ぶ。注記を外した題名、途中の巻の後ろを落としたシリーズ名（「山、買いました」）、副題・巻の題・「 : 」の前までの頭（「仏の辰捕物帳 老いた掏摸」→「仏の辰捕物帳」。4文字以上。英字の語の間の空白では切らない。作者の名前そのものの頭は使わない）。結ぶ先は AniList の小説・漫画（links に 0.9 で残す）、目録済みの本・なろう・カクヨムの行（その作品に入る。links には残らない）。キーの長い一致を優先する（スピンオフに自分の AniList の項目があればそちら、無ければ本編の作品）
- **実測（2026-10-15、本番の複製で `link`）**: 作品数 157,522 → 157,219、JPRO の1冊だけの作品 1,241 → 937、新しくまとまった JPRO の行 305（AniList の作品に入った JPRO の行 1,059 → 1,223）。目録済みの棚から離れた行 0、既存の作品どうしがつながった例 0。別の作品に入った誤りは無作為 40 件で 0 件。うち 6 件は自分の項目が無いスピンオフ・外伝・番外編が同じ作者の本編の作品に入ったもの（「ふつつかな悪女ではございますが おやすみ前の読み聞かせ」「ロメリア戦記 外伝」など。同じシリーズとして扱う）。全件の通し確認では 1 件（作者名が頭の「萩尾望都 創作の泉」→「萩尾望都-愛の宝石-」。作者名の頭を使わないようにして解消）

#### 目録済みの本の本体での紐付け

目録は漫画化を「転生第八王子の幸せ家族計画 : コミック」「…@COMIC」「… : THE COMIC」「漫画版…」と書き、原作小説は「十番目のユナス : 呪いの伯爵と死なない生贄」のように副題を「 : 」の後ろに書く。AniList は漫画を本体だけ（「転生第八王子の幸せ家族計画」）、または原作小説が無く漫画だけのことが多い。シリーズ名が自分の種類の AniList の項目に 0.8 以上で結ばれない本は、次の形でも照合する。

- **形**: 漫画化の印（コミック・コミックス・コミック版・コミックス版・フルカラーコミック・コミカライズ・漫画版・まんが版・マンガ版を「 : 」の後ろ・括弧の中・頭に、THE COMIC・@COMIC を末尾か「 : 」の前に）を外した本体（`text.strip_adaptation`）と、その副題・「 : 」の前までの頭（JPRO と同じく英字の語の間の空白では切らない）。4文字以上（「銀の匙」「こころ」は使わない）、作者の名前そのものは使わない
- **作者で裏を取る**: どの形も作者が重なる時だけ結ぶ（0.9、links に残る）。作者の無い巻は同じシリーズ名・種類の目録済みの巻の作者で見る（棚から離れない）。AniList の漫画の題名に付いた「… THE COMIC」「…@COMIC」も外して照合する（副題を除いた題名と同じ扱い。なろう・カクヨムの照合でも同じ）
- **もう一方の種類**: 自分の種類で結ばれなければ、シリーズ名とこれらの形で、もう一方の種類（漫画の本なら AniList の小説、小説の本なら漫画）にも作者が重なる時だけ結ぶ。漫画化の本は原作小説の作品へ、原作小説・ノベライズは漫画の作品へ入る（同じ作品の別の面）。別名では結ばない（短編を集めた漫画「捨てないで」の別名に収めた短編「小さな故意の物語」が並ぶ）
- **優先**: キーの長い一致（自分の AniList の項目がある外伝・劇場版はそちら。「名探偵コナン 黒鉄の魚影 : 劇場版」）、同じ長さなら自分の種類、作者が多く重なる方、人気の順
- **作者の名前**: AniList は「名 姓」の順のことがある（「とち うえやま」「信輔近藤」）。同じ長さ（3文字以上）で順を入れ替えた名前は同じ人とみる（2文字の「田中」/「中田」は別人）。同じ題名の項目が複数あれば作者が多く重なる方へ（「仕掛人 藤枝梅安」のさいとう・たかを版と武村勇治版。原作者だけでは決まらない）
- **近刊**: 目録済みの巻がこうして作品に入った棚の JPRO の近刊は、その作品へ入る（目録済みの巻と離れない）
- **実測（2026-10-15、本番の複製で `link`）**: 作品数 157,219 → 156,611、ndl の行だけの作品 12,327 → 11,725、AniList の作品に入った目録済みの行 11,428 → 12,318（+890）、それに続いた JPRO の行 41。題名に漫画化の印がある目録済みの行で ndl だけの作品にいるもの 161 → 92（残りは AniList に同じ題名の項目が無い）。作者名の順で結ばれた行（クッキングパパ・忍者と極道・心霊探偵八雲など）を含む。既存の AniList の作品どうしがつながった例 0、目録済みの棚から離れた行 0。別の作品に入った誤りは無作為 100 件で 0 件。全件（620 シリーズ）の通し確認で 1 件（別名による「小さな故意の物語」→「捨てないで」。もう一方の種類は別名で結ばないようにして解消）。名前の順の照合で同じ題名の別の漫画化に移った 1 件（「仕掛人藤枝梅安」）は作者が多く重なる方を選ぶようにして解消。古典で漫画化が複数ある原作（「人間失格」「宮本武蔵」など）は、作者が重なる一番人気の漫画化の作品に入る

### API（`backend/api/catalog.py`）

| Method | Endpoint | 返り値 |
|---|---|---|
| GET | `/api/catalog/stats` | `CatalogStats`（ソース別件数・作品数・最終収集/紐付け時刻）。DB 未作成なら `{"ready": false}` |
| GET | `/api/catalog/works?q=&kind=&has=&status=&year=&season=&sort=&page=&per=` | `CatalogWorkPage` |
| GET | `/api/catalog/works/{clusterId}` | `CatalogWorkDetail` |
| GET | `/api/catalog/events?from=&to=&kind=` | `CatalogEventPage`（カレンダー用。期間内の出来事） |
| GET | `/api/catalog/export?format=csv\|json` | 全件のストリーム（Content-Disposition 付き） |

- `kind`: その種別の面を持つ作品だけ。`has`: `anime,manga,novel,web-novel,book` のカンマ区切り（全部持つ）。`status`: アニメの状態（RELEASING / NOT_YET_RELEASED / FINISHED）。`sort`: `popularity`（既定）/ `recent`（新しい開始日）/ `upcoming`（今日以降で近い日付）/ `title`。`per` は最大 100
- DTO は `backend/catalog/dto.py` が正。フロントの型は `src/lib/catalog/types.ts`
- `/events`: `from`〜`to`（両端を含む、最大 62 日、`YYYY-MM-DD`。不正・逆順・超過は 422）、`kind`（`anime,book`、省略で両方。未知の値は 422）。応答は `{items, truncated}`、最大 600 件（超えたら `truncated: true` で、日付の早い方 600 件）。並びは date → time（無いものは後）→ 作品の人気降順 → cluster_id。`CatalogEvent` は `clusterId / entryId / title（作品の題名） / kind / event / date / time / label / cover / popularity`
  - anime: AniList の行で、日精度（精度が無く日付が日まで、も含む）の `start_date` が期間内の TV / TV_SHORT / ONA / MOVIE。`event` は MOVIE が `movie`（「公開」）、他は `season-start`（「放送開始」。題名に「第2期」「Season 2」などがあれば「第2期 放送開始」）。cover は行の cover、無ければ作品の cover
  - anime の次の話: 放送中（RELEASING）の AniList の行の `extra.nextAiringEpisode`（収集時点の次回放送）。`event` は `episode`、label は「第3話」（話数が無ければ「次回放送」）。保存されている日付・時刻は放送日表記（24:30 = 翌日 00:30）なので、実際の JST の日付と `time`（`HH:MM`）に直して期間と比べる。そのあとは同じ枠で毎週あると見込んで延ばす（`estimated: true`。全話数が分かればその話まで、分からなければ 12 週まで。話数の無い行は延ばさない）。収集時点の次回しか持たないため、延ばさないと翌週以降のカレンダーがほぼ空になる（2026-10-16）。作品・日ごとに1件（取得した次回が見込みより先、次に早い時刻）。同じ行の開始日と重なる第1話は `season-start` だけにする。フロントは実の時刻を放送日（朝5時区切り・24:30 表記）に戻して Library と揃える。`time` は他の出来事では常に null
  - book: 日精度の `start_date` が期間内の本で、作品が AniList の行を持つもの（`cluster_summary.popularity > 0`。本は 1 日に 100 冊を超えるので、知られた作品だけ）。`event` は `release`、label は `_book_next_label`（「2巻」）、cover は作品の cover。同じ作品の同じ日の本は 1 件（巻の小さい方）
  - 成人向けの AniList の行は収集時に除いている（`isAdult: false`）ので、ここでは条件を足さない

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

### 近刊（NDL 目録前）の取得手段 → 実装済み（2026-10-15、下の「目録前の近刊（JPRO）」）

調べた手段と採否:

| 手段 | 評価 |
|---|---|
| NDL Search の OAI-PMH `set=jpro`（JPRO 由来の出版予定） | **採用**（`harvest/jpro.py`）。登録不要、200 件/ページ、OpenSearch のような 500 件の壁が無い。JPRO のジャンル語と NDC へのリンクで漫画・小説を判定でき、ISBN をキーに `ndl:<isbn>` へ統合できる |
| NDL OpenSearch の `dpid=jpro` | 分類が弱く（ジャンル語が載らない）、ムックなどが小説に混ざる。見送り |

## 目録前の近刊（JPRO）

収集器 `jpro`（`backend/catalog/harvest/jpro.py`、`--source jpro`、全ソースの build では `ndl` の後）。詳細はモジュールの docstring。

- **取り方**: `https://ndlsearch.ndl.go.jp/api/oaipmh?verb=ListRecords&metadataPrefix=dcndl&set=jpro&from=<日>&until=<日>`、続きは `verb=ListRecords&resumptionToken=<token>`。`from` / `until` はレコードの更新日時で、`Z` 付きだが日本時間（`responseDate` と HTTP の `Date` で確認）。区切りは JST の日。`until` 無しの `from` は全件（358 万件）なので必ず両端を付ける
- **日の件数**: ふだんは 1日 1,000〜2,600 件（10/01〜10/07: 8,865 件、10/08〜10/14: 6,649 件、10/15 は昼時点 1,912 件）。2026-09-27 に全件（3,556,855 件、すべて 16:30:00）の更新日が付け直され、それより前の日はほぼ空（09/15〜09/23 で削除済み 9 件）。各日の1ページ目の `completeListSize` で見積もり、2 万件を超える日は取らずに `done`（メッセージ `bulk: ...`）にする。その日にしか更新の無い登録は、登録が次に更新された時か、NDL の目録ができた時（`ndl` 収集器）に入る
- **日の確定**: 応答に `Cache-Control: max-age=83212`（約 23 時間、CloudFront）が付く。当日に取った応答が翌日夕方まで返り得るので、日は 2 日後（`SETTLE_DAYS`）に `done`。今日と昨日は `pending`（`open`）のまま次の実行で取り直す
- **判定**（`classify`。表は `test_harvest_jpro.py` の `test_classification_table` で固定）: 削除・電子書籍・ISBN なし → 除外。成人向け（Content warning / Any adult / 成人向け・成人指定）→ 除外。形態がムック・絵本・図鑑・事典・写真集・電磁的記録媒体 → 除外。NDC リンク 726.1 → 漫画、913.6 → 小説（NDL 自身の分類）。芸能・タレント・趣味・実用・実用・ビジネス・学参 → 除外（「日本文学小説・物語」と付いた写真集や戦術書）。内容「コミックス・劇画」→ 漫画、「日本文学小説・物語」→ 小説。JPRO のジャンル「コミックス（書籍 / 雑誌扱 / 廉価版）」で児童でないもの → 漫画（内容が「諸芸・娯楽」のマーマレードコミックスなど）。それ以外 → 除外。そのあと `ndl` 収集器と同じ除外（978-4 以外・特装版・アンソロジー・ガイドなど）、題名の語（写真集・ビジュアルブック・キャラクターブック・シールブック・特別版）、発売日が先月 1 日より前
- **行**: `ndl` 収集器と同じ `ndl:<ISBN>`（source `ndl`、kind `book`）。シリーズ名・巻は `text.split_volume_title`（上の「JPRO の近刊の紐付け」。`link` が同じ関数で分け直す）、叢書名の取り出しは `ndl` 収集器の関数を使う。題名の「（仮）」は落とす。`extra.via = "jpro"`、`extra.jpro` に元の題名・巻・叢書名・ジャンル語。書影は入れない（`https://ndlsearch.ndl.go.jp/thumbnail/<isbn>.jpg` は Referer が NDL Search 以外だと 403。アプリの origin から `<img>`・Referer なし・`no-referrer` で確認。openBD にも無かった）
- **既存の行との統合**: 目録済みの行（`via` なし）は題名・叢書名・種類を保ち、JPRO の発売日が細かい時だけ日付を取る。前の JPRO の行は新しい登録で上書きし、細かい日付は残す。目録ができると `ndl` 収集器が同じ行を上書きし、JPRO の日付は `_keep_finer_dates` で残る
- **区切りと再開**: `day:<YYYY-MM-DD>`。`build` は直近 30 日のうち `done` でない日、`--fresh` は記録を消して 30 日、`--recent` は前回最後まで終わった実行の1日前から今日まで（`done` でも）。途中で止めた日は次の実行で最初から。`--limit` で止めた日は `pending`（`limit`）
- **所要時間（2026-10-15 実測）**: 1 リクエスト 1.3〜9.5 秒（夕方は平均 5〜9 秒。キャッシュに当たると速い）。試運転: 300 行で 16 リクエスト 2分31秒、520 行で約 25 リクエスト 1分54秒。直近 30 日（9/16〜10/15）はおよそ 23,000 件 ≒ 120 ページ＋空の日など ≒ 150 リクエストで 12〜22 分、残るのは約 1 割（2,000〜2,500 冊）。`--recent` は週 1 回なら 8 日 ≒ 50 リクエストで 4〜8 分
- **判定の精度（2026-10-15）**: 10/14 の全 473 件で漫画 87・小説 39、除外はジャンル 241・形態 55・発売日 22・電子書籍 15・成人 7・除外語 7。10/12〜10/15 の試運転で残った 500 冊を目で確かめ、明らかな誤りは 7 冊（写真集 2・麻雀の戦術書・公式ビジュアルブック・キャラクターブック・シールブック・特別版）で、上の規則で落とすようにした。残る境界例は戯曲・古典の現代語訳・児童向けの動物ノンフィクション・画集寄りの短編集・漫画誌の単行本化・特典付きの版（およそ 2%）
- **紐付け**: JPRO の行のうち 206 冊は AniList の作品に結び付き（シリーズ名一致）、64 冊は目録済みの本の棚に入り、230 冊は JPRO だけの棚になった（新しいシリーズ、または叢書名が別名のもの）。題名の分け方と作者での裏取りを直した後（2026-10-15、全 2,620 行）は、1冊だけの作品が 1,241 → 937（上の「JPRO の近刊の紐付け」）
- **同期での利用**: `/api/ndl/series` がカタログの JPRO の行を読み取り専用で足す（`backend/catalog/upcoming.py`。specs/data-coverage/design.md の「カタログの近刊を足す」「近刊の叢書名のゆれ」）
