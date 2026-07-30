# Snapchat Memories Sorter has no dependencies, so the image is just Python plus the source.
FROM python:3.12-alpine

LABEL org.opencontainers.image.title="Snapchat Memories Sorter" \
      org.opencontainers.image.description="Sort your Snapchat memories at the speed of a swipe." \
      org.opencontainers.image.licenses="MIT"

WORKDIR /app
COPY sorter.py ./
COPY sorter ./sorter

# The file browser and the auto-detection both start from the home directory:
# point it at the mounted archives.
ENV HOME=/data \
    PYTHONUNBUFFERED=1

# Mount your export on /data (read-only is a good idea) and the destination
# on /out. Nothing is written outside /out.
VOLUME ["/data", "/out"]
EXPOSE 8765

# 0.0.0.0 is safe here and only here: the container has its own network
# namespace, and the published port is bound to 127.0.0.1 on the host.
ENTRYPOINT ["python3", "sorter.py", "--host", "0.0.0.0", "--port", "8765", "--no-browser"]
