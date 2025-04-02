FROM python:3.9-slim

WORKDIR /app

# Install required dependencies and Google Chrome
RUN apt-get update && \
    apt-get install -y wget gnupg curl unzip \
    libglib2.0-0 libnss3 libgconf-2-4 libfontconfig1 \
    libx11-6 libx11-xcb1 libxcb1 libxcomposite1 \
    libxcursor1 libxdamage1 libxext6 libxfixes3 \
    libxi6 libxrandr2 libxrender1 libxss1 libxtst6 \
    libappindicator1 libasound2 libatk1.0-0 \
    libatk-bridge2.0-0 libdbus-1-3 \
    xdg-utils fonts-liberation && \
    wget -q -O - https://dl.google.com/linux/linux_signing_key.pub | apt-key add - && \
    echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google-chrome.list && \
    apt-get update && \
    apt-get install -y google-chrome-stable && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* 

# Set timezone 
RUN apt-get update && \
    apt-get install -y tzdata && \
    ln -sf /usr/share/zoneinfo/Asia/Kolkata /etc/localtime && \
    echo "Asia/Kolkata" > /etc/timezone && \
    dpkg-reconfigure -f noninteractive tzdata && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Set display port to avoid crash
ENV DISPLAY=:99

# Create debug directory
RUN mkdir -p /app/debug/images && chmod -R 777 /app/debug

# Fix Chrome sandbox issues in Docker
ENV CHROME_BIN=/usr/bin/google-chrome
ENV CHROME_PATH=/usr/lib/chromium/
ENV PATH="/usr/bin/google-chrome:${PATH}"

# Add a non-root user with proper permissions
RUN groupadd -r chrome && useradd -r -g chrome -G audio,video chrome \
    && mkdir -p /home/chrome && chown -R chrome:chrome /home/chrome \
    && mkdir -p /app && chown -R chrome:chrome /app

# Set necessary Chrome flags for running in container
ENV CHROME_OPTIONS="--no-sandbox --disable-dev-shm-usage --disable-gpu --headless --remote-debugging-port=9222 --disable-software-rasterizer"

# Fix for /dev/shm limited memory issue
RUN echo 'kernel.unprivileged_userns_clone=1' > /etc/sysctl.d/00-local-userns.conf

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the code
COPY . .

# Fix permissions for all files and create debug directory
RUN chown -R chrome:chrome /app && \
    mkdir -p /app/debug/images && \
    chmod -R 777 /app/debug/images

# Expose port for debugging
EXPOSE 9222

# Switch to a non-root user for better security
USER chrome

# Use the direct command
CMD ["python", "main.py"]
