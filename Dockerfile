# syntax=docker/dockerfile:1
FROM python:3.11-slim

WORKDIR /app

# 시스템 의존성 (ffmpeg, demucs 실행을 위해 필요할 수 있음)
RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY static/ ./static
COPY templates/ ./templates
# 이것때문에 모든 파일이 다 포함된다

ENV FLASK_APP=app.py
ENV FLASK_ENV=production
# Fly.io 에서는 $PORT 환경변수 제공됨
EXPOSE 3000

CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:3000", "--workers", "2"]
