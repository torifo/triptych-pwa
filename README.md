# triptych

<!-- tech-stack:start (auto-generated) -->
<p align="center">
  <img src="https://img.shields.io/badge/Svelte-FF3E00?style=for-the-badge&logo=svelte&logoColor=white" alt="Svelte">
  <img src="https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/Vite-646CFF?style=for-the-badge&logo=vite&logoColor=white" alt="Vite">
  <img src="https://img.shields.io/badge/TypeScript-3178C6?style=for-the-badge&logo=typescript&logoColor=white" alt="TypeScript">
  <img src="https://img.shields.io/badge/Tailwind_CSS-06B6D4?style=for-the-badge&logo=tailwindcss&logoColor=white" alt="Tailwind CSS">
</p>
<!-- tech-stack:end -->

小説（Web小説・紙書籍）・漫画・アニメを一つの「作品（Work）」として結びつけ、原作↔メディア展開の対応関係、視聴・読書ステータス、発売日・配信日、アニメ化発表を一元管理するローカルファーストPWAです。

「三連画（triptych）」の名の通り、同じ物語の三つの面 — 原作（小説）・漫画・アニメ — を並べて見ることをコアコンセプトにしています。作品詳細画面では、この3種のメディア展開を横並び（モバイルでは縦積み）で対置し、それぞれの読書・視聴ステータスと進捗、次のリリースを一望できます。

自分専用のトラッカーとして開発しています（公開は将来判断）。

## アーキテクチャ概要

PWA（フロントエンド）+ collector（薄い収集プロキシ）の2層構成です。

- **PWA (Svelte 5 + Vite + TypeScript + Tailwind)**: 全ての描画はIndexedDB（Dexie.js）に保存されたローカルデータのみで完結します。ユーザーデータ（作品・ステータス・進捗など）は**端末内にのみ保存され、サーバーには一切送信されません**。
- **collector (FastAPI + httpx)**: 外部ソース（AniList・なろう・openBD・NDL Search・しょぼいカレンダー・カクヨム）へのアクセスを一手に引き受けるステートレスなプロキシです。CORS回避・レート制御・User-Agent明示・SQLiteによる応答キャッシュ（TTL）を担いますが、**ユーザーデータは一切保持しません**。

本番相当の構成では collector がビルド済みPWA（`dist/`）を同一オリジンで静的配信し、`/api/*` を同居させます。これによりスマホからは collector のURL一つでアプリ全体に到達できます。

詳細は [`specs/mvp-core/design.md`](specs/mvp-core/design.md) を参照してください。

## セットアップ

### 前提

- Node.js（`package.json` の devDependencies が要求する Vite 8 / Svelte 5 系が動くバージョン）
- Python 3（`.venv` を作成して依存関係を入れます）

### インストール

```bash
npm install
```

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## 起動

### 開発

フロントエンド（Vite dev server）とバックエンド（collector）を別々に起動します。Vite の proxy 設定（`vite.config.ts`）により、`/api` 宛のリクエストは自動的に `http://127.0.0.1:8000` の collector へ転送されます。

```bash
# ターミナル1: フロントエンド (http://localhost:5173)
npm run dev
```

```bash
# ターミナル2: collector (http://127.0.0.1:8000)
npm run api
```

ブラウザで `http://localhost:5173` を開いてください。

### 本番相当（同一オリジン配信）

フロントエンドをビルドし、collector がその成果物（`dist/`）を静的配信します。この構成では collector 単体のURLだけでアプリ全体（画面 + API）に到達できます。

```bash
npm run build
.venv/bin/uvicorn backend.app:app --host 127.0.0.1 --port 8787
```

- `npm run build` は `dist/` にビルド成果物を出力します。
- `backend/app.py` は起動時に `dist/` の有無を確認し、存在すればアプリのルート（`/`）にマウントします。`/api/*` はそのまま同居します。
- ホスト・ポートは環境変数 `TRIPTYCH_HOST` / `TRIPTYCH_PORT` でも指定できます（`backend.app:main` 経由で起動する場合）。デフォルトは `127.0.0.1:8787` です。
- ビルドし直すと、開いている画面は次の読み込み（またはタブに戻った時）に新しい版へ自動で入れ替わります（Service Worker の autoUpdate。`src/main.ts`）。

### 常駐（launchd）

Mac にログインしている間いつも動かすには、collector を LaunchAgent として登録します（`http://localhost:8000`。落ちても launchd が起こし直します）。あわせて、カタログの差分更新（`build --recent`、15〜30分）を毎週月曜 5:30 に設定画面と同じジョブ API で走らせます（眠っていた時は起きた時に1回）。plist は実行時にこのリポジトリの場所から作ります。

```bash
npm run build
scripts/launchd.sh install     # 常駐と週1の更新を登録して起動（入れ直しにも使う）
scripts/launchd.sh restart     # backend/ を変えた後に読み込み直す
scripts/launchd.sh status      # 動いているか・週1の更新の最後の呼び出し
scripts/launchd.sh logs        # ~/Library/Logs/triptych/collector.log の末尾
scripts/launchd.sh weekly-run  # 週1の更新を今すぐ1回（呼び出しの記録は ~/Library/Logs/triptych/weekly.log）
scripts/launchd.sh uninstall   # 両方を止めて登録を消す
```

フロントエンドだけの変更なら `npm run build` だけで反映されます（再起動は不要）。

## スマホからの利用

Mac上で起動した collector に、スマホから以下のいずれかの経路でアクセスします。

- **LAN IP 経由**: 同一LAN内から `http://<MacのLAN IP>:<port>` でアクセス
- **Tailscale 経由**: Tailscale で Mac とスマホを同じtailnetに参加させ、Tailscale IP/ホスト名でアクセス

collector は `127.0.0.1` バインドがデフォルトです。LANやTailscale経由で外部から到達可能にする場合は、**必ず `TRIPTYCH_TOKEN` 環境変数でBearerトークン認証を有効化してください**。

```bash
TRIPTYCH_TOKEN=<好きなトークン文字列> .venv/bin/uvicorn backend.app:app --host 0.0.0.0 --port 8787
```

`TRIPTYCH_TOKEN` が設定されている間、`/api/*` へのリクエストは `Authorization: Bearer <token>` ヘッダーが一致しない限り `401` を返します（`backend/app.py` のミドルウェア）。フロントエンドの Settings 画面（collector 接続）で、接続先URLとトークンを設定してください。

### iOS利用時の注意（ストレージ削除対策）

iOS Safari は**非利用7日でサイトデータ（IndexedDB含む）を削除しうる**という制約があります。iPhoneで使う場合は以下を前提運用としてください。

- **ホーム画面への追加を必須運用とする**（Safari単体のタブより永続化されやすい）
- 初回起動時に `navigator.storage.persist()` の許可を求められたら許可する
- 下記「データ保全」のとおり、定期的にJSONエクスポートを行う

## データ保全

ユーザーデータは端末内のIndexedDBにのみ保存されるため、端末の入れ替えやストレージ削除に備えて定期的なバックアップを推奨します。

- **エクスポート**: Settings画面の「JSONエクスポート」から全データ（works / editions / releases / detections / meta）をJSONファイルとしてダウンロードできます。
- **インポート**: Settings画面の「インポート」から、エクスポートしたJSONファイルを選択して復元できます（現在のデータを置き換えます）。
- Settings画面には最終エクスポート日時が表示され、30日以上経過すると「要エクスポート」の注記が出ます。定期的なエクスポートを推奨します。

## テスト

```bash
# フロントエンド (vitest)
npm run test
```

```bash
# バックエンド (pytest)
.venv/bin/pytest
```

## 仕様

詳細な要件・設計はspecsディレクトリを参照してください。

- [`specs/mvp-core/requirements.md`](specs/mvp-core/requirements.md) — ユーザーストーリー・機能要件
- [`specs/mvp-core/design.md`](specs/mvp-core/design.md) — アーキテクチャ・データモデル・API設計
- [`specs/mvp-core/tasks.md`](specs/mvp-core/tasks.md) — 実装タスク一覧

運用（外部ソースの収集マナー・同期/検知の仕組み・トラブルシュート）については [`docs/operations.md`](docs/operations.md) を参照してください。
