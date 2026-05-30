#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-.venv/bin/python}"
PROVIDER="${PROVIDER:-deepl}"
LOCALES=(${LOCALES:-en pl tr ru ar})
TEMPLATE_PATHS=("$@")
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$PROJECT_ROOT"

if [ "${#TEMPLATE_PATHS[@]}" -eq 0 ]; then
  TEMPLATE_PATHS=(
    "brainboost/core/templates/landing.html"
    "brainboost/core/templates/landing_alle.html"
    "brainboost/core/templates/landing_eltern.html"
    "brainboost/core/templates/landing_schuelerinnen.html"
    "brainboost/core/templates/landing_tutor.html"
    "brainboost/core/templates/contact.html"
    "brainboost/core/templates/base.html"
    "brainboost/core/templates/partials/price_banner.html"
    "brainboost/core/templates/partials/cookie_notice.html"
  )
fi

python3 scripts/mark_template_i18n.py --write "${TEMPLATE_PATHS[@]}"

for locale in "${LOCALES[@]}"; do
  "$PYTHON_BIN" brainboost/manage.py makemessages -l "$locale"
done

python3 scripts/translate_po.py --provider "$PROVIDER" --locales "${LOCALES[@]}"

"$PYTHON_BIN" brainboost/manage.py compilemessages \
  --ignore ".venv/*" \
  --ignore "venv/*" \
  --ignore "env/*"

if ! "$PYTHON_BIN" scripts/translate_faqs.py --provider "$PROVIDER" --locales "${LOCALES[@]}"; then
  echo "FAQ translation failed. Check database connectivity, migrations, and DeepL settings." >&2
  exit 1
fi
