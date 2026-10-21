FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir . \
 && python -c "from vastclaw_ui import models; print('vastclaw_ui import ok')" \
 && python -c "import vastclaw_ui.app; print('vastclaw_ui.app import ok')"

# Node 24, openclaw@2026.7.35 (JSONL sessions), iproute2 for the OpenShell sandbox network.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates iproute2 \
 && curl -fsSL https://deb.nodesource.com/setup_24.x | bash - \
 && apt-get install -y --no-install-recommends nodejs \
 && npm install -g openclaw@2026.7.35 \
 && apt-get clean && rm -rf /var/lib/apt/lists/*
# Fail the build LOUDLY if the CLI didn't land (instead of degrading at runtime).
RUN node -v && command -v openclaw && openclaw --version && command -v ip

RUN useradd --create-home --uid 10002 vastclaw
ENV OPENCLAW_STATE_DIR=/workspace/.openclaw
USER vastclaw

EXPOSE 8080

CMD ["uvicorn", "vastclaw_ui.app:build_default_app", "--factory", "--host", "0.0.0.0", "--port", "8080"]
