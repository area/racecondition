FROM python:3.12-slim

WORKDIR /app

# The server is pure standard-library Python — nothing to pip install.
COPY server/ /app/

# Persistent SQLite DB lives on a mounted volume (see fly.toml [[mounts]]).
# Create the mountpoint so the container also runs standalone (no volume):
# the Fly volume simply overlays this directory in production.
RUN mkdir -p /data
ENV RACECONDITION_DB=/data/racecondition.db

EXPOSE 8000

CMD ["python", "room_server.py"]
