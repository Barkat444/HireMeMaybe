FROM python:3.9-slim

WORKDIR /app

# Install dependencies + Chrome
RUN apt-get update && \
    apt-get install -y \
    wget curl gnupg unzip ca-certificates \
    libglib2.0-0 libnss3 libfontconfig1 \
    libx11-6 libx11-xcb1 libxcb1 libxcomposite1 \
    libxcursor1 libxdamage1 libxext6 libxfixes3 \
    libxi6 libxrandr2 libxrender1 libxss1 libxtst6 \
    libayatana-appindicator3-1 \
    libasound2 libatk1.0-0 libatk-bridge2.0-0 \
    libdbus-1-3 xdg-utils fonts-liberation && \
    \
    # Add Google Chrome repo (modern way)
    mkdir -p /usr/share/keyrings && \
    wget -q -O /usr/share/keyrings/google-linux.gpg \
      https://dl.google.com/linux/linux_signing_key.pub && \
    echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-linux.gpg] \
      http://dl.google.com/linux/chrome/deb/ stable main" \
      > /etc/apt/sources.list.d/google-chrome.list && \
    \
    apt-get update && \
    apt-get install -y google-chrome-stable && \
    \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Timezone
RUN apt-get update && \
    apt-get install -y tzdata && \
    ln -sf /usr/share/zoneinfo/Asia/Kolkata /etc/localtime && \
    echo "Asia/Kolkata" > /etc/timezone && \
    dpkg-reconfigure -f noninteractive tzdata && \
    rm -rf /var/lib/apt/lists/*

# Chrome runtime env
ENV DISPLAY=:99
ENV CHROME_BIN=/usr/bin/google-chrome
ENV PATH="/usr/bin/google-chrome:${PATH}"
ENV CHROME_OPTIONS="--no-sandbox --disable-dev-shm-usage --disable-gpu --headless --remote-debugging-port=9222"

# Create non-root user
RUN groupadd -r chrome && useradd -r -g chrome -G audio,video chrome && \
    mkdir -p /home/chrome /app/debug/images && \
    chown -R chrome:chrome /home/chrome /app && \
    chmod -R 777 /app/debug

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code
COPY . .
RUN chown -R chrome:chrome /app

EXPOSE 9222
USER chrome

CMD ["python", "main.py"]
