
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=8080

# FFmpeg এবং Node.js ইনস্টল
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ffmpeg \
    nodejs \
    ca-certificates && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python dependencies
COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Project files
COPY . .

EXPOSE 8080

CMD ["sh", "-c", "waitress-serve --listen=0.0.0.0:${PORT:-8080} --threads=4 main:app"]
