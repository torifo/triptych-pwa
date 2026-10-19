#!/bin/bash
# collector を LaunchAgent で常駐させ、カタログの差分更新を週1回走らせる。plist は実行時にこの場所から作る（リポジトリに自分のパスを残さない）
set -euo pipefail

usage() {
  cat <<'EOF'
scripts/launchd.sh install     collector の常駐と週1の差分更新を登録して起動（入れ直しにも使う）
scripts/launchd.sh restart     backend を変えた後に読み込み直す
scripts/launchd.sh status      動いているか・週1の更新の最後の呼び出し
scripts/launchd.sh logs [n]    collector のログの末尾
scripts/launchd.sh weekly-run  週1の差分更新を今すぐ1回走らせる
scripts/launchd.sh uninstall   両方を止めて plist を消す
EOF
}

LABEL="com.torifo.triptych"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$HOME/Library/Logs/triptych"
LOG="$LOG_DIR/collector.log"
DOMAIN="gui/$(id -u)"
PORT="${TRIPTYCH_PORT:-8000}"
PYTHON="$ROOT/.venv/bin/python"

# 週1の差分更新: 月曜 5:30（放送日の区切りの後）に設定画面と同じジョブ API を呼ぶ。眠っていた時は起きた時に1回走る
WEEKLY_LABEL="$LABEL.weekly"
WEEKLY_PLIST="$HOME/Library/LaunchAgents/$WEEKLY_LABEL.plist"
WEEKLY_LOG="$LOG_DIR/weekly.log"
WEEKLY_WEEKDAY=1
WEEKLY_HOUR=5
WEEKLY_MINUTE=30

write_plist() {
  if [[ ! -x "$PYTHON" ]]; then
    echo "見つかりません: $PYTHON（先に python3 -m venv .venv && .venv/bin/pip install -r requirements.txt）" >&2
    exit 1
  fi
  if [[ ! -f "$ROOT/dist/index.html" ]]; then
    echo "dist/ がありません。先に npm run build" >&2
    exit 1
  fi
  mkdir -p "$(dirname "$PLIST")" "$LOG_DIR"
  cat >"$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PYTHON</string>
    <string>-m</string>
    <string>uvicorn</string>
    <string>backend.app:app</string>
    <string>--app-dir</string>
    <string>$ROOT</string>
    <string>--host</string>
    <string>127.0.0.1</string>
    <string>--port</string>
    <string>$PORT</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$ROOT</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>$ROOT/.venv/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>PYTHONUNBUFFERED</key>
    <string>1</string>
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>ThrottleInterval</key>
  <integer>10</integer>
  <key>StandardOutPath</key>
  <string>$LOG</string>
  <key>StandardErrorPath</key>
  <string>$LOG</string>
</dict>
</plist>
EOF
  plutil -lint "$PLIST" >/dev/null
}

# ジョブ API 経由なので、ロック・ログ（local/catalog-build-recent-*.log）・設定画面の表示は手動の更新と同じ。collector の起動待ちは curl の再試行で吸収する
write_weekly_plist() {
  mkdir -p "$(dirname "$WEEKLY_PLIST")" "$LOG_DIR"
  cat >"$WEEKLY_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$WEEKLY_LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/sh</string>
    <string>-c</string>
    <string>date '+%Y-%m-%d %H:%M:%S weekly catalog update'; /usr/bin/curl -sS --max-time 30 --retry 6 --retry-delay 10 --retry-connrefused -X POST -w ' -&gt; HTTP %{http_code}\n' http://127.0.0.1:$PORT/api/catalog/jobs/recent</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Weekday</key>
    <integer>$WEEKLY_WEEKDAY</integer>
    <key>Hour</key>
    <integer>$WEEKLY_HOUR</integer>
    <key>Minute</key>
    <integer>$WEEKLY_MINUTE</integer>
  </dict>
  <key>StandardOutPath</key>
  <string>$WEEKLY_LOG</string>
  <key>StandardErrorPath</key>
  <string>$WEEKLY_LOG</string>
</dict>
</plist>
EOF
  plutil -lint "$WEEKLY_PLIST" >/dev/null
}

is_loaded() {
  launchctl print "$DOMAIN/${1:-$LABEL}" >/dev/null 2>&1
}

# bootout は外し終わる前に戻るので、外れたのを待ってから登録する（すぐ bootstrap すると EIO で失敗し、止まったままになる）
load() {
  local label="$1" plist="$2" i
  if is_loaded "$label"; then
    launchctl bootout "$DOMAIN/$label" || true
    for i in $(seq 1 50); do
      is_loaded "$label" || break
      sleep 0.2
    done
  fi
  for i in 1 2 3; do
    launchctl bootstrap "$DOMAIN" "$plist" 2>/dev/null && return 0
    sleep 1
  done
  launchctl bootstrap "$DOMAIN" "$plist"
}

case "${1:-}" in
  install)
    write_plist
    write_weekly_plist
    load "$LABEL" "$PLIST"
    load "$WEEKLY_LABEL" "$WEEKLY_PLIST"
    echo "起動しました: http://localhost:$PORT （ログ: $LOG）"
    printf '週1の差分更新: 毎週月曜 %d:%02d（ログ: %s、更新の中身は local/catalog-build-recent-*.log）\n' "$WEEKLY_HOUR" "$WEEKLY_MINUTE" "$WEEKLY_LOG"
    ;;
  restart)
    launchctl kickstart -k "$DOMAIN/$LABEL"
    echo "読み込み直しました"
    ;;
  status)
    if is_loaded; then
      launchctl print "$DOMAIN/$LABEL" | grep -E $'^\t(state|pid|last exit code) =' || true
    else
      echo "collector は登録されていません（scripts/launchd.sh install）"
    fi
    if is_loaded "$WEEKLY_LABEL"; then
      printf '週1の差分更新: 毎週月曜 %d:%02d／最後の呼び出し: ' "$WEEKLY_HOUR" "$WEEKLY_MINUTE"
      if [[ -s "$WEEKLY_LOG" ]]; then tail -n 2 "$WEEKLY_LOG" | tr '\n' ' '; echo; else echo "まだ"; fi
    else
      echo "週1の差分更新は登録されていません（scripts/launchd.sh install）"
    fi
    ;;
  logs)
    tail -n "${2:-50}" "$LOG"
    ;;
  weekly-run)
    launchctl kickstart "$DOMAIN/$WEEKLY_LABEL"
    echo "差分更新を呼びました（進み具合は設定画面か local/catalog-build-recent-*.log）"
    ;;
  uninstall)
    for label in "$WEEKLY_LABEL" "$LABEL"; do
      if is_loaded "$label"; then launchctl bootout "$DOMAIN/$label" || true; fi
    done
    rm -f "$PLIST" "$WEEKLY_PLIST"
    echo "止めて plist を消しました"
    ;;
  *)
    usage
    exit 1
    ;;
esac
