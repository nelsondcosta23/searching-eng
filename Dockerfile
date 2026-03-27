# Use an official modern Python base image (Bookworm)
FROM python:3.9-bookworm

# Set the working directory
WORKDIR /app

# Install essential system dependencies (includes cron for auto-scheduling)
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    unzip \
    curl \
    cron \
    && rm -rf /var/lib/apt/lists/*

# Install stable Google Chrome from .deb
RUN wget -q https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
    && apt-get update \
    && apt-get install -y ./google-chrome-stable_current_amd64.deb \
    && rm google-chrome-stable_current_amd64.deb \
    && rm -rf /var/lib/apt/lists/*

# Install required Python libraries
RUN pip install --no-cache-dir \
    beautifulsoup4 \
    requests \
    selenium \
    feedparser \
    undetected-chromedriver \
    streamlit \
    pandas \
    resend \
    fastapi \
    "uvicorn[standard]"

# Add crontab file
# Orchestrator: every day at 08:00 and 20:00 (UTC)
# Email:        every day at 13:15 (UTC) -> 13:15 UTC = 14:15 Portugal (Winter)
# Cleanup:      every Sunday at 03:00 (UTC)
COPY config/crontab /etc/cron.d/scraper_cron
RUN chmod 0644 /etc/cron.d/scraper_cron \
    && crontab /etc/cron.d/scraper_cron

# Default command will be overridden by docker-compose
CMD ["python"]