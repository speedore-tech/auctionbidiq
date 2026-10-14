# auctionbidiq — app image (FastAPI API + static web UI in one container)
# Build context is the repo root (see docker-compose.prod.yml).
FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

COPY requirements.txt requirements-postgres.txt ./
RUN pip install --no-cache-dir -r requirements-postgres.txt

COPY bidiq ./bidiq
COPY web ./web
COPY scripts ./scripts

EXPOSE 8000

# Tables are created automatically on startup (idempotent).
CMD ["uvicorn", "bidiq.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
