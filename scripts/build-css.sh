#!/bin/bash
# Regera css/tailwind.css. Rode depois de mudar classes no HTML ou no JS e commite o resultado.
# Requer o executável standalone do Tailwind CLI v3.4.17 (https://github.com/tailwindlabs/tailwindcss/releases),
# informado em TAILWIND_BIN ou disponível no PATH como tailwindcss.
set -euo pipefail
cd "$(dirname "$0")/.."
"${TAILWIND_BIN:-tailwindcss}" -c tailwind.config.js -i css/tailwind.src.css -o css/tailwind.css --minify
