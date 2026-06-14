FROM python:3.12-slim

# System deps: Node/npm for Playwright CLI, curl for healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
    nodejs \
    npm \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (Docker layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browsers (chromium only — keeps image lean)
COPY package.json package-lock.json* ./
RUN npm ci --omit=dev 2>/dev/null || npm install --omit=dev
RUN npx playwright install chromium --with-deps

# Copy application source
COPY . .

# Ensure generated scripts and upload directories exist at runtime
RUN mkdir -p tests/generated uploads state

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
