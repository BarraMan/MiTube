"""Streaming con HTTP Range Requests (206/416), descargas con Content-Disposition y portadas.
Los endpoints reciben solo IDs; las rutas físicas provienen de la BD y se re-validan
contra el directorio media/ (defensa redundante anti path-traversal)."""
import logging
import re
import unicodedata
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from ..database import q1
from ..media_processing import safe_media_path
from ..security import current_user

log = logging.getLogger("portal.stream")
router = APIRouter(prefix="/api", tags=["stream"])

CHUNK = 128 * 1024
MIME = {".mp4": "video/mp4", ".m4a": "audio/mp4", ".mp3": "audio/mpeg",
        ".flac": "audio/flac", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".webp": "image/webp"}

RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


def _resolve_track_file(track_id: int, kind: str) -> tuple[Path, dict]:
    if kind not in ("audio", "video"):
        raise HTTPException(400, "Tipo de medio inválido.")
    row = q1("SELECT * FROM tracks WHERE id = ?", (track_id,))
    if row is None:
        raise HTTPException(404, "Tema no encontrado.")
    rel = row["audio_path"] if kind == "audio" else row["video_path"]
    if not rel:
        raise HTTPException(404, "Este tema no tiene esa rendition.")
    try:
        path = safe_media_path(rel)
    except ValueError:
        log.error("Ruta insegura en BD para track=%s", track_id)
        raise HTTPException(404, "Archivo no disponible.")
    if not path.is_file():
        log.error("Archivo faltante en disco: %s", path)
        raise HTTPException(404, "Archivo no disponible.")
    return path, dict(row)


def _file_iter(path: Path, start: int, end: int):
    """Genera chunks del rango [start, end] sin cargar el archivo en memoria."""
    with open(path, "rb") as f:
        f.seek(start)
        remaining = end - start + 1
        while remaining > 0:
            data = f.read(min(CHUNK, remaining))
            if not data:
                break
            remaining -= len(data)
            yield data


@router.get("/stream/{track_id}/{kind}")
def stream(track_id: int, kind: str, request: Request, user=Depends(current_user)):
    path, _ = _resolve_track_file(track_id, kind)
    size = path.stat().st_size
    ctype = MIME.get(path.suffix.lower(), "application/octet-stream")
    base_headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "private, max-age=3600",
        "X-Content-Type-Options": "nosniff",
    }
    range_header = request.headers.get("range")
    if not range_header:
        return StreamingResponse(_file_iter(path, 0, size - 1), media_type=ctype,
                                 headers={**base_headers, "Content-Length": str(size)})
    m = RANGE_RE.match(range_header.strip())
    if not m or (m.group(1) == "" and m.group(2) == ""):
        raise HTTPException(416, "Rango malformado.", headers={"Content-Range": f"bytes */{size}"})
    if m.group(1) == "":  # sufijo: bytes=-N (últimos N bytes)
        length = int(m.group(2))
        if length == 0:
            raise HTTPException(416, "Rango inválido.", headers={"Content-Range": f"bytes */{size}"})
        start, end = max(0, size - length), size - 1
    else:
        start = int(m.group(1))
        end = int(m.group(2)) if m.group(2) else size - 1
    if start >= size or end >= size or start > end:
        raise HTTPException(416, "Rango fuera de límites.", headers={"Content-Range": f"bytes */{size}"})
    headers = {
        **base_headers,
        "Content-Range": f"bytes {start}-{end}/{size}",
        "Content-Length": str(end - start + 1),
    }
    return StreamingResponse(_file_iter(path, start, end), status_code=206,
                             media_type=ctype, headers=headers)


def _safe_filename(name: str, ext: str) -> tuple[str, str]:
    """Nombre saneado (sin control chars ni separadores) + versión ASCII de respaldo."""
    name = "".join(c for c in name if c.isprintable() and c not in '/\\:*?"<>|')
    name = name.strip().strip(".")[:120] or "tema"
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode() or "tema"
    return f"{name}{ext}", f"{ascii_name}{ext}"


@router.get("/download/{track_id}/{kind}")
def download(track_id: int, kind: str, user=Depends(current_user)):
    path, row = _resolve_track_file(track_id, kind)
    artist = q1("SELECT name FROM artists WHERE id = ?", (row["artist_id"],))
    base = f"{artist['name'] if artist else 'Desconocido'} - {row['title']}"
    fname, ascii_fname = _safe_filename(base, path.suffix)
    disposition = f"attachment; filename=\"{ascii_fname}\"; filename*=UTF-8''{quote(fname)}"
    size = path.stat().st_size
    return StreamingResponse(
        _file_iter(path, 0, size - 1),
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": disposition,
            "Content-Length": str(size),
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/cover/{track_id}")
def cover(track_id: int, user=Depends(current_user)):
    row = q1("SELECT cover_path FROM tracks WHERE id = ?", (track_id,))
    if row is None or not row["cover_path"]:
        raise HTTPException(404, "Sin portada.")
    try:
        path = safe_media_path(row["cover_path"])
    except ValueError:
        raise HTTPException(404, "Sin portada.")
    if not path.is_file():
        raise HTTPException(404, "Sin portada.")
    return Response(path.read_bytes(), media_type=MIME.get(path.suffix.lower(), "image/jpeg"),
                    headers={"Cache-Control": "private, max-age=86400"})
