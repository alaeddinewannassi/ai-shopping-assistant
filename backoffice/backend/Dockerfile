FROM python:3.11-slim

# Build context is the repo root (docker/docker-compose.yml) so this can also install the
# shared tenancy-db package (specs/002-backoffice-analytics). This service owns writes to
# it (tenant/adapter/LLM/promo config, widget keys, admin users) — chatbot/backend only reads.
WORKDIR /app

COPY tenancy-db ./tenancy-db
RUN pip install --no-cache-dir ./tenancy-db

COPY backoffice/backend/pyproject.toml ./backend/pyproject.toml
COPY backoffice/backend/src ./backend/src
COPY backoffice/backend/scripts ./backend/scripts
RUN pip install --no-cache-dir ./backend

WORKDIR /app/backend
EXPOSE 8001

CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8001"]
