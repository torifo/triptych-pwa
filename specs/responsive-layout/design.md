# triptych Responsive Layout Design

## Breakpoint

- PC: `@media (min-width: 64rem)`（1024px）。Tailwind の `lg:` と同じ値なので、マークアップ側は `lg:` を使ってよい
- `<style>` の中では `@media (min-width: 64rem)` と書く（独自のカスタムメディアは使わない）
- JS で幅が要る所だけ `src/lib/viewport.svelte.ts` の `viewport.wide`（`matchMedia('(min-width: 64rem)')`）を読む。構造が変わる所（カレンダーの升目に出す表紙の数など）に限る

## Shell（`src/App.svelte`）

- スマホ: 今のまま（最大 480px の1列・下のタブ・A2HS の帯）
- PC: 外枠の `max-width` を外し、ヘッダーを「■ triptych｜タブ（アイコン＋ラベル、選択中は黄の下線）｜右端に ON AIR n」に。下のタブと A2HS の帯は出さない。`main` の下の余白は下のタブ分を取らない
- 中身の箱: 各画面の一番外の `<section>` に共通クラス `.page` を付ける。`.page` はスマホでは今の余白（16px）、PC では `max-width: 1200px; margin: 0 auto; padding: 40px 48px 48px`（本人「周りに余裕がなくてキツキツ」→ 広げた）

## 画面ごとの PC の形

| 画面 | PC の形 |
|---|---|
| Home | 「今日」は全幅の横並び（表紙を大きく）。下の節は2列（直近・新着など） |
| Library | 棚は `repeat(auto-fill, minmax(140px, 1fr))`。一覧表示は2列 |
| Work 詳細・カタログ詳細 | 左に表紙の列（280px、sticky）、右に中身 |
| カタログ | 絞り込みは上、結果は `auto-fill, minmax(140px, 1fr)` の表紙の格子 |
| 検索 | 結果を2列 |
| 設定 | 節を2列（CSS grid、節の途中で割らない） |
| カレンダー | 下の節 |

## カレンダー

### 分類（純関数、`src/lib/calendar/category.ts`）

- `type DayCategory = 'program' | 'release' | 'web'`
- Library の Release: Edition の `mediaType` が anime → program、web-novel → web、それ以外（紙の小説・漫画）→ release
- カタログの出来事: `kind === 'anime'` → program、`kind === 'book'` → release
- 見出し: program＝「番組」（赤いランプ）、release＝「発売」（黒い■）、web＝「Web」（印なし）

### 絞り込みのタブ

- 今の媒体チップ（すべて／アニメ／本／Web）を、分類のタブ「すべて｜● 番組｜■ 発売｜Web」に置き換える。値は今の `MediaFilter`（all/anime/book/web）のまま、ラベルと印だけ変える
- 選択は端末に保存する（`triptych:calendar-filter`）

### 日の一覧（週・月で選んだ日・リスト）

- 段に分ける: 「● 番組 n」→「■ 発売 n」→「Web n」。n は Library とカタログの合計。空の段は出さない
- 段の中: Library の行（今の ReleaseRow）が先、カタログの出来事（今の CatalogEventRow、多ければ5件＋「ほか n 件」）が後
- 番組の段は時刻順。発売の段は Library → カタログ（人気順）

### 月の升目

- 件数: 日付の下に `●17`（赤）と `■9`（黒）を等幅で。0 の分類は出さない
- 表紙: スマホは2枚、PC は4枚（`cellCovers` の max を viewport で切り替え）。今の「+n」は件数に置き換わるので出さない
- PC は升目の高さを上げる（最小 120px）

### 週

- スマホ: 今の縦の並び（日ごとに段を分ける）
- PC: 7日を横に並べた7列。各列に日付の見出し、その下に番組・発売の段

## テスト

- `category.ts` の分類と集計は vitest で固定する
- 画面は Chrome で 1280・1024・375・320px、ライト／ダークを目で確かめる
