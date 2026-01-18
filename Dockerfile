FROM python:3.11-slim

# Install ALL Chromium dependencies
RUN apt-get update && apt-get install -y \
    # Basis
    wget \
    gnupg \
    ca-certificates \
    # Chromium dependencies
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libpango-1.0-0 \
    libcairo2 \
    libatspi2.0-0 \
    libgtk-3-0 \
    libgdk-pixbuf2.0-0 \
    libx11-xcb1 \
    libxcb-dri3-0 \
    libxshmfence1 \
    libglu1-mesa \
    # Additional for JavaScript
    libxss1 \
    libxtst6 \
    libx11-6 \
    libxcb1 \
    libxext6 \
    libxi6 \
    # Fonts (important for rendering)
    fonts-liberation \
    fonts-noto-cjk \
    fonts-noto-color-emoji \
    # D-Bus (important for Chromium)
    dbus \
    dbus-x11 \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# D-Bus Setup
RUN mkdir -p /var/run/dbus

WORKDIR /app

# Python Dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright with ALL dependencies
RUN playwright install chromium
RUN playwright install-deps chromium

# Copy bot code
COPY . .

# Create directories
RUN mkdir -p /app/data /app/logs /app/screenshots/debug

CMD ["python", "main.py"]
