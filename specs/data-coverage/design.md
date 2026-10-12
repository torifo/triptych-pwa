# triptych Data Coverage Design

## Overview

requirements.md の US-101〜106 を、既存の 2 層構成（PWA + collector）のまま実現する。並列実装の前に、共有する契約（DTO・エンドポイント・Dexie v2 スキーマ）をこの文書で固定する。各タスクはこの契約だけを前提に実装し、互いのファイルに触れない。

## 実機で確認した外部仕様（2026-10-13）

| ソース | 確認した事実 |
|---|---|
| なろう API | `title=1` でタイトルのみ検索。`type=re` で連載作品のみ（短編の感想文を除外）。`order=hyoka` で評価順。`end`: **0=完結済み連載または短編、1=連載中**。`noveltype`: 1=連載、2=短編。`of=t-n-w-ga-gl-e-nt` で必要項目を取得 |
| NDL OpenSearch | `mediatype=books` で本のみ（1 は無効）。`from=2025` で刊行年以降に絞れる。`cnt` は最大500。**返却順は日付順ではない**。`dcterms:issued` は `2026.8` / `2026.8.25` / `2018.1-2020.1` / `[2019]` が混在。`dc:subject[@xsi:type=dcndl:NDC10/NDC9]` が付き、913.6=小説、726.1=漫画で振り分けられる。`dc:language` は空なので、翻訳は ISBN の 978-4 以外で除外する。`dcndl:seriesTitle` でジュニア文庫などの別系統を判別できる |
| NDL 近刊 | 出版予定（JPRO 由来）のレコードも入り、`mediatype=books` を付けても残る。近刊は `issued` が `2026-12-10` 形式（日精度）で、`seriesTitle` が無くタイトルに巻数が入る（例「本好きの下剋上　第三部　領主の養女10」）。`issued` は初版日に近く、書店発売日と数日ずれることがある。「ミニクリアファイル付き」など特装版も別 ISBN で入る |
| AniList | ブラウザからも CORS 可（今回は collector 経由のまま）。`relations` で ADAPTATION / SEQUEL / SOURCE を辿れる。**実際のレート上限は 30 req/min**（`x-ratelimit-limit: 30`）。今の自主制限 60/min は超過している |
| しょぼいカレンダー | robots.txt に **`Crawl-delay: 60`**（今のアダプターは 1 req/s で違反）。`json.php?Req=TitleSearch` でタイトルから TID、番組（Programs）から放送局と開始時刻が取れ、dアニメストア・Netflix・Prime・ABEMA など配信チャンネルも含む。`Count` は通算話数でシーズン内の話数ではない |
| カクヨム検索 | `/search?q=&order=popular` は robots で許可、作品ページと同じ `__NEXT_DATA__` で 20 件/ページ。`hasPublication` と `publicationEntryPath`（ISBN 入り）が付く場合がある |

見送ったソース: 版元ドットコム（書誌の利用可否が不明）、楽天ブックス API（鍵が必要。必要になったら任意設定で追加）、Google Books（鍵なしは日次枠 0 で 429）、ハーメルン（Cloudflare で 403）、ノベルアップ+（403）、pixiv ノベル（規約でクローラー収集を禁止）、Jikan（到達不能）、なろう R18 API（方針として扱わない）。

## 決定事項

- **アニメの日付**: 日本の放送慣習（しょぼいカレンダーと同じ朝5時区切り、JST）。JST 01:30 の放送は前日の日付で、時刻を `25:30` と表記する
- **日付の精度**: Release は `precision: 'day' | 'month' | 'year'` を持つ。`date` は並べ替え用に月初・年初で埋めるが、表示とカレンダー配置は精度に従う
- **巻の同一性**: ISBN（ハイフンなし ISBN-13）。`mergeRelease` は `externalId` があれば (editionId, kind, externalId) で照合し、無ければ従来どおり (editionId, kind, label)
- **手動 Release**: `origin: 'manual'` は同期で上書きも削除もしない
- **紙の系統（line）**: NDL の `seriesTitle` から番号部分を除いた文字列（例「TOジュニア文庫」）を line キーとし、無いものは `''`（本編）。近刊のように `seriesTitle` が無いレコードは、タイトル中の叢書名（「〜文庫」「〜ジュニア」など）があればそれを line、無ければ本編とする。スピンオフ（「〜ハンネローレの貴族院五年生〜」など）は同じ作品の本として本編 line に含める。Edition ごとに初回同期で件数最大の line を選んで `syncHints.ndlLine` に保存し、以後はその line の巻だけを Release にする
- **NDL の引き方**: 同期では `title=<Work タイトル>&mediatype=books&from=<2年前の年>&cnt=500` の1リクエスト（6時間キャッシュ）。初回の line 判定もこの結果で行う。除外: 978-4 以外の ISBN（翻訳）、電子書籍カテゴリ、ISBN なし、タイトルが Work タイトルで始まらないもの、除外語（アンソロジー・ファンブック・画集・設定資料・ガイド・カレンダー・ドラマCD・付き・特装版・限定版・同梱）、発行日が範囲（`2018.1-2020.1` のようなセット）のもの。同じ巻が既刊形式と近刊形式で二重に出たら ISBN で1つにする
- **AniList のレート**: 自主制限を 25 req/min にする（上限 30 に余裕を持たせる）
- **しょぼいカレンダー**: `Crawl-delay: 60` を守る。collector 内に専用キューを持ち、取得は 60 秒に1回。結果は 24 時間キャッシュ。キャッシュが無い時はエンドポイントが `{"status": "queued"}` を返し、次の同期で拾う。配信系チャンネル（ChLookup のチャンネルグループで判定）の最初の番組を「配信開始」とする
- **成人向け**: AniList の isAdult は除外、なろう R18 API は使わない

## 共有契約

### Backend DTO（`backend/dto.py`）

```python
DatePrecision = Literal["day", "month", "year"]
AiringStatus = Literal["NOT_YET_RELEASED", "RELEASING", "FINISHED", "CANCELLED", "HIATUS"]
AnimeFormat = Literal["TV", "TV_SHORT", "MOVIE", "SPECIAL", "OVA", "ONA", "MUSIC"]

class ReleaseDTO(BaseModel):
    kind: ReleaseKind
    label: str
    date: str                       # YYYY-MM-DD（month/year 精度は 1日・1月1日で埋める）
    precision: DatePrecision = "day"
    time: Optional[str] = None      # アニメの放送時刻（JST・朝5時区切り表記 例 "25:30"）
    source: Source
    externalId: Optional[str] = None  # volume=ISBN13 / episode="<anilistId>:<n>" / season-start=anilistId

class SearchResult(BaseModel):     # 既存 + 追加フィールド
    source: Source
    externalId: str
    mediaType: MediaType
    title: str
    cover: Optional[str] = None
    url: str
    subtitle: Optional[str] = None  # 作者・出版社・巻数など補足（表示用）
    direct: bool = False            # URL/ncode/ISBN からの直接解決なら True

class RelationEdge(BaseModel):
    relationType: str               # ADAPTATION / SEQUEL / SOURCE / PREQUEL / SIDE_STORY ...
    externalId: str                 # AniList id
    type: Literal["ANIME", "MANGA"]
    format: Optional[str] = None    # TV / MOVIE / NOVEL / MANGA ...
    title: str                      # native 優先
    status: Optional[AiringStatus] = None
    startDate: Optional[str] = None
    startPrecision: Optional[DatePrecision] = None
    isAdult: bool = False

class RelationsDTO(BaseModel):
    externalId: str
    type: Literal["ANIME", "MANGA"]
    format: Optional[str] = None
    title: str
    relations: list[RelationEdge]

class DetectionCandidateDTO(BaseModel):  # 既存 + 追加
    source: Literal["anilist"] = "anilist"
    externalId: str
    title: str
    airingStatus: AiringStatus
    format: Optional[str] = None
    startDate: Optional[str] = None
    startPrecision: Optional[DatePrecision] = None
    matchScore: float               # relation 由来は 1.0
    via: Literal["relation", "sequel", "title"] = "title"

class NdlLine(BaseModel):
    key: str                        # '' = 本編
    label: str                      # 表示名（'本編' / 'TOジュニア文庫' など）
    count: int

class NdlSeriesDTO(BaseModel):
    lines: list[NdlLine]
    line: str                       # 採用した line キー
    releases: list[ReleaseDTO]      # 採用 line の巻。発行日の新しい順、最大30件

class BroadcastDTO(BaseModel):      # しょぼいカレンダーの番組1件
    channel: str                    # 放送局・配信サービス名
    isStreaming: bool               # 配信系チャンネルか
    date: str                       # YYYY-MM-DD（朝5時区切り）
    time: str                       # "25:30" 形式
    episodeCount: Optional[int] = None  # しょぼかるの Count（通算話数）

class SyoboiTitleDTO(BaseModel):
    status: Literal["ok", "queued", "not_found"]
    tid: Optional[str] = None
    title: Optional[str] = None
    programs: list[BroadcastDTO] = Field(default_factory=list)  # 今日以降の番組（最大50件）
    streamStarts: list[ReleaseDTO] = Field(default_factory=list) # 配信サービスごとの最初の番組 → kind="stream-start"

class WebNovelDTO(BaseModel):       # なろう・カクヨム共通
    source: Literal["narou", "kakuyomu"]
    externalId: str
    title: str
    author: Optional[str] = None
    url: str
    episodeCount: int
    completed: bool
    isShortStory: bool = False
    lastUpdate: Optional[str] = None  # YYYY-MM-DD（JST）
```

### Backend エンドポイント

すべて `?refresh=1` を受け付け、付いていればキャッシュを読まずに取り直す。

| Method | Endpoint | 返り値 | 担当 |
|---|---|---|---|
| GET | `/api/anilist/media/{id}` | MediaDetailDTO（releases は JST・朝5時区切り・precision 付き。startDate が年のみ/年月のみでも Release を出す） | B1 |
| GET | `/api/anilist/relations/{id}` | RelationsDTO（isAdult は除外済み） | B1 |
| GET | `/api/anilist/resolve-source?title=&kind=novel\|manga` | `list[SearchResult]`（AniList の NOVEL/MANGA 候補。副題・前方一致を許容したスコア順、最大5件） | B1 |
| GET | `/api/anilist/detect?title=` | `list[DetectionCandidateDTO]`（via=title、isAdult 除外） | B1 |
| GET | `/api/ndl/series?title=&kind=novel\|manga&line=` | NdlSeriesDTO | B2 |
| GET | `/api/openbd/{isbn}` | 既存（pubdate を precision 付きで返す） | B2 |
| GET | `/api/narou/novel/{ncode}` | WebNovelDTO | B3 |
| GET | `/api/kakuyomu/work/{id}` | WebNovelDTO | B3 |
| GET | `/api/search?q=&types=` | `{results: SearchResult[], errors: [...]}`。AniList（isAdult 除外）＋なろう（title=1/type=re/order=hyoka）＋カクヨム（`/search?order=popular` の先頭20件）＋NDL 書籍（シリーズ単位に集約）。q が URL（なろう・カクヨム・AniList）・ncode・ISBN なら直接解決して `direct: true` の1件を返す | B3 |
| GET | `/api/syoboi/title?tid=` または `?title=` | SyoboiTitleDTO（TID 未知ならタイトルで解決。Crawl-delay 60 のキューを通す） | B4 |
| GET | `/api/health` | 各アダプターの直近エラーを実際に記録して返す | B3 |

### Frontend スキーマ（`src/lib/db/schema.ts`、Dexie v2）

```ts
export type DatePrecision = 'day' | 'month' | 'year';

export interface Release {
  id: string;
  editionId: string;
  kind: 'volume' | 'episode' | 'season-start' | 'stream-start';
  label: string;
  date: string;              // YYYY-MM-DD
  precision: DatePrecision;  // v2
  time?: string;             // v2 '25:30'
  externalId?: string;       // v2 ISBN13 / '<anilistId>:<n>' / anilistId
  origin: 'sync' | 'manual'; // v2 manual は同期で触らない
  dateHistory: ReleaseDateHistoryEntry[];
  source: Source;
  seenAt?: string;
  createdAt: string;
  updatedAt: string;
}

export interface Edition {
  // 既存フィールドに追加
  completed?: boolean;       // v2 原作側の完結
  syncHints?: {              // v2
    ndlLine?: string;        // 採用した NDL の line キー
    anilistSourceId?: string;// 検知用に解決した AniList NOVEL/MANGA の id
  };
}

export interface DetectionEvent {
  // 既存フィールドに追加
  via: 'relation' | 'sequel' | 'title'; // v2（既存は 'title'）
  candidate: DetectionCandidate & { format?: string; startPrecision?: DatePrecision };
}
```

Dexie v2:
- `releases: 'id, editionId, date, [editionId+kind+externalId]'`
- upgrade: 既存 Release に `precision='day'`・`origin='sync'` を補う。既存 DetectionEvent に `via='title'` を補う。**mediaType が anime 以外の Edition に付いた source='anilist' の Release は削除する**（監査 P2 の誤データ）

## タスク分担（ファイル所有）

| ID | 担当範囲 | 所有ファイル |
|---|---|---|
| W0 | 契約の先行反映（型・DTO の定義だけ） | `backend/dto.py`, `src/lib/db/schema.ts`（型と v2 スキーマ） |
| B1 | AniList: JST・朝5時区切り・精度・relations・resolve-source・isAdult・refresh・ステータス | `backend/adapters/anilist.py`, `backend/api/anilist.py`, 対応テスト・fixture |
| B2 | NDL/openBD: books 絞り・全件取得→日付降順・ISBN 978-4・NDC 振り分け・line・除外語・openBD フォールバック・精度 | `backend/adapters/{ndl,openbd}.py`, `backend/api/{ndl,openbd}.py`, テスト・fixture |
| B3 | なろう/カクヨム/検索/health: なろう検索パラメータ・end 反転修正・noveltype・WebNovelDTO・カクヨム JST・カクヨム検索・NDL 書籍検索・直接解決・health 記録 | `backend/adapters/{narou,kakuyomu}.py`, `backend/api/{narou,kakuyomu,search}.py`, `backend/app.py`, テスト |
| B4 | しょぼいカレンダー: Crawl-delay 60 のキュー・タイトルから TID 解決・番組一覧・配信チャンネル判定・配信開始 Release | `backend/adapters/syoboi.py`, `backend/api/syoboi.py`, テスト・fixture |
| F1 | データ層: v2 移行の upgrade・store（mergeRelease v2・手動 Release CRUD・sourceRef 追加削除）・JST 日付ヘルパー | `src/lib/db/store.ts`, `src/lib/db/migrations`（必要なら）, `src/lib/dates.ts`, テスト |
| F2 | 同期・検知: mediaType 別ルーティング・NDL 新刊・ローリング最新話・current 不変・再試行・relations 検知・重複排除 | `src/lib/sync/{engine,detect}.ts`, テスト |
| F3 | UI: Home（JST・新着の絞り込み）・Calendar（精度対応）・WorkDetail/EditionCard（sourceRef 編集・Release 編集・完結表示）・Search（NDL・直接解決・登録済み）・Settings（検知エラー） | `src/routes/*`, `src/lib/components/*` |
| V | 統合検証（実 API・ブラウザ） | 修正は軽微なもののみ |

## Testing Strategy

- **Backend**: 実機で確認した事例を fixture に固定する。本好きの NDL 149 件から本編最新巻が日付降順で取れること、ジュニア文庫・翻訳・アンソロジーが除外されること、openBD 未収録の巻が NDL の発行日で残ること、`2026.8` が month 精度になること、JST 01:30 の放送が前日・`25:30` になること、無職転生・薬屋の relations から新期が出ること、なろうの end=0 が完結になること
- **Frontend**: v2 移行（誤データ削除・既定値補完）、ISBN キーのマージで日付が往復しないこと、手動 Release を同期が触らないこと、初回同期で current が変わらないこと、全失敗時に lastSyncAt が進まないこと、他 Work 登録済みアニメを再提案しないこと、JST 0〜9時の「今日」
- **統合**: 本好き・薬屋・転スラ・無職転生を実 API で登録→同期→検知まで通す
