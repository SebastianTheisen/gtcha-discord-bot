# GTCHA Discord Bot - Dockerfile
# Optimiert für Railway.app mit Playwright Chromium + optionalem Lightpanda

FROM python:3.11-slim-bookworm

# Umgebungsvariablen
ENV PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    MALLOC_ARENA_MAX=2

WORKDIR /app

# Installiere ALLE Chromium Dependencies (Debian Bookworm kompatibel!)
# + curl für Lightpanda-Download und Health-Check
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    # Core Chromium dependencies
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libdbus-1-3 \
    libxcb1 \
    libxkbcommon0 \
    libx11-6 \
    libxcomposite1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    libatspi2.0-0 \
    libxshmfence1 \
    # GTK und Graphics (KORRIGIERTE Paketnamen für Bookworm!)
    libgtk-3-0 \
    libgdk-pixbuf-2.0-0 \
    libegl1 \
    libglib2.0-0 \
    # Fonts (WICHTIG für korrektes Rendering!)
    fonts-liberation \
    fonts-noto-color-emoji \
    fonts-noto-cjk \
    # Cleanup
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Lightpanda Binary herunterladen (leichtgewichtiger Headless-Browser)
RUN curl -L -o /usr/local/bin/lightpanda \
    https://github.com/lightpanda-io/browser/releases/download/nightly/lightpanda-x86_64-linux \
    && chmod +x /usr/local/bin/lightpanda

# Playwright Browser-Verzeichnis erstellen
RUN mkdir -p /ms-playwright && chmod 755 /ms-playwright

# Python Dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Playwright Chromium installieren
RUN playwright install chromium

# Bot-Code kopieren
COPY . .

# Verzeichnisse erstellen
RUN mkdir -p /app/data /app/logs /app/screenshots/debug

# Entrypoint: startet Lightpanda im Hintergrund wenn BROWSER_MODE=lightpanda
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

CMD ["/app/entrypoint.sh"]
