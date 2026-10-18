# Tor-Container (SOCKS-Proxy mit neuer Route auf Abruf)

Ein schlanker Container mit Tor. Programme gehen über den **SOCKS-Proxy** ins Netz und haben damit eine
Tor-Ausgangs-IP. Über den **ControlPort** (mit Passwort) lässt sich jederzeit eine neue Route anfordern, also eine
neue Ausgangs-IP für die nächsten Verbindungen.

| Variable | Bedeutung | Standard |
|---|---|---|
| `TOR_SOCKS_ADDR` | Adresse:Port, auf der der SOCKS-Proxy lauscht | `127.0.0.1:9050` |
| `TOR_CONTROL_ADDR` | Adresse:Port des ControlPorts | `127.0.0.1:9051` |
| `TOR_CONTROL_PASSWORD` | Passwort für den ControlPort | – (muss gesetzt sein) |

## Im GTCHA-Projekt (wie bisher)

Nichts zu tun. Der Container läuft im Host-Netz und lauscht nur lokal (`127.0.0.1:9050` / `:9051`).
Andere Adresse gewünscht? In der `.env` des GTCHA-Projekts setzen und `docker compose up -d` ausführen:

```
TOR_SOCKS_ADDR=127.0.0.1:9150
TOR_CONTROL_ADDR=127.0.0.1:9151
```

Der Bot übernimmt die Werte automatisch (`SCRAPER_PROXY` und neue Routen).

> Ein **zweites Projekt auf demselben Server** kann den laufenden GTCHA-Tor einfach mitbenutzen: Proxy
> `socks5h://127.0.0.1:9050` (wenn das Projekt im Host-Netz läuft oder direkt auf dem Server). Dann teilen sich beide
> Projekte aber die Ausgangs-IP, und eine neue Route wirkt für beide. Für ein sauber getrenntes Projekt den
> eigenständigen Container unten nehmen.

## Eigenständig in einem anderen Projekt

1. Den Ordner `tor/` in das andere Projekt kopieren (`Dockerfile`, `docker-compose.standalone.yml`, `.env.example`).
2. Passwort setzen:
   ```
   cd tor
   cp .env.example .env
   nano .env            # TOR_CONTROL_PASSWORD=… eintragen
   ```
3. Starten:
   ```
   docker compose -f docker-compose.standalone.yml up -d --build
   docker logs -f tor-proxy        # warten auf "Bootstrapped 100% (done)"
   ```

Der Container hängt im Docker-Netz **`tor-net`** und ist dort unter dem Namen `tor` erreichbar. Nach außen sind
**keine Ports offen**.

### Das andere Projekt anbinden

**a) Das andere Projekt läuft auch in Docker (empfohlen).** In dessen `docker-compose.yml`:

```yaml
services:
  mein-dienst:
    # …
    environment:
      - HTTPS_PROXY=socks5h://tor:9050      # bzw. die Proxy-Variable, die das Programm erwartet
      - TOR_CONTROL_ADDR=tor:9051
      - TOR_CONTROL_PASSWORD=${TOR_CONTROL_PASSWORD}
    networks:
      - tor-net

networks:
  tor-net:
    external: true
```

**b) Das Programm läuft direkt auf dem Server (ohne Docker).** In `docker-compose.standalone.yml` den Block `ports:`
einkommentieren. Dann ist der Proxy unter `socks5h://127.0.0.1:9050` und der ControlPort unter `127.0.0.1:9051`
erreichbar, aber nur vom Server selbst.

### Proxy-Adresse im Programm

- `socks5h://…` (mit **h**): Auch die DNS-Auflösung läuft über Tor. Ohne h würde der Server die Adresse selbst
  auflösen, und das ist sichtbar.
- Python `requests`: `pip install requests[socks]`, dann `requests.get(url, proxies={"https": "socks5h://tor:9050"})`
- `curl --socks5-hostname tor:9050 https://check.torproject.org/api/ip` zeigt, ob die Verbindung über Tor geht.
- Playwright/Chromium: `browser = await p.chromium.launch(proxy={"server": "socks5://tor:9050"})`

### Neue Route (neue Ausgangs-IP) anfordern

Über den ControlPort mit dem Passwort `SIGNAL NEWNYM` senden. Tor erlaubt das höchstens etwa alle 10 Sekunden.
Beispiel in Python, ohne zusätzliche Bibliothek, wie im GTCHA-Bot (`utils/maintenance.py`):

```python
import asyncio, os

async def new_tor_identity() -> bool:
    host, _, port = os.getenv("TOR_CONTROL_ADDR", "127.0.0.1:9051").rpartition(":")
    reader, writer = await asyncio.open_connection(host, int(port))
    writer.write(f'AUTHENTICATE "{os.environ["TOR_CONTROL_PASSWORD"]}"\r\nSIGNAL NEWNYM\r\nQUIT\r\n'.encode())
    await writer.drain()
    reply = (await reader.read(1024)).decode()
    writer.close()
    return reply.count("250 OK") >= 2
```

Schneller Test auf der Kommandozeile:

```
printf 'AUTHENTICATE "PASSWORT"\r\nSIGNAL NEWNYM\r\nQUIT\r\n' | nc 127.0.0.1 9051
```

Zweimal `250 OK` in der Antwort bedeutet, dass die neue Route angefordert wurde.

## Sicherheit

- **Ports 9050/9051 nie ins Internet freigeben** (kein `"9050:9050"` ohne `127.0.0.1:` davor). Ein offener
  SOCKS-Proxy wird innerhalb von Minuten gefunden und missbraucht.
- Im Docker-Netz `tor-net` darf Tor auf `0.0.0.0` lauschen, denn dorthin kommen nur Container, die man selbst ins
  Netz hängt. Tor schreibt dazu beim Start eine Warnung ins Log, die man in diesem Aufbau ignorieren kann.
- Das Passwort für den ControlPort nicht im Code speichern, sondern nur in der `.env` (nicht ins Git).
