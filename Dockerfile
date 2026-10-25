FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg util-linux libudev1 dosfstools \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

ENV PYTHONUNBUFFERED=1 \
    CRISP_CONFIG=/app/config.yaml

# Runs as root: mounting the recorder requires it. Files in data/ are chowned to
# paths.owner_uid/owner_gid from config.yaml.
ENTRYPOINT ["crisp"]
CMD ["run"]
