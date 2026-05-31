#!/usr/bin/env bash
# siga_api.sh — Appel de l'API SIGA via SSH + docker exec.
#
# L'API SIGA (port 8001) n'est PAS exposée sur Internet : elle n'écoute qu'en local dans le
# conteneur Docker `siga-dashboard` du VPS. Ce script est le seul chemin d'accès depuis Cowork.
# Le token est lu à la volée depuis l'env du conteneur (SIGA_API_TOKEN) — jamais en dur.
#
# Usage :
#   siga_api.sh GET    "/api/health"
#   siga_api.sh GET    "/api/equipment/search?q=meuleuse"
#   siga_api.sh POST   "/api/accessories" '{"label":"Batterie 18V 5Ah","brand":"Makita"}'
#   siga_api.sh PATCH  "/api/equipment/<id>" '{"location":"Étagère A3"}'
#   siga_api.sh DELETE "/api/links/compatibility/<link_id>"
#
# Sortie : la réponse JSON brute de l'API sur stdout.

set -euo pipefail

METHOD="${1:?méthode requise (GET|POST|PUT|PATCH|DELETE)}"
PATHQ="${2:?chemin requis, ex: /api/health}"
BODY="${3:-}"

VPS="${SIGA_VPS:-root@100.104.236.78}"
CONTAINER="${SIGA_CONTAINER:-siga-dashboard}"
API_BASE="${SIGA_API_LOCAL:-http://127.0.0.1:8001}"
URL="${API_BASE}${PATHQ}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNNER="$SCRIPT_DIR/_run_in_container.py"
[[ -f "$RUNNER" ]] || { echo "ERREUR: $RUNNER introuvable" >&2; exit 2; }

# Clé SSH résolue dynamiquement (le chemin de session Cowork change à chaque session).
if [[ -n "${SIGA_SSH_KEY:-}" ]]; then
  KEY="$SIGA_SSH_KEY"
else
  KEY="$(ls /sessions/*/mnt/.ssh/codex_vps_tailscale_ed25519 2>/dev/null | head -1 || true)"
fi
[[ -n "${KEY:-}" && -f "$KEY" ]] || { echo "ERREUR: clé SSH introuvable (définir SIGA_SSH_KEY)" >&2; exit 2; }
SSH_OPTS=(-i "$KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=no -o ConnectTimeout=20)

# Args (method/url/body) encodés en un blob base64(JSON) — sûr à passer en argument shell.
ARGS_B64="$(python3 -c 'import json,base64,sys; print(base64.b64encode(json.dumps({"method":sys.argv[1],"url":sys.argv[2],"body":sys.argv[3]}).encode()).decode())' "$METHOD" "$URL" "$BODY")"

# Le runner Python est envoyé via stdin ; le blob d'args via argv. Le token reste dans le conteneur.
ssh "${SSH_OPTS[@]}" "$VPS" "docker exec -i $CONTAINER python3 - $ARGS_B64" < "$RUNNER"
