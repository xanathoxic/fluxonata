#!/usr/bin/env bash
# Real end-to-end check through the web proxy: login (CSRF) -> run action -> poll -> verify persisted result.
source "$(dirname "$0")/_common.sh"; load_env
BASE="http://127.0.0.1:${ORCH_WEB_PORT:-5173}/api/v1"; J="$RUN_DIR/smoke.cookies"; rm -f "$J"
csrf() { grep csrftoken "$J" | awk '{print $7}'; }
curl -fs -c "$J" "$BASE/auth/csrf/" >/dev/null
curl -fs -b "$J" -c "$J" -H "X-CSRFToken: $(csrf)" -H 'Content-Type: application/json' \
  -d '{"username":"alice","password":"demo-fluxonata"}' "$BASE/auth/login/" >/dev/null
resp="$(curl -fs -b "$J" -c "$J" -H "X-CSRFToken: $(csrf)" -H 'Content-Type: application/json' \
  -d '{"action":"text-stats","environment":"test","inputs":{"text":"un deux trois"}}' "$BASE/workspaces/demo/executions/")"
url="$(python3 -c 'import sys,json;print(json.load(sys.stdin)["url"])' <<<"$resp")"
for _ in $(seq 1 40); do
  body="$(curl -fs -b "$J" "http://127.0.0.1:${ORCH_WEB_PORT:-5173}$url")"
  st="$(python3 -c 'import sys,json;print(json.load(sys.stdin)["status"])' <<<"$body")"
  [ "$st" = succeeded ] || [ "$st" = failed ] && break; sleep 1
done
echo "statut final: $st"; python3 -c 'import sys,json;d=json.load(sys.stdin);print(d["result"])' <<<"$body"
[ "$st" = succeeded ] && python3 -c 'import sys,json;assert json.load(sys.stdin)["result"]["words"]==3' <<<"$body" && echo "SMOKE OK"
