FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=6001 \
    DB_PATH=/app/data/gamesense.db

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/data /app/logs

EXPOSE 6001

VOLUME ["/app/data"]

CMD ["gunicorn", "--bind", "0.0.0.0:6001", "--workers", "2", "--threads", "4", "server:app"]
