# One-shot migration runner (docker-compose.yml's `migrate` service): the two application
# images (chatbot/backend/Dockerfile, backoffice/backend/Dockerfile) only ever COPY
# `src/tenancy_db` via `pip install ./tenancy-db` — they never carry migrations/alembic.ini,
# and neither runs `alembic upgrade head` itself on startup (an app process racing its own
# schema migration on every boot is its own footgun). This image is the schema step: it
# depends on nothing but Postgres, applies pending migrations, and exits.
FROM python:3.11-slim
WORKDIR /app/tenancy-db
COPY tenancy-db/pyproject.toml ./
COPY tenancy-db/src ./src
RUN pip install --no-cache-dir -e .
COPY tenancy-db/alembic.ini ./
COPY tenancy-db/migrations ./migrations
CMD ["alembic", "upgrade", "head"]
