#!/bin/bash
# Installation de Forex Sync.
# A lancer UNE FOIS depuis le Terminal (pas en double-clic) :
#   bash install.sh
# Le lancement depuis le Terminal est ce qui declenche la popup
# "Terminal souhaite controler Calendrier" -> il faut l'accepter.

set -euo pipefail

APP_DIR="$HOME/Library/Application Support/ForexSync"
AGENT_DIR="$HOME/Library/LaunchAgents"
LABEL="com.local.forexsync"
PLIST="$AGENT_DIR/$LABEL.plist"
SRC_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "==> Recherche de python3"
PY="$(command -v python3 || true)"
if [ -z "$PY" ]; then
  echo "python3 introuvable. Installe les Command Line Tools :"
  echo "   xcode-select --install"
  exit 1
fi
if ! "$PY" -c 'import zoneinfo' 2>/dev/null; then
  echo "Ce python3 ($PY) n'a pas zoneinfo (Python < 3.9). Installe un python3 recent."
  exit 1
fi
echo "    $PY  ($("$PY" -V 2>&1))"

echo "==> Installation du script dans $APP_DIR"
mkdir -p "$APP_DIR" "$AGENT_DIR" "$HOME/Library/Logs"
cp "$SRC_DIR/forex_sync.py" "$APP_DIR/forex_sync.py"
chmod +x "$APP_DIR/forex_sync.py"

echo "==> Ecriture du LaunchAgent"
cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>

    <key>ProgramArguments</key>
    <array>
        <string>$PY</string>
        <string>$APP_DIR/forex_sync.py</string>
    </array>

    <!-- A l'ouverture de session / au chargement de l'agent -->
    <key>RunAtLoad</key>
    <true/>

    <!-- Toutes les heures. launchd declenche immediatement au reveil
         si le creneau est tombe pendant le sommeil. -->
    <key>StartInterval</key>
    <integer>3600</integer>

    <!-- Filet dedie : dimanche 20h00 (Weekday 0 = dimanche) -->
    <key>StartCalendarInterval</key>
    <dict>
        <key>Weekday</key><integer>0</integer>
        <key>Hour</key><integer>20</integer>
        <key>Minute</key><integer>0</integer>
    </dict>

    <key>StandardOutPath</key>
    <string>$HOME/Library/Logs/forexsync.out.log</string>
    <key>StandardErrorPath</key>
    <string>$HOME/Library/Logs/forexsync.err.log</string>

    <key>ProcessType</key>
    <string>Background</string>
</dict>
</plist>
PLISTEOF

echo "==> (Re)chargement du LaunchAgent"
launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$UID" "$PLIST"
launchctl enable "gui/$UID/$LABEL"

echo
echo "==> Premier lancement (accepte la popup Calendrier si elle apparait)"
"$PY" "$APP_DIR/forex_sync.py" --force

echo
echo "Termine."
echo "  Script    : $APP_DIR/forex_sync.py"
echo "  Agent     : $PLIST"
echo "  Journal   : $HOME/Library/Logs/forexsync.log"
echo
echo "Reveil programme du dimanche soir (optionnel, sur secteur surtout) :"
echo "  sudo pmset repeat wakeorpoweron U 19:55:00"
echo
echo "Desinstallation :"
echo "  launchctl bootout gui/$UID/$LABEL && rm -rf \"$APP_DIR\" \"$PLIST\""
