#!/bin/bash
set -e

echo "--- 🛠️ Starting Search Engine Initialization ---"

# Start Xvfb virtual display so Selenium scrapers can run in non-headless mode.
# Non-headless Chrome bypasses WAF fingerprinting (e.g. Expresso 403 fix).
if command -v Xvfb &>/dev/null; then
    Xvfb :99 -screen 0 1920x1080x24 -ac &>/dev/null &
    export DISPLAY=:99
    echo "Xvfb started on :99"
fi

# Ensure the database is initialized/migrated
if [ -f "init_db.py" ]; then
    echo "Running database initialization/migration..."
    python init_db.py
else
    echo "⚠️ Warning: init_db.py not found, skipping DB init."
fi

echo "--- 🚀 Initialization Complete. Starting Service... ---"

# Execute the passed command
exec "$@"
