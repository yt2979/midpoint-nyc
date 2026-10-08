FROM ghcr.io/astral-sh/uv:0.11.21 AS uv
FROM python:3.13-slim
COPY --from=uv /uv /uvx /bin/
WORKDIR /app
ENV PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY app.py agent.py tools.py fairness.py maps_client.py meeting_candidates.py index.html ./
COPY static ./static
ENV PATH="/app/.venv/bin:$PATH" PORT=8080
CMD ["sh", "-c", "exec uvicorn app:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1"]
