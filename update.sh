#!/bin/bash
# Holt die neue Version von GitHub (main), baut nur, was sich geändert hat, und prüft danach, ob alles läuft.
# Aufruf auf dem VPS:  cd ~/gtcha-discord-bot && ./update.sh        (./update.sh --alles baut alles neu)
set -e
cd "$(dirname "$0")"

# .env vorab prüfen: jede Zeile NAME=Wert (ohne Leerzeichen im Namen) - zeigt nur Zeilennummer und Namen, nie Werte
if [ -f .env ]; then
  bad=$(grep -nvE '^[[:space:]]*(#.*)?$|^[A-Za-z_][A-Za-z0-9_]*=' .env | cut -d= -f1 | cut -c1-40)
  if [ -n "$bad" ]; then
    echo "❌ Die .env hat ungültige Zeilen (erwartet NAME=Wert, ohne Leerzeichen um das =):"
    echo "$bad" | sed 's/^/   Zeile /'
    echo "   Korrigieren mit:  nano .env"
    exit 1
  fi
fi

# Hängengebliebene Sperre (z. B. nach Abbruch mit Strg+C) entfernen, wenn kein git mehr läuft
if [ -f .git/index.lock ] && ! pgrep -x git > /dev/null; then
  rm -f .git/index.lock
  echo "Alte git-Sperre entfernt."
fi

old=$(git rev-parse HEAD)
echo "Hole neue Version von GitHub …"
if ! timeout 60 git pull --ff-only origin main; then
  echo "❌ GitHub nicht erreichbar oder Abruf fehlgeschlagen (Zeitlimit 60 s). In ein paar Minuten erneut versuchen."
  exit 1
fi
new=$(git rev-parse HEAD)

if [ "$old" = "$new" ] && [ "$1" != "--alles" ]; then
  echo "✅ Schon aktuell ($(git log -1 --format='%h %s'))"
  exit 0
fi

changed=$(git diff --name-only "$old" "$new")
echo "Neu seit dem letzten Stand:"
git log --oneline "$old..$new" | sed 's/^/  /'

services=""
if [ "$1" = "--alles" ] || echo "$changed" | grep -qE '^(bot|scraper|database|utils|services|tor)/|^(main|config)\.py$|^requirements\.txt$|^Dockerfile$|^docker-compose\.yml$'; then
  services="$services gtcha-bot"
fi
if [ "$1" = "--alles" ] || echo "$changed" | grep -qE '^(webapp|utils|database)/|^requirements-webapp\.txt$|^Dockerfile\.webapp$|^docker-compose\.yml$'; then
  services="$services gtcha-app"
fi
if [ "$1" = "--alles" ] || echo "$changed" | grep -qE '^tor/'; then
  services="$services tor"
fi

if [ -z "$services" ]; then
  echo "✅ Nur Dokumentation/Werkzeuge geändert – nichts neu zu bauen."
  exit 0
fi

echo "Baue neu:$services"
docker compose up -d --build $services

# Prüfen: Web-App antwortet, Container laufen
if echo "$services" | grep -q gtcha-app; then
  port=$(grep -E '^WEBAPP_PORT=' .env 2>/dev/null | cut -d= -f2)
  for i in $(seq 1 30); do
    if curl -fs "http://127.0.0.1:${port:-8080}/api/health" > /dev/null; then
      echo "✅ Web-App läuft"
      break
    fi
    [ "$i" = 30 ] && { echo "❌ Web-App antwortet nicht – docker logs --tail 50 gtcha-app"; exit 1; }
    sleep 3
  done
fi
for c in $services; do
  name=$c; [ "$c" = gtcha-bot ] && name=gtcha-discord-bot; [ "$c" = tor ] && name=gtcha-tor
  state=$(docker inspect -f '{{.State.Status}}' "$name" 2>/dev/null || echo fehlt)
  [ "$state" = running ] && echo "✅ $name läuft" || { echo "❌ $name: $state – docker logs --tail 50 $name"; exit 1; }
done
echo "Fertig. App-Version: $(grep -oE 'APP_VERSION = [0-9]+' webapp/static/app.js | grep -oE '[0-9]+')"
