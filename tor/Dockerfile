FROM debian:trixie-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends tor \
    && rm -rf /var/lib/apt/lists/*

USER debian-tor
CMD ["tor", "--SocksPort", "127.0.0.1:9050", "--Log", "notice stdout"]
