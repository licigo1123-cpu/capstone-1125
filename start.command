#!/bin/zsh
set -eu
cd "${0:A:h}"
if curl -fsS --max-time 1 http://127.0.0.1:8767/projects >/dev/null 2>&1; then
  open http://127.0.0.1:8767
  exit 0
fi
if [[ -d ../ollama-runtime/models ]]; then
  zsh scripts/ensure_ollama.zsh || print "本地文本服务未就绪，可在连接设置里选择 API。"
fi
( for i in {1..50}; do
    if curl -fsS --max-time 1 http://127.0.0.1:8767/projects >/dev/null 2>&1; then open http://127.0.0.1:8767; break; fi
    sleep .2
  done ) &
exec python3 app.py
