#!/usr/bin/env bash
# Importa tu biblioteca completa al portal (ajusta las rutas si cambian).
# Uso: bash import_all.sh
set -euo pipefail
cd "$(dirname "$0")"

AUDIO_DIR="$HOME/inventario/multimedia/audio"
VIDEO_DIR="$HOME/inventario/multimedia/video"

# VIDEOS PRIMERO: cada tema nace con su video y el portal extrae el audio.
# Los audios de la segunda carpeta se FUSIONAN con el tema existente
# (mismo título/artista) en lugar de duplicarse en la lista.
if [ -d "$VIDEO_DIR" ]; then
  echo "== Importando video desde $VIDEO_DIR"
  python import_media.py "$VIDEO_DIR"
else
  echo "No existe $VIDEO_DIR (omitido)"
fi

if [ -d "$AUDIO_DIR" ]; then
  echo "== Importando audio desde $AUDIO_DIR (se fusiona, no duplica)"
  python import_media.py "$AUDIO_DIR"
else
  echo "No existe $AUDIO_DIR (omitido)"
fi

echo "Listo. Cada canción aparece una sola vez; el switch Audio/Video decide la modalidad."
