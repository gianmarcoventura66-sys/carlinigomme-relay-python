FROM python:3.12-slim

# curl_cffi ha dipendenze native — libcurl è inclusa nel wheel ma servono questi
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

EXPOSE 8080
ENV PORT=8080

# Health check integrato (Koyeb lo usa per sapere se il container è pronto)
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
  CMD curl -f http://localhost:${PORT}/health || exit 1

CMD ["gunicorn", "main:app", \
     "--bind", "0.0.0.0:8080", \
     "--workers", "1", \
     "--timeout", "60", \
     "--access-logfile", "-"]
