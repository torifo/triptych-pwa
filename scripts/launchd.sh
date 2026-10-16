#!/bin/bash
# collector を LaunchAgent で常駐させる。plist は実行時にこの場所から作る（リポジトリに自分のパスを残さない）
set -euo pipefail

usage() {
  cat <<'EOF'
scripts/launchd.sh install    plist を書いて起動（入れ直しにも使う）
scripts/launchd.sh restart    backend を変えた後に読み込み直す
scripts/launchd.sh status     動いているか
scripts/launchd.sh logs [n]   ログの末尾
scripts/launchd.sh uninstall  止めて plist を消す
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

is_loaded() {
  launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1
}

case "${1:-}" in
  install)
    write_plist
    if is_loaded; then launchctl bootout "$DOMAIN/$LABEL" || true; fi
    launchctl bootstrap "$DOMAIN" "$PLIST"
    echo "起動しました: http://localhost:$PORT （ログ: $LOG）"
    ;;
  restart)
    launchctl kickstart -k "$DOMAIN/$LABEL"
    echo "読み込み直しました"
    ;;
  status)
    if is_loaded; then
      launchctl print "$DOMAIN/$LABEL" | grep -E $'^\t(state|pid|last exit code) =' || true
    else
      echo "登録されていません（scripts/launchd.sh install）"
    fi
    ;;
  logs)
    tail -n "${2:-50}" "$LOG"
    ;;
  uninstall)
    if is_loaded; then launchctl bootout "$DOMAIN/$LABEL" || true; fi
    rm -f "$PLIST"
    echo "止めて plist を消しました"
    ;;
  *)
    usage
    exit 1
    ;;
esac
