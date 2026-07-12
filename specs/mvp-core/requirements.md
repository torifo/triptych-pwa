# triptych MVP-Core Requirements

## Overview

小説（Web小説・紙書籍）・漫画・アニメを一つの「作品（Work）」として結びつけ、原作↔メディア展開の対応関係、視聴・読書ステータス、発売日・配信日、アニメ化発表を一元管理するPWA。三連画（triptych）のように、同じ物語の三つの面（小説/漫画/アニメ）を並べて見ることをコアコンセプトとする。

- 形態: PWA（自分用トラッカーとして開始、公開は将来判断）
- 配置: `dev/pwa/triptych/`
- データ収集: 公開API中心 + APIでカバーできない範囲はスクレイピング併用

## Personas

- **P1（主）**: 本人。Web小説→書籍化→漫画化→アニメ化と追いかける読者/視聴者。「この新アニメの原作はなろうのどれ？」「読んでるあの作品の新刊/アニメ化はいつ？」を即答したい。

## Domain Model (用語定義)

- **Work（作品）**: 物語の単位。メディア横断の親エンティティ（例:「本好きの下剋上」）。
- **Edition（展開）**: Work に属する各メディア展開。`web-novel` / `light-novel`(紙・電子書籍) / `manga` / `anime` の4種。
- **Release（リリース）**: Edition に属する個々の発売/配信イベント（新刊◯巻、アニメ第◯期第◯話、シーズン配信開始 等）。
- **Status（ステータス）**: Edition 単位のユーザー状態。`plan`(積み) / `active`(読書中・視聴中) / `paused` / `done` / `dropped` の5値。

## User Stories

### US-001: 作品リンク（原作↔アニメ）
**As a** P1 **I want to** アニメとその原作（Web小説/書籍/漫画）を1つの Work に紐づけて一覧したい **So that** 「このアニメの原作は何か」「原作のどこまでがアニメ化されたか」を即座に把握できる

**Acceptance Criteria:**
- WHEN ユーザーが Work 詳細を開いた時 THE SYSTEM SHALL その Work に属する全 Edition（web-novel / light-novel / manga / anime）をメディア種別ごとに並べて表示する
- WHEN ユーザーがアニメの Edition を登録した時 THE SYSTEM SHALL 外部API（AniList等）の source relation を参照して原作 Edition の候補を提示する
- WHEN 外部APIで原作候補が見つからない時 THE SYSTEM SHALL 手動で Edition 同士をリンクする手段を提供する
- WHEN ユーザーが誤ったリンクを解除した時 THE SYSTEM SHALL Edition を別の Work へ付け替え可能にする

### US-002: 視聴・読書ステータス管理
**As a** P1 **I want to** 各 Edition の進捗状態（積み/進行中/完了 等）と現在位置（巻数/話数）を記録したい **So that** 何をどこまで読んだか・見たかを忘れずに管理できる

**Acceptance Criteria:**
- WHEN ユーザーが Edition のステータスを変更した時 THE SYSTEM SHALL 5値（plan/active/paused/done/dropped）のいずれかとして即時保存する
- WHEN ユーザーが進捗位置（第◯巻/第◯話/なろう第◯部分）を入力した時 THE SYSTEM SHALL Edition ごとに現在位置を保存し Work 詳細に表示する
- WHEN ユーザーがステータス一覧を開いた時 THE SYSTEM SHALL ステータス別・メディア種別でフィルタした一覧を表示する
- IF 同一 Work に複数 Edition がある THEN THE SYSTEM SHALL Edition ごとに独立したステータスを保持する（アニメは完了・原作は読書中、が表現できる）

### US-003: 発売日・配信日カレンダー
**As a** P1 **I want to** 追跡中の作品の新刊発売日・アニメ放送/配信日を時系列で見たい **So that** 買い逃し・見逃しを防げる

**Acceptance Criteria:**
- WHEN ユーザーがカレンダービューを開いた時 THE SYSTEM SHALL 追跡中（status が plan/active/paused の Edition）の Release を日付順に表示する
- WHEN 今日から7日以内の Release が存在する時 THE SYSTEM SHALL ホーム画面の「直近」セクションに強調表示する
- WHEN 自動収集で新しい Release（新刊・新期・配信開始）が検出された時 THE SYSTEM SHALL カレンダーへ自動追加し「新着」マークを付ける
- WHEN Release の日付が変更（延期等）された時 THE SYSTEM SHALL 旧日付を上書きし変更履歴を保持する

### US-004: アニメ化予定の検知
**As a** P1 **I want to** 読んでいる原作のアニメ化発表を自動検知したい **So that** 発表を見逃さずアニメ Edition を追跡開始できる

**Acceptance Criteria:**
- WHEN 定期収集で追跡中 Work に対応するアニメが外部ソース（AniList「NOT_YET_RELEASED」等）に出現した時 THE SYSTEM SHALL アニメ Edition を自動生成し「アニメ化検知」として通知欄に表示する
- WHEN 検知結果が誤りだった時 THE SYSTEM SHALL ユーザーが検知を却下（リンク解除）できる
- IF 検知済みアニメの放送日が確定した THEN THE SYSTEM SHALL Release をカレンダーへ追加する

### US-005: データ収集（API + スクレイピング）
**As a** P1 **I want to** 作品メタデータとリリース情報を自動収集させたい **So that** 手入力を最小化できる

**Acceptance Criteria:**
- WHEN ユーザーが作品を検索・登録する時 THE SYSTEM SHALL 外部ソースからタイトル・カバー画像・話数/巻数等のメタデータを取得して Edition を初期化する
- WHEN 定期同期が実行された時 THE SYSTEM SHALL 追跡中 Edition の最新情報（新話公開・新刊予定・放送日）を更新する
- IF 外部ソースが利用不能（レート制限・障害） THEN THE SYSTEM SHALL 既存のキャッシュ済みデータで表示を継続しエラーをユーザーへ非侵襲的に示す
- THE SYSTEM SHALL 手動登録・手動編集を全エンティティで常に可能とする（自動収集はあくまで補助）

## Functional Requirements

### FR-001: Work/Edition/Release データモデル
**Priority:** P0
THE SYSTEM SHALL Work（1）→ Edition（N）→ Release（N）の3層モデルで全データを保持する。
**Rationale:** メディア横断リンク（US-001）とEdition独立ステータス（US-002）の土台。

### FR-002: ローカルファースト永続化
**Priority:** P0
THE SYSTEM SHALL 全ユーザーデータを端末内（IndexedDB）に保存し、ネットワーク不通時も閲覧・編集を可能とする。
**Rationale:** PWA・自分用トラッカー。サーバー障害や外部API停止で自分のデータが見えなくなる事態を排除する。

### FR-003: 外部ソースアダプター
**Priority:** P0
THE SYSTEM SHALL 外部ソースごとに独立したアダプター（取得・正規化・レート制御）を実装し、以下を初期対応とする:
- **AniList API**（GraphQL・アニメメタデータ・source relation・放送予定）
- **なろう小説API**（公式API・Web小説メタデータ・更新日）
- **openBD API**（ISBN・書籍発売日）
- **しょぼいカレンダー**（放送日詳細・補完用）
- **スクレイピング枠**（カクヨム等API非提供ソース。robots.txt 遵守・リクエスト間隔 ≥ 1秒）
**Rationale:** ソース追加・仕様変更の影響を局所化する。

### FR-004: 定期同期
**Priority:** P1
WHEN 同期がトリガーされた時（アプリ起動時 + 手動更新） THE SYSTEM SHALL 追跡中 Edition のみを対象に差分更新を実行する。
**Rationale:** 全件同期はレート制限と時間の浪費。MVP はプッシュ型cronでなくアプリ主導のpull型で十分。

### FR-005: アニメ化検知ジョブ
**Priority:** P1
WHEN 同期実行時 THE SYSTEM SHALL 追跡中の原作系 Edition（web-novel/light-novel/manga）についてタイトル照合でアニメ情報を検索し、新規一致を検知結果として記録する。
**Rationale:** US-004 の実体。タイトル照合の誤検知は却下フローで許容する。

### FR-006: 検索・登録フロー
**Priority:** P0
WHEN ユーザーが検索語を入力した時 THE SYSTEM SHALL 複数ソースを横断検索し、選択された結果から Work + Edition を一括生成する。
**Rationale:** 登録の摩擦が高いとトラッカーは使われなくなる。

### FR-007: PWA 要件
**Priority:** P1
THE SYSTEM SHALL Web App Manifest とService Worker を備え、ホーム画面追加・オフライン起動（App Shell + キャッシュ済みデータ表示）に対応する。
**Rationale:** スマホからの日常利用が主想定。

## Non-Functional Requirements

- **Performance:** 一覧・詳細表示はローカルデータのみで初回描画 500ms 以内（外部フェッチは非同期・後追い反映）
- **収集マナー:** スクレイピング対象へのリクエスト間隔 1 リクエスト/秒以上、User-Agent 明示、robots.txt 遵守。API はレート制限（AniList: 90 req/min 等）を遵守
- **データ保全:** 全データを JSON でエクスポート/インポート可能（端末移行・バックアップ）
- **ブラウザ対応:** Chrome / Safari (iOS) / Firefox で動作（Chrome専用CSSプロパティ不使用）
- **将来拡張:** 公開サービス化・マルチデバイス同期はスコープ外だが、データモデルはユーザーID付与で拡張可能な形にしておく

## Out of Scope (MVP)

- プッシュ通知（アニメ化検知はアプリ内通知欄のみ）
- サーバーサイド常駐 cron（同期はアプリ主導）
- マルチユーザー・認証
- 漫画の話単位トラッキング（巻単位のみ）
- 感想・レビュー機能
