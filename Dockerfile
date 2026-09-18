FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_LINK_MODE=copy PATH="/app/.venv/bin:$PATH"
WORKDIR /app
RUN pip install --no-cache-dir uv==0.12.3 && useradd --create-home --uid 10001 relay
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend ./backend
COPY scripts ./scripts
COPY migrations ./migrations
COPY alembic.ini ./
ENV EMBEDDING_MODEL_DIR=/app/models/bge-small-en-v1.5
RUN python -m scripts.prepare_embeddings
RUN mkdir /data && chown -R relay:relay /data /app
USER relay
EXPOSE 8000
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
