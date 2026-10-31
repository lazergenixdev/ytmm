# The ytmm worker with everything downloading needs: yt-dlp, ffmpeg, Deno and
# the PO token provider plugin. Build from the repository root:
#   docker build -f deploy/Dockerfile -t ytmm .
FROM python:3.13-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*
COPY --from=denoland/deno:bin /deno /usr/local/bin/deno
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# A virtualenv owned by the worker's user, so update_command can upgrade yt-dlp in place:
#   update_command = "uv pip install --upgrade 'yt-dlp[default]' bgutil-ytdlp-pot-provider"
ENV VIRTUAL_ENV=/opt/ytmm \
    PATH=/opt/ytmm/bin:$PATH \
    UV_CACHE_DIR=/tmp/uv-cache \
    YTMM_CONFIG=/config/config.toml \
    PYTHONUNBUFFERED=1
COPY . /src
RUN uv venv /opt/ytmm \
 && uv pip install --no-cache '/src[server]' bgutil-ytdlp-pot-provider \
 && chown -R 1000:1000 /opt/ytmm

USER 1000:1000
CMD ["ytmm", "worker"]
