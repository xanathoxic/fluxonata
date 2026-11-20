#!/usr/bin/env bash
# Essai de CLUSTER ACTIF/ACTIF sur éléments JETABLES : deux instances de l'API (image construite depuis le commit courant) derrière un
# répartiteur nginx, une base PostgreSQL et un cache partagé en base, tous créés pour l'essai puis supprimés. Rien de la pile de
# développement ni de vos données n'est lu ou écrit ; aucun secret réel (clé et mot de passe aléatoires, jetés avec les conteneurs).
# Vérifie : répartition entre les deux instances, session valable sur les deux, limite de connexion sous `X-Forwarded-For` falsifié
# (avec et sans ORCH_NUM_PROXIES), arrêt puis retour d'une instance. N'est PAS couvert : PgBouncer, nginx de la pile, TLS, orchestrateur,
# workers, PostgreSQL en haute disponibilité (voir docs/CLUSTER.md).
# Usage : scripts/cluster-test.sh      (Docker requis ; ≈ 3 à 6 min, dont la construction de l'image)
source "$(dirname "$0")/_common.sh"; cd "$ROOT"
TMP="$(mktemp -d)"; ID="$$"; NET="fxcl-$ID"; WT="$TMP/src"; IMG="fluxonata-backend:cluster-$ID"
FAIL=0
cleanup() {
  docker rm -f "fxcl-lb-$ID" "fxcl-a-$ID" "fxcl-b-$ID" "fxcl-db-$ID" >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
  docker volume rm "fxcl-keys-$ID" >/dev/null 2>&1 || true
  docker rmi "$IMG" >/dev/null 2>&1 || true
  git worktree remove --force "$WT" >/dev/null 2>&1 || true; git worktree prune; rm -rf "$TMP"
}
trap cleanup EXIT
check() { if [ "$2" = "ok" ]; then echo "  PASS  $1"; else echo "  FAIL  $1 — $3"; FAIL=1; fi; }
load_avg="$(cut -d' ' -f1 /proc/loadavg)"; cpus="$(nproc)"
awk -v l="$load_avg" -v c="$cpus" 'BEGIN{exit !(l > c*0.9)}' && { echo "Machine saturée (charge $load_avg pour $cpus cœurs) : essai refusé."; exit 2; }

echo "== Construction de l'image du commit $(git rev-parse --short HEAD)"
git worktree add -q --detach "$WT" HEAD
docker build -q -f "$WT/infra/Dockerfile.backend" --target runtime -t "$IMG" "$WT" >/dev/null
docker network create "$NET" >/dev/null
PW="$(python3 -c 'import secrets;print(secrets.token_urlsafe(24))')"
SK="$(python3 -c 'import secrets;print(secrets.token_urlsafe(48))')"
docker run -d --name "fxcl-db-$ID" --network "$NET" -e POSTGRES_USER=fluxonata -e POSTGRES_PASSWORD="$PW" -e POSTGRES_DB=fluxonata postgres:17-alpine >/dev/null
ok=0; for _ in $(seq 1 60); do if docker exec "fxcl-db-$ID" pg_isready -h 127.0.0.1 -U fluxonata -q; then ok=$((ok+1)); [ $ok -ge 3 ] && break; else ok=0; fi; sleep 1; done
[ $ok -ge 3 ] || { echo "ÉCHEC : la base jetable ne démarre pas."; exit 1; }
docker volume create "fxcl-keys-$ID" >/dev/null
ENVS=(-e ORCH_SECRET_KEY="$SK" -e ORCH_DB_HOST="fxcl-db-$ID" -e ORCH_DB_PORT=5432 -e ORCH_DB_PASSWORD="$PW" -e ORCH_CACHE=db
      -e ORCH_KEY_DIR=/keys -e ORCH_INSECURE_COOKIES=1 -e ORCH_ALLOWED_HOSTS=localhost,127.0.0.1)
docker run --rm --network "$NET" --user root -v "fxcl-keys-$ID:/keys" "${ENVS[@]}" -e ORCH_KEY_AUTOCREATE=1 "$IMG" \
  sh -c 'chown fluxonata /keys && su -s /bin/sh fluxonata -c "python manage.py migrate --noinput >/dev/null && python manage.py createcachetable && python manage.py shell -c \"from django.contrib.auth import get_user_model as g; g().objects.create_user(\\\"essai\\\", password=\\\"essai-jetable-12345\\\")\""' \
  || { echo "ÉCHEC : migrations."; exit 1; }

start_api() {  # $1 = a|b ; $2 = valeur de ORCH_NUM_PROXIES ("" = non défini)
  docker rm -f "fxcl-$1-$ID" >/dev/null 2>&1 || true
  local extra=(); [ -n "${2:-}" ] && extra=(-e ORCH_NUM_PROXIES="$2")
  docker run -d --name "fxcl-$1-$ID" --network "$NET" -v "fxcl-keys-$ID:/keys:ro" "${ENVS[@]}" -e ORCH_KEY_AUTOCREATE=0 "${extra[@]}" "$IMG" \
    gunicorn fluxonata.wsgi -b 0.0.0.0:8000 -w 2 --worker-class gthread --threads 8 --timeout 60 --access-logfile - >/dev/null
}
wait_ready() {
  for _ in $(seq 1 60); do
    docker exec "fxcl-$1-$ID" python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health/ready/',timeout=3)" >/dev/null 2>&1 && return 0; sleep 1
  done; return 1
}
hits() { docker logs "fxcl-$1-$ID" 2>&1 | grep -c "$2" || true; }

cat > "$TMP/lb.conf" <<CONF
events {}
http {
  upstream fluxonata { server fxcl-a-$ID:8000 max_fails=1 fail_timeout=5s; server fxcl-b-$ID:8000 max_fails=1 fail_timeout=5s; }
  server {
    listen 8080;
    location / {
      proxy_pass http://fluxonata;
      proxy_set_header Host \$http_host;
      proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
      proxy_set_header X-Forwarded-Proto \$scheme;
      proxy_connect_timeout 2s;
    }
  }
}
CONF
chmod 644 "$TMP/lb.conf"
start_lb() {
  docker rm -f "fxcl-lb-$ID" >/dev/null 2>&1 || true
  docker run -d --name "fxcl-lb-$ID" --network "$NET" -v "$TMP/lb.conf:/etc/nginx/nginx.conf:ro" -p 127.0.0.1::8080 nginx:1.30.7-alpine >/dev/null
  PORT="$(docker port "fxcl-lb-$ID" 8080/tcp | head -1 | sed 's/.*://')"; URL="http://127.0.0.1:$PORT"
  for _ in $(seq 1 30); do curl -s -o /dev/null "$URL/api/v1/health/live/" && break; sleep 1; done
}
login_attempts() {  # $1 = n ; $2 = "spoof" ou "fixe" ; affiche les codes HTTP obtenus
  local jar="$TMP/jar-$RANDOM"; curl -s -c "$jar" -o /dev/null "$URL/api/v1/auth/csrf/"
  local tok; tok="$(awk '$6=="csrftoken"{print $7}' "$jar")"; local out=""
  for i in $(seq 1 "$1"); do
    local xff=(); [ "$2" = spoof ] && xff=(-H "X-Forwarded-For: 198.51.100.$i")
    out+="$(curl -s -o /dev/null -w '%{http_code}' -b "$jar" -H "X-CSRFToken: $tok" -H 'Content-Type: application/json' "${xff[@]}" \
      -d '{"username":"essai","password":"mauvais"}' "$URL/api/v1/auth/login/") "
  done; echo "$out"
}

echo "== Phase 1 : deux instances, ORCH_NUM_PROXIES non défini (comportement historique)"
start_api a ""; start_api b ""
wait_ready a && wait_ready b || { echo "ÉCHEC : les instances ne démarrent pas."; docker logs "fxcl-a-$ID" 2>&1 | tail -5; exit 1; }
start_lb
codes="$(login_attempts 14 spoof)"
echo "  14 connexions ratées, X-Forwarded-For différent à chaque fois : $codes"
case "$codes" in *429*) check "contournement par X-Forwarded-For (attendu sans réglage)" fail "la limite s'est appliquée malgré l'en-tête falsifié";; *) check "contournement par X-Forwarded-For constaté sans réglage (comportement historique documenté)" ok;; esac

echo "== Phase 2 : ORCH_NUM_PROXIES=1 sur les deux instances"
start_api a 1; start_api b 1
wait_ready a && wait_ready b || { echo "ÉCHEC : redémarrage."; exit 1; }
sleep 2
codes="$(login_attempts 14 spoof)"
echo "  14 connexions ratées, X-Forwarded-For différent à chaque fois : $codes"
n429="$(echo "$codes" | tr ' ' '\n' | grep -c 429 || true)"
[ "$n429" -ge 3 ] && check "limite de connexion appliquée malgré l'en-tête falsifié ($n429 refus sur 14)" ok || check "limite sous en-tête falsifié" fail "codes : $codes"
first429="$(echo "$codes" | tr ' ' '\n' | grep -n 429 | head -1 | cut -d: -f1)"
[ "${first429:-99}" -eq 11 ] && check "le 11e essai est le premier refusé (limite de 10 par minute, compteur partagé entre les deux instances)" ok || check "compteur partagé" fail "premier refus au rang ${first429:-aucun}"

echo "== Répartition et session"
a0="$(hits a 'health/live')"; b0="$(hits b 'health/live')"
for _ in $(seq 1 20); do curl -s -o /dev/null "$URL/api/v1/health/live/"; done
a1="$(hits a 'health/live')"; b1="$(hits b 'health/live')"
da=$((a1-a0)); db=$((b1-b0))
echo "  20 requêtes : instance A $da, instance B $db"
[ "$da" -ge 5 ] && [ "$db" -ge 5 ] && check "les deux instances reçoivent du trafic" ok || check "répartition" fail "A=$da B=$db"
JAR="$TMP/session"; curl -s -c "$JAR" -o /dev/null "$URL/api/v1/auth/csrf/"
TOK="$(awk '$6=="csrftoken"{print $7}' "$JAR")"
# L'adresse de cet essai a épuisé la limite de connexion à la phase 2 : on attend la fin de la fenêtre si besoin.
code="$(curl -s -o /dev/null -w '%{http_code}' -b "$JAR" -c "$JAR" -H "X-CSRFToken: $TOK" -H 'Content-Type: application/json' \
  -d '{"username":"essai","password":"essai-jetable-12345"}' "$URL/api/v1/auth/login/")"
if [ "$code" = 429 ]; then echo "  (limite de connexion encore active : attente de 60 s)"; sleep 61
  code="$(curl -s -o /dev/null -w '%{http_code}' -b "$JAR" -c "$JAR" -H "X-CSRFToken: $TOK" -H 'Content-Type: application/json' \
    -d '{"username":"essai","password":"essai-jetable-12345"}' "$URL/api/v1/auth/login/")"; fi
[ "$code" = 200 ] && check "connexion réussie" ok || check "connexion réussie" fail "code $code"
ma="$(hits a 'auth/me')"; mb="$(hits b 'auth/me')"; bad=0
for _ in $(seq 1 20); do c="$(curl -s -o /dev/null -w '%{http_code}' -b "$JAR" "$URL/api/v1/auth/me/")"; [ "$c" = 200 ] || bad=$((bad+1)); done
ma2="$(hits a 'auth/me')"; mb2="$(hits b 'auth/me')"
echo "  20 lectures de la session : A $((ma2-ma)), B $((mb2-mb)), échecs $bad"
{ [ "$bad" -eq 0 ] && [ $((ma2-ma)) -ge 3 ] && [ $((mb2-mb)) -ge 3 ]; } && check "la session ouverte sur une instance est valable sur les deux" ok || check "session partagée" fail "échecs $bad, A $((ma2-ma)), B $((mb2-mb))"

echo "== Arrêt d'une instance"
docker stop -t 2 "fxcl-a-$ID" >/dev/null
bad=0; for _ in $(seq 1 20); do c="$(curl -s -o /dev/null -w '%{http_code}' -b "$JAR" "$URL/api/v1/auth/me/")"; [ "$c" = 200 ] || bad=$((bad+1)); done
[ "$bad" -eq 0 ] && check "instance A arrêtée : 20 requêtes sur 20 réussies via l'instance B, session conservée" ok || check "bascule" fail "$bad échec(s) sur 20"
docker start "fxcl-a-$ID" >/dev/null; wait_ready a || true
sleep 6
a0="$(hits a 'health/live')"; for _ in $(seq 1 20); do curl -s -o /dev/null "$URL/api/v1/health/live/"; done; a1="$(hits a 'health/live')"
[ $((a1-a0)) -ge 3 ] && check "instance A redémarrée : elle reçoit de nouveau du trafic ($((a1-a0)) sur 20)" ok || check "retour de l'instance" fail "A=$((a1-a0))"

echo
[ "$FAIL" -eq 0 ] && echo "Essai de cluster : tout est passé (éléments jetables supprimés)." || echo "Essai de cluster : des vérifications ont échoué."
exit "$FAIL"
