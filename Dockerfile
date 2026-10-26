# DERVA gateway image: LiteLLM proxy with the DERVA hooks, plus the derva CLI.
FROM python:3.12-slim

# uv installs exactly what uv.lock says, the same lock used on laptops and in CI.
COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    DERVA_HOME=/app

WORKDIR /app

# Dependencies first, so code changes don't reinstall them. No dev tools.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project \
      --extra gateway --extra presidio --extra postgres --extra gcp

# The large English model masks names more accurately than the small one.
ARG SPACY_MODEL=en_core_web_lg
ARG SPACY_MODEL_VERSION=3.8.0
RUN uv pip install --python /opt/venv/bin/python \
      "https://github.com/explosion/spacy-models/releases/download/${SPACY_MODEL}-${SPACY_MODEL_VERSION}/${SPACY_MODEL}-${SPACY_MODEL_VERSION}-py3-none-any.whl"

COPY src ./src
RUN uv sync --frozen --no-dev \
      --extra gateway --extra presidio --extra postgres --extra gcp

COPY config ./config
COPY data/fixtures ./data/fixtures
COPY data/golden ./data/golden
COPY evals ./evals
COPY sql ./sql

RUN useradd --create-home --uid 10001 derva
USER derva

# Presidio's email check uses tldextract, which downloads the public suffix list
# on first use, with no timeout by default. Fetch it now, into the image, so the
# running gateway never reaches out for it; and bound any later fetch.
RUN python -c "import tldextract; tldextract.extract('example.com')"

# DERVA_WARM_START loads the masker's language model while the proxy starts, so
# the health check passes only once the gateway can answer without delay.
ENV DERVA_MASKER=presidio \
    DERVA_SPACY_MODEL=${SPACY_MODEL} \
    DERVA_LITELLM_CONFIG=config/litellm.yaml \
    DERVA_WARM_START=true \
    TLDEXTRACT_CACHE_TIMEOUT=5 \
    PORT=4000
EXPOSE 4000

HEALTHCHECK --interval=15s --timeout=3s --start-period=180s \
  CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health/liveliness' % os.environ['PORT'])"

# Cloud Run sets PORT; the same image runs `derva mock-server` in compose.
CMD ["sh", "-c", "exec litellm --config \"$DERVA_LITELLM_CONFIG\" --host 0.0.0.0 --port \"$PORT\""]
