FROM python:3.12-slim-bookworm

# Pinned; bump deliberately. Litestream's config format changed between minors.
COPY --from=ghcr.io/astral-sh/uv:0.12.17 /uv /usr/local/bin/uv
COPY --from=litestream/litestream:0.5.17 /usr/local/bin/litestream /usr/local/bin/litestream

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Dependencies first, so code changes don't reinstall them.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY . .
RUN chmod +x deploy/*.sh \
    && SECRET_KEY=collectstatic-only python manage.py collectstatic --noinput

COPY deploy/litestream.yml /etc/litestream.yml

EXPOSE 8000
CMD ["/app/deploy/start.sh"]
