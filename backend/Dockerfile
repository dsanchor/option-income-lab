# Stock Options Manager
# Python 3.12

FROM python:3.12-slim AS base

WORKDIR /app

# Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Chromium + OS-level deps via Playwright (needed for TradingView fallback)
RUN playwright install chromium --with-deps

# Application source + config + scripts
COPY config.yaml run.py run_web.py ./
COPY src/ src/
COPY web/ web/
COPY scripts/ scripts/

EXPOSE 8000

ENTRYPOINT ["python", "run.py"]
