# triptych Data Coverage Requirements

## Overview

MVP の収集経路を、ローカル運用（Mac 上の collector）前提で「できるだけ広く、正確に」拾えるように直す。監査（2026-10-13）で見つかった取りこぼしと誤りを潰し、紙の新刊・アニメの新期・Web 小説の更新を、手作業なしで正しい日付で反映できるようにする。

公開（GitHub Pages など）はこのスコープ外。collector が手元で動く前提でよい。

## 監査で確定した問題（要約）

| # | 問題 | 影響 |
|---|---|---|
| P1 | 紙の新刊が拾えない。NDL を返却順の先頭20件で打ち切り、巻ラベルが衝突し、openBD 未収録の新刊を捨てている | 高 |
| P2 | 検索から登録した文庫・漫画は sourceRefs が anilist だけで、新刊探索の経路に乗らない。代わりにアニメ用の処理が走り、過去日の無意味な Release が付く | 高 |
| P3 | アニメ化検知がタイトル照合だけで、続編・副題つきの新期（無職転生 0.27、薬屋第3期 0.73）が閾値 0.85 に届かない | 高 |
| P4 | 日付がずれる。AniList の放送時刻を UTC で日付化し、Home の「今日」も UTC。年だけ・年月だけの日付を `-01-01` で埋めて精度を偽る | 高 |
| P5 | 検索が AniList となろうだけ。なろう検索はあらすじまで対象・新着順のため、本物が20件の外に出る。紙の一般小説は検索に出ない | 中〜高 |
| P6 | 手動で追加した Edition は同期されず、sourceRefs や Release を手で足す UI もない | 高 |
| P7 | 同期・検知が失敗してもスロットルを進めるので、しばらく再試行されない。検知エラーが UI に出ない | 中 |
| P8 | なろうの完結判定が逆（end: 0=完結/短編、1=連載中）。初回同期で既読位置が全話になる。`第N部分` の Release が更新ごとに増える | 中 |
| P9 | 「新着」に過去分・追跡外が混ざる。同じ作品を二重登録できる | 中 |

## User Stories

### US-101: 紙の新刊を正しく拾う
**As a** P1 **I want to** 追跡中の文庫・漫画の新刊（予定を含む）を、正しい巻と発売日で自動で知りたい **So that** 買い逃さない

- WHEN 同期が走った時 THE SYSTEM SHALL mediaType が light-novel または manga の追跡中 Edition について、sourceRefs の有無に関わらず Work/Edition のタイトルで NDL を引き、新刊候補を取得する
- THE SYSTEM SHALL NDL の結果を本（mediatype=books）に限り、日本の ISBN（978-4）を持つものだけにし、NDC 分類で小説（913.x）と漫画（726.x）を Edition の種別に合わせて振り分ける
- THE SYSTEM SHALL 候補を発行日の新しい順に並べてから件数を絞る（返却順で打ち切らない）
- IF openBD に書誌が無い THEN THE SYSTEM SHALL NDL の発行日を使い、候補を捨てない
- THE SYSTEM SHALL 巻の同一性を ISBN で判定し、同じ ISBN の日付が変わった時だけ dateHistory に記録する
- THE SYSTEM SHALL 本編・ジュニア文庫・コミカライズなど別系統の本を、タイトルとシリーズ名で区別し、Edition に紐づく系統の巻だけを Release にする

### US-102: 日付を正しい精度で扱う
- THE SYSTEM SHALL すべての Release に日付の精度（day / month / year）を持たせ、年だけ・年月だけの日付を特定の日として表示しない
- WHEN 精度が month の Release を表示する時 THE SYSTEM SHALL 「2026年8月」のように表示し、カレンダーでは日付セルではなく月の一覧に出す
- THE SYSTEM SHALL アニメの放送日を日本の放送慣習（朝5時区切り、JST）で日付化し、時刻を「25:30」形式で併記する
- THE SYSTEM SHALL フロントの「今日」と「直近7日」を JST の暦日で計算する

### US-103: アニメ化・新期を確実に検知する
- WHEN 追跡中の原作 Edition が AniList の NOVEL/MANGA エントリに紐づいている時 THE SYSTEM SHALL その relations（ADAPTATION / ANIME）を辿ってアニメを検知する
- WHEN 追跡中のアニメ Edition がある時 THE SYSTEM SHALL relations の SEQUEL を辿って新期を検知する
- IF 原作が AniList に紐づいていない THEN THE SYSTEM SHALL タイトルで AniList の NOVEL/MANGA を探して紐づけ候補とし、その relations で検証してから検知する。タイトル照合だけの検知は残すが、relations 由来より低い信頼度として区別して表示する
- THE SYSTEM SHALL 既にどれかの Work に登録済みのアニメを再提案しない
- THE SYSTEM SHALL 成人向け（isAdult）を検索・検知から除く

### US-107: アニメの配信日を拾う
- WHEN 追跡中のアニメ Edition を同期した時 THE SYSTEM SHALL しょぼいカレンダーで放送局・配信サービスごとの番組を（14日前から）取得し、まだ配信の無い配信サービスは、そのサービスの最初の番組を「配信開始」の Release として反映する
- IF 配信サービスが直近14日にも配信している（配信中）THEN THE SYSTEM SHALL 配信開始とはせず、次回の配信を1件のローリング Release（`rolling`・既読）として持つ。日付が動いても延期として記録せず、新着・直近7日・カレンダーには出さない。Work 詳細の「次話と配信先」の行と、次話の行の「◯◯ 配信中」で示す
- IF Edition の AniList id がすべて放送終了・中止 THEN THE SYSTEM SHALL しょぼいカレンダーを照会しない。しょぼいカレンダーは AniList のあとに照会する
- THE SYSTEM SHALL しょぼいカレンダーの `Crawl-delay: 60` を守り、取得できていない間は「取得待ち」として扱い、次の同期で反映する
- THE SYSTEM SHALL Work 詳細のアニメ面に、次回の放送・配信予定（局・日付・時刻）を表示する

### US-104: 検索を広げ、精度を上げる
- THE SYSTEM SHALL 検索に NDL の書籍タイトル検索を加え、巻ごとではなくシリーズ単位で1件にまとめて返す
- THE SYSTEM SHALL なろう検索をタイトル限定・連載作品・評価順で行う
- WHEN 入力が URL（なろう・カクヨム・AniList）、ncode、ISBN の時 THE SYSTEM SHALL 検索せずにその作品を直接解決して登録候補にする
- IF 同じ (source, externalId) が登録済み THEN THE SYSTEM SHALL 登録ボタンを「登録済み」にして二重登録を防ぐ

### US-105: 手で補えるようにする
- THE SYSTEM SHALL Edition に sourceRef（URL・ncode・ISBN・AniList ID）を後から追加・削除できる UI を持つ
- THE SYSTEM SHALL Release を手で追加・編集・削除できる UI を持ち、手動の Release を同期で上書きしない

### US-106: 同期・検知を壊れにくくする
- IF 同期でソースが全部失敗した THEN THE SYSTEM SHALL lastSyncAt を進めず、次の起動で再試行する
- IF 検知の照会が失敗した THEN THE SYSTEM SHALL その Work の検知スロットルを進めず、エラーを同期状態に表示する
- WHEN 手動同期した時 THE SYSTEM SHALL すべてのソースでキャッシュを使わず取り直す
- THE SYSTEM SHALL なろう・カクヨムの初回同期で既読位置（current）を変えない。最新話は1件の Release（ローリング）として持つ
- THE SYSTEM SHALL なろうの完結を正しく判定し、Edition に「完結」を表示する
- THE SYSTEM SHALL 「新着」を追跡中 Edition のうち、登録後に新しく見つかった Release に限る。登録時点で過去日の Release は既読扱いにする

## Non-Functional Requirements

- 収集マナーは MVP と同じ（レート制御・UA 明示・robots.txt 遵守・TTL キャッシュ）。NDL は1作品の同期で最大2リクエスト
- 既存データを壊さない。Dexie はスキーマ v2 へ移行し、既存 Release には precision=day を補う
- テスト: 監査で再現した事例（本好きの NDL、無職転生・薬屋の新期、深夜放送の日付、なろう end）を fixture で固定する
