FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim
ARG BLENDER_VERSION=5.2.1
ARG BLENDER_SERIES=Blender5.2
# Blender binaries use the upstream release server.
ARG APT_MIRROR=http://deb.debian.org
ARG BLENDER_MIRROR=https://download.blender.org/release
ARG BLENDER_SHA256=a31f524fa99a527d3d52b7f5aaa68c34e1a19d5a1c9473f79c5cc610fd5b10e9
RUN sed -i "s|http://deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources \
    && apt-get update && apt-get install -y --no-install-recommends curl xz-utils libx11-6 libxi6 libxrender1 libxfixes3 libxkbcommon0 libsm6 libgl1 libegl1 libglib2.0-0 libgomp1 ca-certificates xvfb libxkbcommon-x11-0 libxxf86vm1 && rm -rf /var/lib/apt/lists/*
RUN curl -fSL --retry 3 "${BLENDER_MIRROR}/${BLENDER_SERIES}/blender-${BLENDER_VERSION}-linux-x64.tar.xz" -o /tmp/blender.tar.xz \
    && echo "${BLENDER_SHA256}  /tmp/blender.tar.xz" | sha256sum -c - \
    && mkdir /opt/blender && tar -xJf /tmp/blender.tar.xz --strip-components=1 -C /opt/blender \
    && rm /tmp/blender.tar.xz
ENV PATH="/opt/blender:/app/.venv/bin:$PATH" WORK_DIR=/work NVIDIA_DRIVER_CAPABILITIES=compute,utility,graphics
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev
COPY vendor ./vendor
ENV PYTHONPATH=/app/vendor/blender-mcp/src BLENDER_MCP_DISABLE_TELEMETRY=1 BLENDERMCP_ADDONS_DIR=/app/vendor/blender-mcp
COPY blender_server ./blender_server
COPY THIRD_PARTY_NOTICES.md /app/THIRD_PARTY_NOTICES.md
ARG APP_UID=1000
ARG APP_GID=1000
RUN groupadd --gid ${APP_GID} renderer && useradd --create-home --uid ${APP_UID} --gid ${APP_GID} renderer && mkdir /work && chown renderer:renderer /work
USER renderer
EXPOSE 10849
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:10849/health')"
CMD ["python", "-m", "blender_server.entrypoint"]
