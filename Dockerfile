FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download a small Whisper model at build time (optional; speeds first run)
RUN python -c "import whisper; whisper.load_model('base')" || true

COPY . .

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

EXPOSE 8000

VOLUME ["/app/projects"]

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
