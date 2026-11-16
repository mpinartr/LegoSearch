#!/usr/bin/env bash
#
# Activa la protección de la rama principal en GitHub para que un PR con los
# tests en rojo no pueda mergearse.
#
# Requiere un token con permiso de ADMINISTRACIÓN sobre el repo:
#   - Fine-grained PAT: "Administration" -> Read and write
#   - Classic PAT:      scope "repo" (y ser admin del repositorio)
#
# Uso:
#   GITHUB_ADMIN_TOKEN=ghp_xxx ./scripts/proteger_rama.sh
#
# Opcionales:
#   GITHUB_REPO=mpinartr/LegoSearch   (por defecto)
#   GITHUB_BRANCH=main                (por defecto: la rama por defecto del repo)
#   CHECK=test                        (nombre del job de test.yml)
#
# Efecto:
#   - Exige que el check "test" pase antes de poder mergear (strict: la rama
#     debe estar actualizada con la base).
#   - Prohíbe force-push y borrado de la rama.
#   - No exige revisiones (pensado para trabajo en solitario).
#   - No afecta a los administradores (enforce_admins: false). Cambia a true
#     si también quieres que te aplique a ti.
#
set -euo pipefail

REPO="${GITHUB_REPO:-mpinartr/LegoSearch}"
CHECK="${CHECK:-test}"
TOKEN="${GITHUB_ADMIN_TOKEN:-${GITHUB_TOKEN:-}}"

if [[ -z "$TOKEN" ]]; then
  echo "ERROR: falta GITHUB_ADMIN_TOKEN (PAT con permiso de administración)." >&2
  echo "Ejemplo: GITHUB_ADMIN_TOKEN=ghp_xxx $0" >&2
  exit 1
fi

api() {
  curl -sS \
    -H "Authorization: Bearer $TOKEN" \
    -H "Accept: application/vnd.github+json" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    "$@"
}

# Si no se indica rama, se usa la rama por defecto del repositorio.
if [[ -z "${GITHUB_BRANCH:-}" ]]; then
  BRANCH="$(api "https://api.github.com/repos/$REPO" \
    | python3 -c 'import sys, json; print(json.load(sys.stdin)["default_branch"])')"
else
  BRANCH="$GITHUB_BRANCH"
fi

echo "Protegiendo $REPO@$BRANCH (check requerido: '$CHECK')..."

CUERPO=$(cat <<JSON
{
  "required_status_checks": {
    "strict": true,
    "contexts": ["$CHECK"]
  },
  "enforce_admins": false,
  "required_pull_request_reviews": null,
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "required_conversation_resolution": true
}
JSON
)

TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

HTTP="$(curl -sS -o "$TMP" -w '%{http_code}' -X PUT \
  -H "Authorization: Bearer $TOKEN" \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  "https://api.github.com/repos/$REPO/branches/$BRANCH/protection" \
  --data "$CUERPO")"

if [[ "$HTTP" == "200" ]]; then
  echo "OK: protección de rama activada en $BRANCH."
else
  echo "ERROR (HTTP $HTTP). Respuesta de GitHub:" >&2
  cat "$TMP" >&2
  echo >&2
  echo "Revisa que el token tenga permiso de administración y que el check" >&2
  echo "'$CHECK' haya aparecido al menos una vez (lanzar test.yml)." >&2
  exit 1
fi
