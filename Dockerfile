# DERVA gateway image: LiteLLM proxy with the DERVA hooks, plus the derva CLI.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DERVA_HOME=/app

WORKDIR /app

# Locked dependencies first, so code changes don't reinstall them.
COPY requirements.lock .
RUN pip install -r requirements.lock

# The large English model masks names more accurately than the small one.
ARG SPACY_MODEL=en_core_web_lg
ARG SPACY_MODEL_VERSION=3.8.0
RUN pip install "https://github.com/explosion/spacy-models/releases/download/${SPACY_MODEL}-${SPACY_MODEL_VERSION}/${SPACY_MODEL}-${SPACY_MODEL_VERSION}-py3-none-any.whl"

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-deps .

COPY config ./config
COPY data/fixtures ./data/fixtures
COPY data/golden ./data/golden
COPY evals ./evals
COPY sql ./sql

RUN useradd --create-home --uid 10001 derva
USER derva

ENV DERVA_MASKER=presidio \
    DERVA_SPACY_MODEL=${SPACY_MODEL} \
    DERVA_LITELLM_CONFIG=config/litellm.yaml \
    PORT=4000
EXPOSE 4000

HEALTHCHECK --interval=15s --timeout=3s --start-period=40s \
  CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health/liveliness' % os.environ['PORT'])"

# Cloud Run sets PORT; the same image runs `derva mock-server` in compose.
CMD ["sh", "-c", "exec litellm --config \"$DERVA_LITELLM_CONFIG\" --host 0.0.0.0 --port \"$PORT\""]
