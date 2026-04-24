FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    gifsicle \
    libjpeg-turbo-progs \
    libjpeg62-turbo-dev \
    libwebp-dev \
    pngquant \
    webp \
    zlib1g-dev \
    zopfli \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN mkdir -p /app/data/uploads /app/data/output /app/data/tmp /app/data/evaluation-images /app/data/evaluation-compressed
EXPOSE 8000
