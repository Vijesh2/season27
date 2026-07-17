FROM node:22-bookworm-slim AS frontend
WORKDIR /build
COPY package.json package-lock.json tsconfig.json ./
COPY frontend ./frontend
RUN npm ci && mkdir -p app/static && npm run build

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:$PATH"
WORKDIR /app
RUN pip install --no-cache-dir uv
COPY pyproject.toml uv.lock README.md alembic.ini ./
RUN uv sync --frozen --no-dev
COPY app ./app
COPY scripts ./scripts
COPY migrations ./migrations
COPY --from=frontend /build/app/static/app.js ./app/static/app.js
EXPOSE 5001
CMD ["uv", "run", "--no-sync", "season27-start"]
