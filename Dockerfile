FROM python:3.11-slim
COPY --from=ghcr.io/astral-sh/uv:0.12.24 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-cache
COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./
COPY certs ./certs
RUN useradd --uid 10001 --create-home portfolio
USER portfolio
ENV PATH="/app/.venv/bin:$PATH"
ENV TINVEST_CA_FILE="/app/certs/tinvest-ca.pem"
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1"]
