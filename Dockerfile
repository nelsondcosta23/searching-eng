# Use an official modern Python base image (Bookworm)
FROM python:3.9-bookworm

# Set the working directory
WORKDIR /app

# Install essential system dependencies (includes cron and Chrome libs)
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    unzip \
    curl \
    cron \
    xvfb \
    libnss3 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libxkbcommon0 \
    libpango-1.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Install stable Google Chrome from .deb
RUN wget -q https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
    && apt-get update \
    && apt-get install -y ./google-chrome-stable_current_amd64.deb \
    && rm google-chrome-stable_current_amd64.deb \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python libraries
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Add crontab file
COPY config/crontab /etc/cron.d/scraper_cron
RUN chmod 0644 /etc/cron.d/scraper_cron \
    && crontab /etc/cron.d/scraper_cron

# Prepare entrypoint
COPY docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Entrypoint handles DB init/migrations
ENTRYPOINT ["docker-entrypoint.sh"]

# Default command
CMD ["python"]