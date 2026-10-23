FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ZQC_HOST=0.0.0.0 \
    ZQC_DATA_DIR=/data

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY docs ./docs
COPY README.md LICENSE render.yaml .env.example ./
RUN mkdir -p /data/inbox /data/outputs /data/templates /data/reference /data/logs /data/config /data/state /data/trash

EXPOSE 8848
CMD ["sh", "-c", "exec gunicorn app.web_app:app --bind ${ZQC_HOST:-0.0.0.0}:${PORT:-8848} --workers 1 --threads 4 --timeout 300"]
