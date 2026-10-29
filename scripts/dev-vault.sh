#!/usr/bin/env bash
# Un HashiCorp Vault de DÉVELOPPEMENT (conteneur, mode dev : en mémoire, jeton racine connu) prêt pour Fluxonata :
# AppRole + politique, un secret d'exemple, et — avec un PostgreSQL jetable — les identifiants dynamiques.
# Pour essayer l'intégration, JAMAIS pour de vrais secrets.
#   scripts/dev-vault.sh          démarre et affiche l'adresse, le role_id et le secret_id à saisir dans Plate-forme → Coffre externe
#   scripts/dev-vault.sh --down   arrête et supprime
source "$(dirname "$0")/_common.sh"; cd "$ROOT"
VT=fluxonata-dev-vault; PG=fluxonata-dev-vault-pg; NET=fluxonata-dev-vault-net; PORT="${DEV_VAULT_PORT:-8200}"; ROOT_TOKEN=dev-root-token
if [ "${1:-}" = "--down" ]; then docker rm -f "$VT" "$PG" >/dev/null 2>&1 || true; docker network rm "$NET" >/dev/null 2>&1 || true; echo "→ Vault de développement supprimé"; exit 0; fi
docker rm -f "$VT" "$PG" >/dev/null 2>&1 || true; docker network create "$NET" >/dev/null 2>&1 || true
docker run -d --name "$PG" --network "$NET" -p 127.0.0.1::5432 -e POSTGRES_PASSWORD=rootpw postgres:16-alpine >/dev/null
docker run -d --name "$VT" --network "$NET" --cap-add IPC_LOCK -p "127.0.0.1:$PORT:8200" -e "VAULT_DEV_ROOT_TOKEN_ID=$ROOT_TOKEN" -e VAULT_DEV_LISTEN_ADDRESS=0.0.0.0:8200 hashicorp/vault:1.17 >/dev/null
ADDR="http://127.0.0.1:$PORT"
for _ in $(seq 1 60); do curl -fs "$ADDR/v1/sys/health" >/dev/null 2>&1 && docker exec "$PG" pg_isready -U postgres >/dev/null 2>&1 && break; sleep 1; done
v() { curl -fsS -H "X-Vault-Token: $ROOT_TOKEN" -H "Content-Type: application/json" -X "$1" "$ADDR/v1/$2" ${3:+-d "$3"}; }
v POST sys/auth/approle '{"type":"approle"}' >/dev/null
v PUT sys/policies/acl/fluxonata '{"policy":"path \"secret/data/fluxonata/*\" { capabilities = [\"create\",\"read\",\"update\",\"delete\"] }\npath \"secret/metadata/fluxonata/*\" { capabilities = [\"read\",\"delete\",\"list\"] }\npath \"secret/data/shared/*\" { capabilities = [\"create\",\"read\",\"update\"] }\npath \"database/creds/*\" { capabilities = [\"read\"] }\npath \"sys/leases/revoke\" { capabilities = [\"update\"] }"}' >/dev/null
v POST auth/approle/role/fluxonata '{"token_policies":"fluxonata","token_ttl":"1h"}' >/dev/null
ROLE_ID="$(v GET auth/approle/role/fluxonata/role-id | python3 -c 'import sys,json;print(json.load(sys.stdin)["data"]["role_id"])')"
SECRET_ID="$(v POST auth/approle/role/fluxonata/secret-id | python3 -c 'import sys,json;print(json.load(sys.stdin)["data"]["secret_id"])')"
v POST secret/data/shared/demo '{"data":{"value":"secret-de-demonstration","user":"demo"}}' >/dev/null
v POST sys/mounts/database '{"type":"database"}' >/dev/null
v POST database/config/pg "{\"plugin_name\":\"postgresql-database-plugin\",\"allowed_roles\":\"ro\",\"username\":\"postgres\",\"password\":\"rootpw\",\"connection_url\":\"postgresql://{{username}}:{{password}}@$PG:5432/postgres?sslmode=disable\"}" >/dev/null
v POST database/roles/ro '{"db_name":"pg","default_ttl":"5m","max_ttl":"1h","creation_statements":"CREATE ROLE \"{{name}}\" WITH LOGIN PASSWORD '"'"'{{password}}'"'"' VALID UNTIL '"'"'{{expiration}}'"'"'; GRANT CONNECT ON DATABASE postgres TO \"{{name}}\";"}' >/dev/null
# l'API de Fluxonata n'atteint une adresse locale que si l'opérateur l'a autorisée
if [ -f .env ]; then
  CUR="$(grep -E '^ORCH_EGRESS_ALLOWLIST=' .env | cut -d= -f2- || true)"
  if ! echo ",$CUR," | grep -q ",127.0.0.1,"; then
    NEW="${CUR:+$CUR,}127.0.0.1"
    if grep -q '^ORCH_EGRESS_ALLOWLIST=' .env; then sed -i "s|^ORCH_EGRESS_ALLOWLIST=.*|ORCH_EGRESS_ALLOWLIST=$NEW|" .env; else echo "ORCH_EGRESS_ALLOWLIST=$NEW" >> .env; fi
    echo "→ 127.0.0.1 autorisé dans .env (ORCH_EGRESS_ALLOWLIST) : relancez scripts/dev-down.sh puis scripts/dev-up.sh"
  fi
fi
echo
echo "Vault de développement prêt (données en mémoire) :"
echo "  Adresse      : $ADDR      (jeton racine : $ROOT_TOKEN)"
echo "  role_id      : $ROLE_ID"
echo "  secret_id    : $SECRET_ID"
echo "  Secret d'exemple (en lecture) : secret/shared/demo ; identifiants dynamiques : database/creds/ro"
echo "→ Saisissez ces valeurs dans Fluxonata : Plate-forme → Coffre externe (en admin), enregistrez, testez."
echo "  Motifs à ouvrir à un espace : secret/shared/*  et  database/creds/*"
