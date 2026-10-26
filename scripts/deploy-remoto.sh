#!/bin/bash
# Parte do deploy que roda na VPS. Chamado por scripts/deploy.sh; não rode direto sem a área de preparação.
set -euo pipefail
COMMIT="$1"
STAGING=/opt/geowork-staging
APP=/opt/geowork
VENV=$APP/backend/.venv

# Aplica, em ordem, os arquivos de db/migracoes ainda não registrados na tabela schema_migracoes
migrar() {
  local url="$1"
  psql "$url" -q -v ON_ERROR_STOP=1 -c \
    "CREATE TABLE IF NOT EXISTS schema_migracoes (arquivo text PRIMARY KEY, aplicada_em timestamptz NOT NULL DEFAULT now())"
  for f in "$STAGING"/db/migracoes/*.sql; do
    local nome
    nome=$(basename "$f")
    if [ -z "$(psql "$url" -tAc "SELECT 1 FROM schema_migracoes WHERE arquivo = '$nome'")" ]; then
      echo "  migração $nome"
      psql "$url" -q -v ON_ERROR_STOP=1 -1 -f "$f"
      psql "$url" -q -c "INSERT INTO schema_migracoes (arquivo) VALUES ('$nome')"
    fi
  done
}

URL_PROD=$(grep '^GEOWORK_DATABASE_URL=' /etc/geowork/env | cut -d= -f2-)
URL_TESTE=$(grep '^GEOWORK_DATABASE_URL=' /etc/geowork/test.env | cut -d= -f2-)

echo "Dependências"
$VENV/bin/pip install -q -r "$STAGING/backend/requirements.txt"

echo "Migrações no banco de testes"
migrar "$URL_TESTE"

echo "Testes"
(set -a; . /etc/geowork/test.env; set +a; cd "$STAGING/backend" && $VENV/bin/pytest -q -p no:cacheprovider) | tail -3

echo "Backup antes de migrar a produção"
/usr/local/bin/geowork-backup

echo "Migrações na produção"
migrar "$URL_PROD"

echo "Troca dos arquivos"
rsync -a --delete --exclude backend/.venv "$STAGING"/ "$APP"/
echo "$COMMIT $(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$APP/VERSAO"
chown -R geowork:geowork "$APP/backend"

echo "Reinício"
systemctl restart geowork.service
for _ in $(seq 1 20); do
  curl -sf http://127.0.0.1:8000/health >/dev/null && break
  sleep 1
done
curl -sf http://127.0.0.1:8000/health >/dev/null || { echo "A API não respondeu depois do reinício" >&2; exit 1; }
echo "Publicado: $(cat "$APP/VERSAO")"
