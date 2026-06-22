FROM python:3.12-slim

WORKDIR /app

# aiohttp serves both the HTTP API and the WebSocket endpoint. Install deps
# first (separate layer) so source changes don't bust the pip cache.
COPY server/requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt
COPY server/ /app/

# Persistent SQLite DB lives on a mounted volume (see fly.toml [[mounts]]).
# Create the mountpoint so the container also runs standalone (no volume):
# the Fly volume simply overlays this directory in production.
RUN mkdir -p /data
ENV RACECONDITION_DB=/data/racecondition.db

EXPOSE 8000

CMD ["python", "room_server.py"]
