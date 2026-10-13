# KLDataViz Studio — image de la version en ligne (KLDV_MODE=cloud)
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 KLDV_MODE=cloud KLDV_DATA_DIR=/data PORT=10000
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Code seulement : aucune donnée, aucun secret (voir .dockerignore)
COPY . .

# Utilisateur sans privilèges ; /data est le disque persistant monté par Render
RUN useradd --create-home --uid 10001 kldv && mkdir -p /data && chown -R kldv /data
USER kldv

EXPOSE 10000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request,os;urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/healthz',timeout=4)"
CMD ["python", "server.py"]
