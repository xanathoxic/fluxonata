#!/usr/bin/env bash
# Banc du MCP de conception (ADR 0025) avec le CLIENT OFFICIEL du SDK MCP, contre la pile de dev (scripts/dev-up.sh).
# Crée un espace jetable « mcp-essai » + deux jetons à courte durée, joue un assistant, vérifie que rien n'a été publié / lancé / activé,
# puis supprime l'espace. Le SDK est installé une fois dans .run/mcp-client (--ignore-scripts, version épinglée).
source "$(dirname "$0")/_common.sh"
load_env
SDK=1.32.2
API="http://127.0.0.1:${ORCH_API_PORT:-8100}"
curl -fsS "$API/api/v1/health/live/" >/dev/null || { echo "API injoignable : lancez scripts/dev-up.sh" >&2; exit 2; }
command -v node >/dev/null || { echo "node est requis" >&2; exit 2; }
CLIENT="$RUN_DIR/mcp-client"; mkdir -p "$CLIENT"
if [ ! -d "$CLIENT/node_modules/@modelcontextprotocol/sdk" ]; then
  (cd "$CLIENT" && { [ -f package.json ] || npm init -y >/dev/null; } && npm install --ignore-scripts --no-audit --no-fund "@modelcontextprotocol/sdk@$SDK" >/dev/null)
fi
cp "$ROOT/scripts/mcp_design_client.mjs" "$CLIENT/client.mjs"
TOK="$(mktemp -d)"; chmod 700 "$TOK"
shell() { (cd "$ROOT/backend" && MCP_ACTION="$1" MCP_TOKEN_DIR="$TOK" "$ROOT/.venv/bin/python" manage.py shell < "$ROOT/scripts/mcp_design_setup.py" 2>&1 | grep -v "RuntimeWarning\|objects imported\|^$"); }
cleanup() { shell cleanup >/dev/null || true; rm -rf "$TOK"; }
trap cleanup EXIT
shell cleanup >/dev/null || true   # repart d'un espace propre
shell setup
rc=0
(cd "$CLIENT" && node client.mjs "$API/api/v1/workspaces/mcp-essai/mcp/design/" "$TOK/token-design" "$TOK/token-big") || rc=1
shell check || rc=1
exit $rc
