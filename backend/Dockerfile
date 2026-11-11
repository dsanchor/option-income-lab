# Stock Options Manager
# Python 3.12

FROM python:3.12-slim AS base

WORKDIR /app

# Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Playwright's bundled Node needs libatomic before it can install browser dependencies.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libatomic1 \
    && rm -rf /var/lib/apt/lists/*

# Install Chromium + OS-level deps via Playwright (needed for TradingView fallback)
RUN playwright install chromium --with-deps

# Application source + config + scripts
COPY config.yaml run.py run_web.py run_mcp.py ./
COPY src/ src/
COPY web/ web/
COPY scripts/ scripts/

EXPOSE 8000
EXPOSE 8001

ENTRYPOINT ["python", "run.py"]
