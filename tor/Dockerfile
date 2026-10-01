FROM debian:trixie-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends tor \
    && rm -rf /var/lib/apt/lists/*

USER debian-tor
# ControlPort (nur lokal, mit Passwort): der Bot fordert darüber vor jedem Scrape neue Routen an
CMD ["sh", "-c", "exec tor --SocksPort 127.0.0.1:9050 --ControlPort 127.0.0.1:9051 --HashedControlPassword \"$(tor --quiet --hash-password \"$TOR_CONTROL_PASSWORD\")\" --Log 'notice stdout'"]
