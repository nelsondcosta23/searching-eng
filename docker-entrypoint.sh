#!/bin/bash
set -e

echo "--- 🛠️ Starting Search Engine Initialization ---"

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
