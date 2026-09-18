# Service dbt : one-shot, reconstruit le warehouse puis se termine (CLAUDE.md §8).
# Seul le groupe uv `dbt` est installe : ni runtime de l'API, ni outils de dev.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.17 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    DBT_SEND_ANONYMOUS_USAGE_STATS=false \
    DBT_LOG_PATH=/tmp/dbt-logs

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --only-group dbt --no-install-project

COPY scripts/swap_warehouse.py scripts/
COPY transform transform

# dbt ecrit son etat (.user.yml, partial_parse) a cote du projet : dossier ouvert
# a l'utilisateur non root impose par docker-compose.yml.
RUN chmod -R a+rwX /app/transform

CMD ["python", "scripts/swap_warehouse.py"]
