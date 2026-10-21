FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ADBC_DRIVER_PATH=/opt/adbc-driver/libadbc_driver_vastdb.so

ARG ADBC_DRIVER_URL="https://artifactory.vastdata.com/files/vastdb-native-client/1955131/libadbc_driver_vastdb.so"

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /opt/adbc-driver \
    && curl -fsSL -o "${ADBC_DRIVER_PATH}" "${ADBC_DRIVER_URL}"

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

RUN python -c "from mcp.server.fastmcp import FastMCP; print('mcp.server.fastmcp OK')" \
    || python -c "from fastmcp import FastMCP; print('fastmcp OK')"

RUN useradd --create-home --uid 10001 vastclaw
USER vastclaw

ENTRYPOINT ["vastclaw-mcp"]
