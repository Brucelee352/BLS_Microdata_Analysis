FROM python:3.12-slim

WORKDIR /workspace

COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv && uv sync --frozen --no-dev

COPY app/ ./app/
COPY marketplace.duckdb ./
COPY marketplace_pipeline/target/ ./dbt_docs/

ENV PORT=8080

CMD exec uv run gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 0 app.app:server