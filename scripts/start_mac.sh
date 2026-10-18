#!/usr/bin/env bash
# Start FinAlly in Docker (macOS/Linux). Safe to run repeatedly.
#   ./scripts/start_mac.sh            build image if missing, (re)start container
#   ./scripts/start_mac.sh --build    force an image rebuild
#   ./scripts/start_mac.sh --no-open  don't open the browser
set -euo pipefail

IMAGE="finally"
CONTAINER="finally"
VOLUME="finally-data"
PORT="${PORT:-8000}"
URL="http://localhost:${PORT}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

BUILD=0
OPEN=1
for arg in "$@"; do
  case "$arg" in
    --build|-b) BUILD=1 ;;
    --no-open) OPEN=0 ;;
    -h|--help) sed -n '2,5p' "$0"; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

if ! docker info >/dev/null 2>&1; then
  echo "Error: Docker is not running. Start Docker and try again." >&2
  exit 1
fi

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Warning: .env not found; created it from .env.example."
  echo "         Set OPENROUTER_API_KEY in .env to enable AI chat (or LLM_MOCK=true)."
fi

if [[ $BUILD -eq 1 ]] || ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "Building image '$IMAGE'..."
  docker build -t "$IMAGE" .
fi

if docker container inspect "$CONTAINER" >/dev/null 2>&1; then
  echo "Removing existing container '$CONTAINER'..."
  docker rm -f "$CONTAINER" >/dev/null
fi

echo "Starting container '$CONTAINER'..."
docker run -d \
  --name "$CONTAINER" \
  -v "$VOLUME":/app/db \
  -p "$PORT":8000 \
  --env-file .env \
  "$IMAGE" >/dev/null

printf "Waiting for the app"
for _ in $(seq 1 30); do
  if curl -fsS "$URL/api/health" >/dev/null 2>&1; then
    echo " ready."
    break
  fi
  printf "."
  sleep 1
done
echo
echo "FinAlly is running at $URL"
echo "Logs: docker logs -f $CONTAINER    Stop: ./scripts/stop_mac.sh"

if [[ $OPEN -eq 1 ]]; then
  if command -v open >/dev/null 2>&1; then
    open "$URL" >/dev/null 2>&1 || true
  elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$URL" >/dev/null 2>&1 || true
  fi
fi
