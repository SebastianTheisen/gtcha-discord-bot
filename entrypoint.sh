#!/bin/bash
# Entrypoint: Startet Lightpanda im Hintergrund (falls vorhanden) und dann den Bot

if [ "$BROWSER_MODE" = "lightpanda" ] && [ -x /usr/local/bin/lightpanda ]; then
    echo "Starte Lightpanda auf Port 9222..."
    LIGHTPANDA_DISABLE_TELEMETRY=true /usr/local/bin/lightpanda serve --host 127.0.0.1 --port 9222 &
    LIGHTPANDA_PID=$!
    # Warte bis CDP-Endpoint bereit ist (max 10s)
    for i in $(seq 1 20); do
        if curl -s http://127.0.0.1:9222/json/version > /dev/null 2>&1; then
            echo "Lightpanda bereit."
            break
        fi
        sleep 0.5
    done
fi

exec python main.py
