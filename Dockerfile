FROM python:3.12-slim

WORKDIR /app

# Install system dependencies needed by some packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first (cached layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create output directory (ephemeral — files survive for the instance lifetime)
RUN mkdir -p /tmp/output

# Cloud Run sets PORT env var; default to 8080
ENV PORT=8080
ENV OUTPUT_DIR=/tmp/output

EXPOSE 8080

CMD ["sh", "-c", "uvicorn web_app:app --host 0.0.0.0 --port ${PORT} --ws-ping-interval 15 --ws-ping-timeout 20 --timeout-keep-alive 300"]
