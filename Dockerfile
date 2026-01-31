# GTCHA Discord Bot - Dockerfile
# WebKit-basierter Scraper für Railway.app

FROM python:3.11-slim-bookworm

# Umgebungsvariablen
ENV PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    MALLOC_ARENA_MAX=2

WORKDIR /app

# WebKit + allgemeine Dependencies installieren
# curl für Health-Check, fonts für korrektes Rendering
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    # Fonts (WICHTIG für korrektes Rendering!)
    fonts-liberation \
    fonts-noto-color-emoji \
    fonts-noto-cjk \
    # Cleanup
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Playwright Browser-Verzeichnis erstellen
RUN mkdir -p /ms-playwright && chmod 755 /ms-playwright

# Python Dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Playwright WebKit installieren (+ System-Dependencies)
RUN playwright install --with-deps webkit

# Bot-Code kopieren
COPY . .

# Verzeichnisse erstellen
RUN mkdir -p /app/data /app/logs /app/screenshots/debug

CMD ["python", "main.py"]
