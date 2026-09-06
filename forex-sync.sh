#!/bin/bash

# Commande Raycast — a placer dans un dossier de "Script Commands"
# (Raycast > Extensions > Script Commands > Add Script Directory).

# @raycast.schemaVersion 1
# @raycast.title Forex Sync
# @raycast.mode fullOutput
# @raycast.packageName Trading

# @raycast.icon 📈
# @raycast.description Pousse les news rouges et jours feries EUR/USD/JPY de la semaine dans Calendar.
# @raycast.author local

SCRIPT="$HOME/Library/Application Support/ForexSync/forex_sync.py"

if [ ! -f "$SCRIPT" ]; then
  echo "Forex Sync n'est pas installe."
  echo "Lance d'abord install.sh depuis le Terminal."
  exit 1
fi

PY="$(command -v python3)"
# --force : un lancement manuel doit toujours reconcilier avec Calendar,
# sans se fier a l'empreinte du dernier run.
exec "$PY" "$SCRIPT" --force
