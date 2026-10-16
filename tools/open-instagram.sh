#!/usr/bin/env bash
# Открывает Firefox с instagram.com на рабочем столе Codespaces (desktop-lite, дисплей :1).
set -euo pipefail
export DISPLAY="${DISPLAY:-:1}"
nohup firefox-esr --new-window "https://www.instagram.com/accounts/login/" >/dev/null 2>&1 &
echo "Firefox запущен. Откройте вкладку «Рабочий стол (noVNC)» (порт 6080), пароль: vscode"
