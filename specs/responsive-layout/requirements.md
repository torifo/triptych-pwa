# triptych Responsive Layout Requirements

## Overview

今はどの画面も幅 480px の1列で、PC でもスマホの形がそのまま真ん中に出ている。幅 1024px 以上を PC 画面として、ナビを上部のタブにし、中身を全幅で使う。スマホ（1024px 未満）は今の1列＋下のタブのまま。スマホからの接続方法（LAN・Tailscale）はいったん置き、画面だけ先に整える。

カレンダーは放送と本の発売が1本の一覧に混ざり、右端の小さな「放送」「発売」でしか見分けられない。放送局の ON AIR の型（赤いランプ●＋等幅の時刻）を番組に、黒い札■を発売に当て、一覧を分け、タブでも切り替えられるようにする。

本人の選択（2026-10-16）: PC は「上部タブ＋全幅」。番組と発売は「ON AIR の型で分ける。タブ切り替えのようなものもあってよい」。

## User Stories

### US-401: PC では上部のタブで画面を移る
- WHEN 画面の幅が 1024px 以上の時 THE SYSTEM SHALL ナビ（HOME・検索・LIBRARY・CATALOG・CALENDAR・SETTINGS）をヘッダーの横並びのタブで出し、下のタブを出さない
- WHEN 画面の幅が 1024px 以上の時 THE SYSTEM SHALL 中身を最大 1200px で組み、周りに余白を取る（左右 48px・上 40px。端まで詰めない）
- WHEN 画面の幅が 1024px 未満の時 THE SYSTEM SHALL 今の1列（最大 480px）と下のタブを変えない
- THE SYSTEM SHALL ヘッダーの ON AIR n を PC でもスマホでも出す（PC ではタブの右端）

### US-402: PC では各画面が横に広い形になる
- THE SYSTEM SHALL PC 幅で、Library・カタログ・検索の表紙を幅に合わせた多列で並べる
- THE SYSTEM SHALL PC 幅で、Work 詳細とカタログ詳細を「表紙の列｜中身の列」の2列にする
- THE SYSTEM SHALL PC 幅で、Home の節を2列に組める所は2列にする
- THE SYSTEM SHALL PC 幅で、設定の節を2列に並べる
- THE SYSTEM SHALL スマホ幅の見た目と操作を変えない（375px・320px で横にはみ出さない）

### US-403: カレンダーで番組と発売を見分けられる
- THE SYSTEM SHALL 放送・配信・放送開始・公開（アニメ）を「番組」、本の発売を「発売」、Web 小説の更新を「Web」として分ける
- THE SYSTEM SHALL 番組を赤いランプ●（ON AIR の赤）と等幅の時刻で、発売を黒い四角■の札で示す。紙面のモノクロ（色は表紙と ON AIR だけ）は崩さない
- THE SYSTEM SHALL 日の一覧を「● 番組 n」「■ 発売 n」（あれば「Web n」）の段に分け、各段に Library の予定を先、カタログの出来事を後に並べる
- THE SYSTEM SHALL 月の升目に番組と発売の件数（●n ■n）を出す
- THE SYSTEM SHALL 媒体の絞り込みを「すべて｜● 番組｜■ 発売｜Web」のタブにし、選んだ分類だけを升目・一覧・週に出す
- WHEN PC 幅の時 THE SYSTEM SHALL 月の升目を大きくして表紙を多く出し、週は7日を横に並べる

## Non-Functional Requirements
- 切り替えの幅は CSS の `@media (min-width: 64rem)`（Tailwind の `lg:` と同じ 1024px）に揃え、JS で幅を読まない（読む時は同じ値）
- vitest・`npm run check` 0/0・ビルドが通る
- Chrome で 1280px・1024px・375px・320px、ライト／ダークを確かめる（Safari・Firefox の実機は人手）
