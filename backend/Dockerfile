FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml .
COPY uv.lock .

RUN pip install uv && uv sync --frozen --no-dev

COPY src/ ./src/

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
