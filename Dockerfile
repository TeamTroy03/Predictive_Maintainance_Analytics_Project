# One image serves the React site and the FastAPI + LangChain Core service (§6.7), same origin.
# Built as the CI artifact (§6.8).

# --- 1. frontend: Node is only needed to build, it never reaches the final image
FROM node:22-slim AS frontend
WORKDIR /web
COPY src/app/frontend/package.json src/app/frontend/package-lock.json ./
RUN npm ci
COPY src/app/frontend/ ./
ENV VITE_USE_MOCK=false
RUN npm run build

# --- 2. backend deps: build the venv here so the final image carries no build cache
FROM python:3.14-slim AS backend
ENV PIP_NO_CACHE_DIR=1
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
WORKDIR /srv
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install .

# --- 3. runtime
FROM python:3.14-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    FRONTEND_DIST=/srv/frontend \
    AGENT_LOG_DIR=/tmp/agent-log

COPY --from=backend /opt/venv /opt/venv
COPY --from=frontend /web/dist /srv/frontend

RUN useradd --create-home --uid 10001 appuser
USER appuser

EXPOSE 8000
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]