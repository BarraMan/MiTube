# MiTube — imagen de producción / production image
FROM python:3.12-slim

# FFmpeg fijado desde la imagen: elimina el "en mi máquina sí funciona"
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server ./server
COPY static ./static
COPY seed.py import_media.py make_samples.py ./

# Usuario sin privilegios + directorios de datos
RUN useradd -r -m mitube \
    && mkdir -p /data media/video media/audio media/covers \
    && chown -R mitube:mitube /app /data

USER mitube

ENV PYTHONUNBUFFERED=1 \
    MITUBE_DB=/data/portal.db

EXPOSE 9000

# Crea el admin si no existe (idempotente) y arranca el servidor
CMD ["sh", "-c", "python seed.py && python -m uvicorn server.main:app --host 0.0.0.0 --port 9000"]
