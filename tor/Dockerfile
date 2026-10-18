FROM debian:trixie-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends tor \
    && rm -rf /var/lib/apt/lists/*

# Adressen per Variable (Standard: nur lokal wie bisher). Für andere Projekte siehe tor/README.md.
#   TOR_SOCKS_ADDR    SOCKS-Proxy, über den Programme ins Netz gehen      (Standard 127.0.0.1:9050)
#   TOR_CONTROL_ADDR  ControlPort für neue Routen, mit Passwort           (Standard 127.0.0.1:9051)
#   TOR_CONTROL_PASSWORD  Passwort für den ControlPort
ENV TOR_SOCKS_ADDR=127.0.0.1:9050 \
    TOR_CONTROL_ADDR=127.0.0.1:9051

USER debian-tor
CMD ["sh", "-c", "exec tor --SocksPort \"$TOR_SOCKS_ADDR\" --ControlPort \"$TOR_CONTROL_ADDR\" --HashedControlPassword \"$(tor --quiet --hash-password \"$TOR_CONTROL_PASSWORD\")\" --Log 'notice stdout'"]
