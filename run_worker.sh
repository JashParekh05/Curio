#!/bin/bash
set -e

cd "$(dirname "$0")/backend"

# Stop the Docker worker so it doesn't compete
docker compose -f ../docker-compose.yml stop worker 2>/dev/null || true

# Install deps if needed
pip install -q -r requirements.txt

# Load .env and override the cookies path to a local file path
export $(grep -v '^#' .env | xargs)
export REDIS_URL=redis://localhost:6379/0
export YOUTUBE_COOKIES_FILE="$(pwd)/youtube_cookies.txt"

echo "Starting Celery worker on your Mac's native network..."
echo "Redis:   $REDIS_URL"
echo "Cookies: $YOUTUBE_COOKIES_FILE"
echo ""

celery -A app.workers.video_worker.celery worker --loglevel=info --concurrency=1
