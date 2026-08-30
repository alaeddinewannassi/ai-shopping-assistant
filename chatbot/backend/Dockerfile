FROM python:3.11-slim

# Build context is the repo root (docker/docker-compose.yml) so this can also install the
# shared tenancy-db package (specs/002-backoffice-analytics) — this service only ever reads
# tenant config through it, never writes.
WORKDIR /app

COPY tenancy-db ./tenancy-db
RUN pip install --no-cache-dir ./tenancy-db

COPY chatbot/backend/pyproject.toml ./backend/pyproject.toml
COPY chatbot/backend/src ./backend/src
RUN pip install --no-cache-dir ./backend

WORKDIR /app/backend
EXPOSE 8000

CMD ["uvicorn", "src.api.chat:app", "--host", "0.0.0.0", "--port", "8000"]
