# KLDataViz Studio — image de la version en ligne (KLDV_MODE=cloud)
# Construite depuis la RACINE du dépôt (render.yaml : dockerContext « . ») pour embarquer aussi l'agent
# AGT-11 (Tableau de bord) de HERMES_DA/hermes_da. Ce qui entre dans l'image : voir /.dockerignore (liste blanche).
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 KLDV_MODE=cloud KLDV_DATA_DIR=/data PORT=10000
WORKDIR /app

# Polices du rapport PDF : Arial Narrow n'est pas libre de diffusion ; on installe ses équivalents condensés libres
# (Liberation Sans Narrow si le paquet la fournit, DejaVu Sans Condensed sinon). Voir pdf_report.FONT_SETS.
RUN apt-get update && apt-get install -y --no-install-recommends fonts-liberation fonts-dejavu-extra \
    && rm -rf /var/lib/apt/lists/*

COPY KLDataViz_console/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Code seulement : aucune donnée, aucun secret (voir /.dockerignore)
COPY KLDataViz_console/ /app/
# Agents HERMES_DA (paquet Python seul) : server.py les cherche dans ../HERMES_DA, soit /HERMES_DA
COPY HERMES_DA/hermes_da/ /HERMES_DA/hermes_da/

# Utilisateur sans privilèges ; /data est le disque persistant monté par Render
RUN useradd --create-home --uid 10001 kldv && mkdir -p /data && chown -R kldv /data
USER kldv

EXPOSE 10000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request,os;urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/healthz',timeout=4)"
CMD ["python", "server.py"]
