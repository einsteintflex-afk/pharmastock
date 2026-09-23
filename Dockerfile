# PharmaStock application image.
#   docker build -t pharmastock .
# Configuration comes only from environment variables (see .env.example);
# no secrets are baked into the image.

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PHARMASTOCK_ENV=production \
    LOG_FORMAT=json

WORKDIR /srv/pharmastock
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY backend ./backend
COPY frontend ./frontend
COPY deploy/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh \
    && useradd --system --uid 10001 --home /srv/pharmastock pharmastock \
    && chown -R pharmastock /srv/pharmastock
USER pharmastock

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(urllib.request.urlopen('http://localhost:8000/health', timeout=4).status != 200)"

ENTRYPOINT ["docker-entrypoint.sh"]
# Behind a reverse proxy: trust X-Forwarded-* only from the proxy network.
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*", "--workers", "2"]
