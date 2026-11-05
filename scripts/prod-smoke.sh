#!/usr/bin/env bash
# Fresh-install proof: build images, start the production-like stack, bootstrap an admin, run a real action through nginx, check health probes and
# clean shutdown of every service, tear down.
# Usage: scripts/prod-smoke.sh [--roles] [--agent] [--keep]   (needs Docker; uses ports 8080 by default via ORCH_HTTP_PORT)
#   (rien)   topologie COMPACTE : api, orchestrator, worker
#   --agent  topologie PAR AGENTS (implique --roles) : `exec-agent` (socket Docker, NI base NI clé) remplace `worker-exec` ; l'action de test passe par lui
#   --roles  topologie PAR RÔLES (docker-compose.roles.yml) : api-ui, api-ingest, runner-gw, orchestrator, worker-exec, worker-aux, avec en plus
#            routage par préfixe, absence de sortie réseau d'api-ingest et isolation (un api-ingest gelé et inondé n'affecte pas l'interface)
source "$(dirname "$0")/_common.sh"; cd "$ROOT"
KEEP=0; ROLES=0; AGENT=0
for a in "$@"; do case "$a" in --keep) KEEP=1 ;; --roles) ROLES=1 ;; --agent) ROLES=1; AGENT=1 ;; *) echo "option inconnue : $a" >&2; exit 2 ;; esac; done
scripts/prod-init.sh >/dev/null
set -a; . infra/.env.prod; set +a
export ORCH_HTTP_PORT="${ORCH_HTTP_PORT:-8080}"
FILES=(-f infra/docker-compose.prod.yml)
if [ "$ROLES" = 1 ]; then
  FILES+=(-f infra/docker-compose.roles.yml); API=api-ui
  SERVICES=(api-ui api-ingest runner-gw orchestrator worker-exec worker-aux); STOPS=(orchestrator worker-exec worker-aux api-ingest runner-gw api-ui)
  if [ "$AGENT" = 1 ]; then
    FILES+=(-f infra/docker-compose.agent.yml)
    SERVICES=(api-ui api-ingest runner-gw orchestrator worker-aux); STOPS=(exec-agent orchestrator worker-aux api-ingest runner-gw api-ui)
  fi
else
  API=api; SERVICES=(api orchestrator worker); STOPS=(orchestrator worker api)
fi
C=(docker compose "${FILES[@]}" --env-file infra/.env.prod)
cleanup() { [ "$KEEP" = 1 ] || "${C[@]}" down -v --remove-orphans >/dev/null 2>&1; }
trap cleanup EXIT
"${C[@]}" up -d --build 2>&1 | tail -3
BASE="http://127.0.0.1:$ORCH_HTTP_PORT"
for _ in $(seq 1 60); do curl -fs "$BASE/api/v1/health/ready/" >/dev/null 2>&1 && break; sleep 2; done
curl -fs "$BASE/api/v1/health/ready/" || { echo "API non prête"; "${C[@]}" logs --tail 30 "$API"; exit 1; }
echo; "${C[@]}" exec -T -e ADMIN_PW=prod-smoke-pass-123 "$API" python manage.py bootstrap --workspace principal --password-env ADMIN_PW | tail -2
"${C[@]}" exec -T "$API" python manage.py seed_demo --password prod-smoke-pass-123 >/dev/null
if [ "$AGENT" = 1 ]; then  # enregistrer le runner (jeton de l'environnement), PUIS démarrer l'agent : sans base ni clé, il ne parle qu'à runner-gw
  "${C[@]}" exec -T -e ORCH_EXEC_AGENT_TOKEN "$API" python manage.py runner_bootstrap
  "${C[@]}" --profile agent up -d --build exec-agent 2>&1 | tail -2
  for _ in $(seq 1 30); do [ "$("${C[@]}" exec -T "$API" python manage.py shell -c "from runs.models import Runner; print(Runner.objects.filter(name='exec-agent', last_contact_at__isnull=False).count())" 2>/dev/null | tail -1)" = 1 ] && break; sleep 2; done
fi
J="$(mktemp)"; csrf() { grep csrftoken "$J" | awk '{print $7}'; }
curl -fs -c "$J" "$BASE/api/v1/auth/csrf/" >/dev/null
curl -fs -b "$J" -c "$J" -H "X-CSRFToken: $(csrf)" -H "Origin: $BASE" -H 'Content-Type: application/json' -d '{"username":"alice","password":"prod-smoke-pass-123"}' "$BASE/api/v1/auth/login/" >/dev/null
url="$(curl -fs -b "$J" -c "$J" -H "X-CSRFToken: $(csrf)" -H "Origin: $BASE" -H 'Content-Type: application/json' -d '{"action":"text-stats","environment":"test","inputs":{"text":"installation vierge"}}' "$BASE/api/v1/workspaces/demo/executions/" | python3 -c 'import sys,json;print(json.load(sys.stdin)["url"])')"
for _ in $(seq 1 60); do
  body="$(curl -fs -b "$J" "$BASE$url")"; st="$(python3 -c 'import sys,json;print(json.load(sys.stdin)["status"])' <<<"$body")"
  [ "$st" = succeeded ] || [ "$st" = failed ] && break; sleep 1
done
echo "statut: $st"; python3 -c 'import sys,json;print(json.load(sys.stdin)["result"])' <<<"$body"
curl -fsI "$BASE/" | grep -iE "content-security-policy|x-content-type-options" | head -2
# AR4.2 : un VRAI workflow (jonction → attente → action), avancé par l'orchestrateur — qui n'a ni la clé du coffre ni sortie réseau
ORCH_WEB_PORT="$ORCH_HTTP_PORT" python3 - <<'PY'
import sys, time
sys.path.insert(0, "tests/resilience")
import scenarios as S
api = S.Api()
api.call("POST", "/auth/login/", {"username": "alice", "password": "prod-smoke-pass-123"})
slug = f"smoke-{int(time.time())}"
S.publish(api, slug, [{"id": "a", "type": "join", "name": "A"}, {"id": "p", "type": "wait", "name": "P", "seconds": 2},
                      {"id": "b", "type": "action", "name": "B", "action": "text-stats", "inputs": {"text": "orchestrateur sans clé"}}],
          [{"from": "a", "to": "p"}, {"from": "p", "to": "b"}])
run = S.wait_for(api, S.start(api, slug), lambda r: r["status"] in ("succeeded", "failed"), timeout=90, label="workflow")
print(f"workflow (orchestrateur sans clé du coffre ni réseau sortant) : {run['status']}")
sys.exit(0 if run["status"] == "succeeded" else 1)
PY
# AR2.6 : sondes de santé de chaque service
cname() { "${C[@]}" ps -aq "$1" | head -1; }
for svc in "${SERVICES[@]}"; do
  id="$(cname "$svc")"; h=""
  for _ in $(seq 1 60); do h="$(docker inspect -f '{{.State.Health.Status}}' "$id")"; [ "$h" = healthy ] && break; sleep 2; done
  echo "santé de $svc : $h"; [ "$h" = healthy ] || { "${C[@]}" logs --tail 20 "$svc"; exit 1; }
done

if [ "$ROLES" = 1 ]; then
  echo "-- topologie par rôles"
  code() { curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$@" || true; }
  HOOK="$BASE/api/v1/hooks/3f2b9d6e-8c1a-4f0e-9a52-1b7c2d4e5f60/"
  r_ui="$(code "$BASE/api/v1/auth/csrf/")"
  r_in="$(code -X POST -H 'Content-Type: application/json' -d '{}' "$HOOK")"
  r_gw="$(code -X POST -H 'Content-Type: application/json' -d '{}' "$BASE/api/v1/runner/jobs/request/")"
  echo "routage par préfixe via nginx : interface $r_ui (attendu 200) | webhook $r_in (401) | runner $r_gw (401 ou 403)"
  [ "$r_ui" = 200 ] && [ "$r_in" = 401 ] && { [ "$r_gw" = 401 ] || [ "$r_gw" = 403 ]; } || { echo "ROUTAGE INCORRECT"; exit 1; }
  if [ "$AGENT" = 1 ]; then
    aid="$(cname exec-agent)"
    echo "exec-agent : socket Docker = $(docker inspect -f '{{range .Mounts}}{{.Destination}} {{end}}' "$aid" | grep -c docker.sock), volume des clés = $(docker inspect -f '{{range .Mounts}}{{.Destination}} {{end}}' "$aid" | grep -c /keys), variables de base = $(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$aid" | grep -c '^ORCH_DB_'), réseaux = $(docker inspect -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' "$aid")"
    [ "$(docker inspect -f '{{range .Mounts}}{{.Destination}} {{end}}' "$aid" | grep -c /keys)" = 0 ] && [ "$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$aid" | grep -c '^ORCH_DB_')" = 0 ] || { echo "exec-agent détient la clé ou une base : I1 FAUX"; exit 1; }
  fi
  # une route d'un autre rôle n'existe pas DANS le process (pas seulement dans le proxy)
  other="$("${C[@]}" exec -T api-ui python -c "
import urllib.request as u, urllib.error as e
try: u.urlopen(u.Request('http://api-ingest:8000/api/v1/auth/csrf/', headers={'Host': 'localhost'}), timeout=4); print(200)
except e.HTTPError as x: print(x.code)")"
  echo "api-ingest sert la route de connexion ? → HTTP $other (attendu 404)"; [ "$other" = 404 ] || exit 1
  # AUCUNE sortie réseau pour api-ingest (réseaux internes seulement), alors que api-ui peut sortir
  reach() { "${C[@]}" exec -T "$1" python -c "import socket; socket.create_connection(('1.1.1.1', 443), 4)" >/dev/null 2>&1 && echo oui || echo non; }
  ui_out="$(reach api-ui)"; in_out="$(reach api-ingest)"
  echo "sortie vers Internet : api-ui = $ui_out, api-ingest = $in_out (attendu : api-ingest = non)"
  if [ "$ui_out" = non ]; then echo "  (l'hôte n'a pas d'accès Internet : preuve de l'absence de sortie d'api-ingest INCONCLUANTE)"; elif [ "$in_out" = oui ]; then echo "api-ingest a une sortie réseau !"; exit 1; fi
  # isolation : api-ingest gelé puis inondé de webhooks ; l'interface doit rester réactive
  lat() { python3 - "$J" "$BASE" <<'PY'
import subprocess, sys, time
jar, base = sys.argv[1:3]
ts, bad = [], 0
for _ in range(20):
    t = time.time()
    out = subprocess.run(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "-b", jar, "--max-time", "10", f"{base}/api/v1/workspaces/demo/actions/"], capture_output=True, text=True).stdout
    ts.append((time.time() - t) * 1000); bad += out != "200"
ts.sort(); print(f"{ts[18]:.0f} {bad}")
PY
  }
  read -r base_p95 base_bad <<<"$(lat)"
  "${C[@]}" pause api-ingest >/dev/null
  for _ in $(seq 1 40); do curl -s -o /dev/null --max-time 30 -X POST -H 'Content-Type: application/json' -d '{}' "$HOOK" & done
  sleep 2; read -r down_p95 down_bad <<<"$(lat)"
  code_hook="$(code -X POST -H 'Content-Type: application/json' -d '{}' "$HOOK")"
  "${C[@]}" unpause api-ingest >/dev/null; wait
  echo "GET /actions/ : p95 ${base_p95} ms normal ; p95 ${down_p95} ms (${down_bad} erreur) pendant que api-ingest est GELÉ et reçoit 40 webhooks ; un webhook répond alors HTTP ${code_hook:-timeout} (délai coupé, interface indemne)"
  [ "$base_bad" = 0 ] && [ "$down_bad" = 0 ] && [ "$down_p95" -lt 1500 ] || { echo "L'INTERFACE A ÉTÉ AFFECTÉE PAR api-ingest"; exit 1; }
  for _ in $(seq 1 30); do [ "$(code -X POST -H 'Content-Type: application/json' -d '{}' "$HOOK")" = 401 ] && break; sleep 1; done
  echo "après dégel, le webhook répond de nouveau : HTTP $(code -X POST -H 'Content-Type: application/json' -d '{}' "$HOOK")"
fi

# AR2.6 : arrêt propre de chaque service (signal d'arrêt, code de sortie 0 = arrêt voulu, pas 137 = tué)
for svc in "${STOPS[@]}"; do
  id="$(cname "$svc")"; t0=$SECONDS
  "${C[@]}" stop "$svc" >/dev/null 2>&1
  code="$(docker inspect -f '{{.State.ExitCode}}' "$id")"; took=$((SECONDS - t0))
  echo "arrêt de $svc : code de sortie $code en ${took}s"
  [ "$code" = 0 ] || { echo "arrêt NON propre de $svc"; "${C[@]}" logs --tail 15 "$svc"; exit 1; }
done
[ "$st" = succeeded ] && echo "PROD SMOKE OK$([ "$ROLES" = 1 ] && echo ' (par rôles)')$([ "$AGENT" = 1 ] && echo ' (par agents)')"
