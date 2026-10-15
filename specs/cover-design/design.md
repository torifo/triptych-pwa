# triptych Cover Design Design

## Overview

紙面はモノクロ、色は表紙だけ。共通の表紙部品と放送局の小さな部品を先に作り（Wave 0）、画面ごとの改修を並列で載せる（Wave 1）。データ側は「同期で表紙を埋める」「番組表を保存する」「本はカタログの AniList 表紙を借りる」の3点だけを足す。

## 見た目の決まり

| 要素 | 決まり |
|---|---|
| 地と文字 | 既存の `--bg` / `--fg` / `--muted` / `--line`。黄のマーカー（`--hi`）はページ見出しの一点だけ |
| 色 | 表紙の画像だけが色を持つ。UI 側で新しく足す色は ON AIR の赤（`--onair`）1色のみ |
| 表紙の面 | 比率 2:3、角丸 2px、`--line` の細い縁。読み込み中は `--cover-ph`（無彩色） |
| 文字の表紙 | `--paper`（生成り）の面に題名を縦組み（`writing-mode: vertical-rl`）、下に媒体名。ダークでは `--paper` を暗い生成りに |
| 等幅 | 時刻・日付・カウントダウン・話数は `--mono`（DM Mono 400/500）＋ `font-variant-numeric: tabular-nums` |
| ON AIR | `--onair` の 6px の丸。今日放送・配信中のものだけに付ける。点滅は 2 秒周期の控えめなもので、`prefers-reduced-motion: reduce` では止める |
| 次話の帯 | 黒地（ダークでは白地）に白文字の帯。左にランプ、右に等幅の日時 |
| アイコン | `@lucide/svelte`。16〜20px。装飾は `aria-hidden` |
| 禁止 | 絵文字、border-left のアクセントカード、`word-break: auto-phrase` などの Chrome 専用 CSS |

追加するトークン（`src/app.css` の `:root` と `.dark`）: `--onair`, `--cover-ph`, `--paper`, `--paper-ink`, `--mono`。

## 共有契約（Wave 0 で作る）

### 依存
- `@lucide/svelte`（アイコン）、`@fontsource/dm-mono`（400・500）

### `src/lib/components/cover.ts`
```ts
export type CoverSize = 'xs' | 's' | 'm' | 'l' | 'fill'; // 幅 28 / 48 / 64 / 112px（l は詳細ヘッダー）/ fill は親の幅いっぱい（棚）
export interface CoverSource { url?: string; title: string; mediaType?: MediaType }
/** 表紙の URL（http/https のみ）。無ければ undefined。 */
export function coverUrlOf(edition: Pick<Edition, 'coverUrl'> | undefined): string | undefined;
/** Work の代表表紙: coverEditionId → 表紙のある最初の Edition（アニメ優先） */
export function workCover(work: Work, editions: readonly Edition[]): CoverSource;
/** 文字の表紙に組む題名（副題を落とし、長ければ 12 字で切る）と媒体名 */
export function fallbackCoverText(title: string, mediaType?: MediaType): { title: string; media: string };
```

### `src/lib/components/Cover.svelte`
- props: `src?: string`, `title: string`, `mediaType?: MediaType`, `size?: CoverSize`（既定 's'）, `onAir?: boolean`, `badge?: Snippet`（表紙の左下に載せる等幅の時刻など）
- `<img loading="lazy" decoding="async" alt={title}>`。`error` で文字の表紙に切り替える
- `onAir` の時は右上に `OnAirLamp`

### `src/lib/components/OnAirLamp.svelte` / `MonoTime.svelte`
- `OnAirLamp`: `--onair` の丸。`label?: string`（例 'ON AIR 2'）を渡すと横に等幅で出す
- `MonoTime`: `--mono` で日時・時刻・カウントダウンを出す小さな部品（`text`, `muted?`）

### `src/lib/components/CoverHero.svelte`
- Work 詳細とカタログ詳細の共通ヘッダー。props: `src?`, `title`, `subtitle?`, `mediaType?`, `children?: Snippet`
- 背景は同じ画像を `filter: blur(24px)`・拡大して敷き、上に `--bg` の 78% の面を重ねる（色抽出はしない。CORS 不要）。画像が無い時は `--paper`

### `src/lib/components/onair.ts`
```ts
/** 今日（JST 朝5時区切り）放送・配信がある追跡中 Edition の id */
export function onAirEditionIds(releases: readonly Release[], editions: ReadonlyMap<string, Edition>, today: string): Set<string>;
/** 「あと 3:12」形式（24 時間以内のみ。過ぎたら undefined） */
export function countdownLabel(release: Pick<Release, 'date' | 'time'>, now: Date): string | undefined;
```
放送・配信は episode と stream-start（rolling を含む）。ローリングの次回配信が今日なら ON AIR に数える。

### データ（`src/lib/db/schema.ts`）
```ts
export interface EditionSyncHints {
  // 既存: ndlLine, anilistSourceId, syoboiTid
  broadcasts?: BroadcastHint[]; // v2 しょぼかるの今日以降の番組（最大 8・日時順）
  coverFrom?: 'anilist' | 'catalog'; // 自動で入れた表紙の出どころ。未設定で coverUrl がある＝手で設定（上書きしない）
}
export interface BroadcastHint { channel: string; date: string; time: string; isStreaming: boolean; episode?: number }
```
索引の無い項目なので Dexie の版は上げない。

## 画面ごと（Wave 1）

### Home（`src/routes/Home.svelte`）
- 「今日」: 今日の episode / stream-start / volume / chapter を表紙 m の横並び（横スクロール・スナップ）。表紙の左下に時刻の badge、下に作品名と「#14」「あと 3:12」または局名
- 「直近」「新着」: 既存の ReleaseRow に表紙 xs を付ける（ReleaseRow に `cover?: CoverSource` を足す）
- Library が空の時: 「今期の放送中」— `fetchCatalogWorks({ status: 'RELEASING', year, season, sort: 'popularity', per: 12 })` の表紙を3列で出し、「カタログを見る」「検索する」の2つのボタン。カタログ未作成・取得失敗の時は既存の空表示＋ボタンだけ

### ヘッダーとナビ（`src/App.svelte`）
- ロゴの右に `OnAirLamp label="ON AIR n"`（n>0 の時だけ）。数は Home と同じ `onAirEditionIds`
- 下部ナビは各項目に Lucide アイコン（house / search / library / book-open / calendar / settings）を足し、文字は今の大きさのまま下に置く。375px・320px で詰まらないこと

### Library（`src/routes/Library.svelte`）
- 既定は表紙の3列の棚: 表紙 fill（列幅いっぱい）、下に作品名1行・状態と媒体。ON AIR の Edition はランプ
- 右上の切り替え（Lucide grid / list）で既存の一覧表示に戻せる。選択は `localStorage`（try/catch）
- 同じ作品の Edition を見分ける2行目（既存 `editionDisambiguators`）は一覧・棚の両方で出す

### Work 詳細（`src/routes/WorkDetail.svelte`, `src/lib/components/{EditionCard,ReleaseList}.svelte`）
- `CoverHero`（代表表紙・作品名・媒体の並び）
- Edition が2つ以上ならタブ（媒体名＋見分け用の副題）で切り替え。1つならタブ無し
- 「次話と配信先」の先頭を次話の帯にし、帯の下に `syncHints.broadcasts` の局名と時刻（今日以降の先頭3件）、続けて配信先（既存の stream-start）
- 既存の編集・削除・追加の操作は変えない

### カタログ（`src/routes/{Catalog,CatalogDetail}.svelte`, `src/lib/components/CatalogEntryRow.svelte`）
- 一覧の行の左に表紙 s（`CatalogWorkSummary.cover`）。媒体の箱は今のまま
- 詳細は `CoverHero`。追加ボタンなど既存の操作は変えない

### カレンダー・検索（`src/routes/{Calendar,Search}.svelte`）
- 日ごとの一覧の行に表紙 xs（ReleaseRow 経由）、時刻は `--mono`
- 検索結果の表紙は `Cover` に置き換えて見た目を揃える

## データの改修（Wave 1）

### 同期（`src/lib/sync/engine.ts`）
- `syncAnimeMedia`: `dto.cover` があり、Edition の `coverUrl` が空か `syncHints.coverFrom === 'anilist'` の時だけ `coverUrl` を更新し `coverFrom: 'anilist'`
- `syncSyoboi`: `dto.programs` の今日以降を日時順に最大 8 件 `syncHints.broadcasts` に保存（中身が同じなら書かない）。放送終了で照会しない時は消さずにそのまま

### カタログからの追加（`src/lib/catalog/register.ts`）
- 本（kind=book）の Edition に表紙が無い時は、同じカタログ作品の entries から AniList の漫画・小説（無ければアニメ）の表紙を使い `coverFrom: 'catalog'`

## Error Handling

- 画像の読み込み失敗・URL が http(s) でない → 文字の表紙
- カタログ API の失敗（Home の今期の放送中）→ その枠を出さず、ボタンだけ出す
- `localStorage` が使えない → 既定の棚表示

## Testing Strategy

- vitest: `cover.ts`（代表表紙の選び方・文字の表紙の切り方・URL の検査）、`onair.ts`（今日の数え方・朝5時区切り・カウントダウン）、同期の表紙の上書き条件と番組の保存、register の表紙の借り方
- 画面: `npm run check` 0/0、ビルド後に 375px・320px・ダークで全画面のスクリーンショット確認（メイン）
- 実機（人手）: iOS Safari・Firefox での表示
