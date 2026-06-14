FROM python:3.12-slim

WORKDIR /workspace

COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv && uv sync --frozen --no-dev && uv pip install gunicorn

COPY scripts/ ./scripts/
COPY duckdb/bls_cps.duckdb ./duckdb/

ENV PORT=8080

CMD exec uv run gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 0 scripts.dashboard:server