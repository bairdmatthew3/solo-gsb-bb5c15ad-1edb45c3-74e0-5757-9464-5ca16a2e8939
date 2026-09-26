FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Dependencies are installed at image build time.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# The SQLite database lives in /data (mounted as a volume by Compose);
# it is created and initialised automatically on first startup.
RUN useradd --system --uid 10001 appuser \
    && mkdir -p /data \
    && chown appuser:appuser /data
USER appuser

ENV POOL_PARENT="10.0.0.0/8" \
    POOL_RESERVED="" \
    DB_PATH="/data/pool.db"

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
