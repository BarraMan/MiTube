"""Procesamiento multimedia: validación por magic bytes, ffprobe y extracción con FFmpeg.
Todas las invocaciones usan listas de argumentos (nunca shell=True)."""
import hashlib
import json
import logging
import subprocess
import uuid
from pathlib import Path

from .database import MEDIA_DIR

log = logging.getLogger("portal.media")

VIDEO_DIR = MEDIA_DIR / "video"
AUDIO_DIR = MEDIA_DIR / "audio"
COVER_DIR = MEDIA_DIR / "covers"

ALLOWED_MEDIA_EXT = {".mp4", ".mp3", ".m4a", ".flac"}
ALLOWED_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}
MAX_UPLOAD_BYTES = 500 * 1024 * 1024

# Firmas de tipo real (magic bytes)
_MAGIC = [
    (b"ID3", "mp3"),
    (b"\xff\xfb", "mp3"), (b"\xff\xf3", "mp3"), (b"\xff\xf2", "mp3"),
    (b"fLaC", "flac"),
    (b"\x89PNG", "png"),
    (b"\xff\xd8\xff", "jpg"),
    (b"RIFF", "webp"),  # se confirma con 'WEBP' en offset 8
]


def ensure_dirs() -> None:
    for d in (VIDEO_DIR, AUDIO_DIR, COVER_DIR):
        d.mkdir(parents=True, exist_ok=True)


def detect_magic(head: bytes) -> str | None:
    """Detecta el tipo real por firmas binarias. head = primeros 16 bytes."""
    if len(head) >= 12 and head[4:8] == b"ftyp":  # MP4 / M4A (ISO BMFF)
        return "mp4"
    for sig, kind in _MAGIC:
        if head.startswith(sig):
            if kind == "webp" and head[8:12] != b"WEBP":
                continue
            return kind
    return None


def magic_matches_extension(head: bytes, ext: str) -> bool:
    kind = detect_magic(head)
    if kind is None:
        return False
    mapping = {
        ".mp4": {"mp4"}, ".m4a": {"mp4"}, ".mp3": {"mp3"}, ".flac": {"flac"},
        ".jpg": {"jpg"}, ".jpeg": {"jpg"}, ".png": {"png"}, ".webp": {"webp"},
    }
    return kind in mapping.get(ext, set())


def safe_media_path(path_str: str) -> Path:
    """Chequeo redundante anti path-traversal: la ruta debe quedar dentro de media/."""
    p = (MEDIA_DIR / path_str).resolve()
    if not str(p).startswith(str(MEDIA_DIR.resolve()) + "/"):
        raise ValueError("Ruta fuera del directorio de medios")
    return p


def ffprobe_info(path: Path) -> dict | None:
    """Valida el archivo con ffprobe. Devuelve info o None si no es media válido."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", str(path)],
            capture_output=True, timeout=60, check=True,
        )
        info = json.loads(out.stdout)
        if not info.get("streams"):
            return None
        return info
    except Exception:
        log.exception("ffprobe falló para %s", path)
        return None


def has_video_stream(info: dict) -> bool:
    return any(
        s.get("codec_type") == "video" and s.get("disposition", {}).get("attached_pic", 0) != 1
        for s in info.get("streams", [])
    )


def duration_of(info: dict) -> float:
    try:
        return float(info.get("format", {}).get("duration", 0))
    except (TypeError, ValueError):
        return 0.0


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _run_ffmpeg(args: list[str]) -> bool:
    try:
        subprocess.run(["ffmpeg", "-y", "-v", "error", *args],
                       capture_output=True, timeout=600, check=True)
        return True
    except Exception:
        log.exception("ffmpeg falló: %s", " ".join(args[:6]))
        return False


def extract_audio(video_path: Path) -> Path | None:
    """Extrae la rendition de audio (m4a) de un video."""
    out = AUDIO_DIR / f"{uuid.uuid4().hex}.m4a"
    ok = _run_ffmpeg(["-i", str(video_path), "-vn", "-acodec", "aac", "-b:a", "192k", str(out)])
    return out if ok and out.exists() else None


def extract_cover_from_video(video_path: Path, at_second: float = 1.0) -> Path | None:
    out = COVER_DIR / f"{uuid.uuid4().hex}.jpg"
    ok = _run_ffmpeg(["-ss", str(at_second), "-i", str(video_path),
                      "-frames:v", "1", "-vf", "scale=640:-2", str(out)])
    return out if ok and out.exists() else None


def store_upload(tmp_path: Path, ext: str, kind: str) -> Path:
    """Mueve el archivo validado a su directorio final con nombre UUID."""
    target_dir = {"video": VIDEO_DIR, "audio": AUDIO_DIR, "cover": COVER_DIR}[kind]
    final = target_dir / f"{uuid.uuid4().hex}{ext}"
    tmp_path.replace(final)
    return final


def rel_media(path: Path) -> str:
    return str(path.resolve().relative_to(MEDIA_DIR.resolve()))
