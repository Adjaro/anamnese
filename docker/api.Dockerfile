# Service api : FastAPI sur 8000, lit le warehouse en lecture seule.
# Runtime seul (pas de dbt, pas d'outils de dev) ; aucun secret dans l'image :
# MISTRAL_API_KEY arrive a l'execution par env_file (CLAUDE.md §8).
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.17 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project
COPY src src
RUN uv sync --locked --no-dev

USER 1000:1000
EXPOSE 8000
CMD ["uvicorn", "anamnese.api:app", "--host", "0.0.0.0", "--port", "8000"]
