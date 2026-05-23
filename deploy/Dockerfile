FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libreoffice-writer \
    p7zip-full \
    && rm -rf /var/lib/apt/lists/*

ENV TENDER_REPO_ROOT=/app
ENV PYTHONPATH=/app/apps/portal

COPY apps/portal/requirements.txt /tmp/requirements-portal.txt
RUN pip install --no-cache-dir -r /tmp/requirements-portal.txt

COPY apps/portal/seace_monitor ./apps/portal/seace_monitor
COPY scripts ./scripts
COPY instrucciones ./instrucciones
COPY config.example.yaml ./

ENV PYTHONUNBUFFERED=1

VOLUME ["/app/data"]

EXPOSE 8080

CMD ["python", "-m", "seace_monitor", "web", "-c", "/app/config.yaml", "--host", "0.0.0.0", "--port", "8080"]
