#!/bin/bash
# Roda os testes do backend na VPS, no banco geowork_test, com a árvore de trabalho atual (mesmo sem commit).
# Não toca na produção. Uso: scripts/testar-remoto.sh [argumentos do pytest]
set -euo pipefail
cd "$(dirname "$0")/.."

SSH_DESTINO="${GEOWORK_SSH:-root@179.197.82.224}"
SSH_CHAVE="${GEOWORK_SSH_KEY:-$HOME/.ssh/geowork_vps}"

# Arquivos versionados e novos ainda não ignorados pelo .gitignore
git ls-files -z --cached --others --exclude-standard | tar --null -T - -czf - |
  ssh -i "$SSH_CHAVE" "$SSH_DESTINO" 'rm -rf /opt/geowork-teste && mkdir -p /opt/geowork-teste && tar -xzf - -C /opt/geowork-teste'

ssh -i "$SSH_CHAVE" "$SSH_DESTINO" "bash -s -- $*" <<'REMOTO'
set -euo pipefail
DIR=/opt/geowork-teste
URL=$(grep '^GEOWORK_DATABASE_URL=' /etc/geowork/test.env | cut -d= -f2-)
for f in "$DIR"/db/migracoes/*.sql; do
  nome=$(basename "$f")
  if [ -z "$(psql "$URL" -tAc "SELECT 1 FROM schema_migracoes WHERE arquivo = '$nome'")" ]; then
    echo "migração $nome (banco de testes)"
    psql "$URL" -q -v ON_ERROR_STOP=1 -1 -f "$f"
    psql "$URL" -q -c "INSERT INTO schema_migracoes (arquivo) VALUES ('$nome')"
  fi
done
set -a; . /etc/geowork/test.env; set +a
cd "$DIR/backend" && /opt/geowork/backend/.venv/bin/pytest -q -p no:cacheprovider "$@"
REMOTO
