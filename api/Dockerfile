# Rare Disease Atlas API — deploy image (Render: render.yaml uses
# dockerContext "." and dockerfilePath "api/Dockerfile").
#
# The image must run even without data/build: api/fixtures/ ships inside
# api/, so the loader chain falls back to the dev fixture (mode: "mock").
# Point ATLAS_GRAPH_PATH at a mounted real graph, or uncomment the COPY
# below once data/build/graph.json exists in the build context.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY api/requirements.txt ./api/requirements.txt
RUN pip install --no-cache-dir -r api/requirements.txt

# api/ includes the fixtures (api/fixtures/*.mock.json)
COPY api/ ./api/

# Optional real build output (uncomment once data/build exists in the context):
# COPY data/build/ ./data/build/

EXPOSE 8000
CMD ["sh", "-c", "uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
