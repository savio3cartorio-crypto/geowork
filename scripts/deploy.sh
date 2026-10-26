#!/bin/bash
# Publica na VPS exatamente o commit atual (HEAD). Recusa se houver alterações não commitadas.
# Uso: scripts/deploy.sh
# Variáveis opcionais: GEOWORK_SSH (padrão root@179.197.82.224), GEOWORK_SSH_KEY (padrão ~/.ssh/geowork_vps)
set -euo pipefail
cd "$(dirname "$0")/.."

SSH_DESTINO="${GEOWORK_SSH:-root@179.197.82.224}"
SSH_CHAVE="${GEOWORK_SSH_KEY:-$HOME/.ssh/geowork_vps}"

if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "Há alterações não commitadas. Faça o commit antes de publicar." >&2
  exit 1
fi

COMMIT=$(git rev-parse --short HEAD)
echo "Publicando o commit $COMMIT em $SSH_DESTINO"

# 1. Envia o commit para a área de preparação
git archive --format=tar HEAD | ssh -i "$SSH_CHAVE" "$SSH_DESTINO" \
  'rm -rf /opt/geowork-staging && mkdir -p /opt/geowork-staging && tar -xf - -C /opt/geowork-staging'

# 2. Testa, faz backup, migra, troca os arquivos e reinicia (tudo no servidor)
ssh -i "$SSH_CHAVE" "$SSH_DESTINO" "bash -s -- $COMMIT" < scripts/deploy-remoto.sh
