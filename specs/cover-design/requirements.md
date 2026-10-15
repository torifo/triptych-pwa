# triptych Cover Design Requirements

## Overview

画面が文字と枠だけで質素に見える。紙面のモノクロの骨格（mono の罫線・見出し・黄のマーカー）は残し、色は作品の表紙に持たせる。カタログの AniList 由来 99,369 件には表紙があり、Edition も `coverUrl` を持てるのに、今は検索結果でしか表紙を使っていない。

放送局らしさ（ON AIR の表示・等幅の時刻・局名）はサブとして控えめに入れる。

自作スタディの横断フィードバック（furniture/urban）「白黒の中では色の付いた所に目が行く。画像をカラーにして目立たせる」に沿う。

## User Stories

### US-301: どの画面でも作品を表紙で見分けられる
- THE SYSTEM SHALL Home・Library・Work 詳細・カタログ（一覧・詳細）・カレンダー・検索で、作品や Release の行に表紙を出す
- WHEN 表紙の画像が無い、または読み込みに失敗した時 THE SYSTEM SHALL 題名を縦に組んだ「文字の表紙」（生成りの面＋媒体名）を出す
- THE SYSTEM SHALL 表紙の比率を 2:3 に揃え、読み込み中は無彩色の面で場所を確保する（レイアウトが動かない）
- THE SYSTEM SHALL 表紙に作品名の alt を付ける

### US-302: 表紙の出どころを増やす
- WHEN アニメの同期で AniList の作品情報を取った時 AND Edition に表紙が無い時 THE SYSTEM SHALL AniList の表紙を Edition に保存する
- WHEN カタログから本（NDL）を Library に追加する時 AND その本に表紙が無い時 THE SYSTEM SHALL 同じカタログ作品の AniList（漫画・小説）の表紙を使う
- THE SYSTEM SHALL ユーザーが手で設定した表紙を同期で上書きしない

### US-303: Home を開いた時に今日の作品が目に入る
- THE SYSTEM SHALL 今日放送・配信・発売の Release を「今日」として表紙の横並びで出し、表紙の上に時刻を載せる
- THE SYSTEM SHALL 新着を表紙のサムネ付きの行で出す
- WHEN Library が空の時 THE SYSTEM SHALL カタログから今期の放送中の作品を表紙で見せ、カタログ・検索へ誘う（空白の画面にしない）

### US-304: Library を表紙の棚として眺められる
- THE SYSTEM SHALL Library を表紙の3列の棚で出し、一覧表示にも切り替えられる（選択は端末に保存する）
- THE SYSTEM SHALL 放送中の Edition の表紙に ON AIR のランプを付ける

### US-305: Work 詳細とカタログ詳細を表紙で始める
- THE SYSTEM SHALL 大きな表紙と、表紙をぼかした淡い背景のヘッダーで詳細を始める
- THE SYSTEM SHALL Work 詳細の Edition をタブで切り替えられる
- THE SYSTEM SHALL 次話を黒い帯で出し、時刻を等幅で出す

### US-306: 放送局の要素をサブとして入れる
- THE SYSTEM SHALL ヘッダーに「ON AIR n」（今日放送・配信がある追跡中の Edition の数）を出す。0 の時は出さない
- THE SYSTEM SHALL 時刻・カウントダウン・日付を等幅（DM Mono）で組む
- WHEN しょぼいカレンダーの番組表を取った時 THE SYSTEM SHALL 今日以降の番組（局名・日付・時刻・配信か）を Edition ごとに最大 8 件保存し、Work 詳細の次話に局名を出す
- WHERE ユーザーが動きを減らす設定にしている時 THE SYSTEM SHALL ランプの点滅などの動きを止める

## Non-Functional Requirements

- アイコンは Lucide（`@lucide/svelte`）を使い、UI に絵文字を使わない
- 左に色帯の付くカード（border-left のアクセント）を使わない。階層は背景・余白・文字で作る
- ライト・ダークの両方で読める。375px・320px で横スクロール・はみ出し・不自然な折り返しが無い
- Chrome 専用の CSS を使わず、Safari・Firefox でも同じに見える書き方にする
- 表紙は遅延読み込み（`loading="lazy"`・`decoding="async"`）。一覧のスクロールが重くならない
- 既存のテスト（vitest・pytest）と `npm run check` 0/0 を保つ

## Out of Scope

- レコメンド（別機能として後で作る）
- NDL・openBD の書影（2026-10-15 時点で外部から取得できない: NDL 403・openBD 404）
- 表紙画像のキャッシュ用プロキシ（ブラウザのキャッシュに任せる）
