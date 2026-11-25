# Image published to Docker Hub and run by Render (port 8501).
# The pipeline tags it with a hash of the files the build reads: keep that list
# in .github/workflows/pipeline.yml in step with the COPY lines and with the
# sources of the bind mounts.
FROM python:3.13-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.13.9 /uv /uvx /bin/

WORKDIR /app

ENV UV_NO_DEV=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project

COPY src/ ./src/

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=README.md,target=README.md \
    uv sync --locked

FROM python:3.13-slim

RUN useradd --create-home --uid 1000 app

WORKDIR /app

COPY --from=builder --chown=app:app /app/.venv ./.venv
ENV PATH="/app/.venv/bin:$PATH"

COPY --chown=app:app .streamlit/ ./.streamlit/
COPY --chown=app:app src/ ./src/
COPY --chown=app:app data/ ./data/

# After the COPY lines: the tag is not a file of the repository, so it stays out of
# the tag hash. Streamlit serves it at /app/static/version.txt.
ARG IMAGE_TAG=dev
RUN mkdir src/static && echo "$IMAGE_TAG" > src/static/version.txt && chown -R app:app src/static

USER app

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=3)"]

ENTRYPOINT ["streamlit", "run", "src/streamlit_app.py", "--server.port=8501", "--server.address=0.0.0.0"]
