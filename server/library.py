"""Lógica compartida de la biblioteca: ingesta con FUSIÓN de modalidades.

Cada canción existe UNA sola vez en la lista aunque llegue en dos archivos
(MP3/M4A/FLAC en la carpeta de audio y MP4 en la de video). La coincidencia
se hace por título+artista (insensible a mayúsculas):

- Llega un VIDEO y ya existe el tema solo-audio  -> se ADJUNTA el video
  (se conserva el audio original, no se sobreescribe).
- Llega un AUDIO original y el tema existe con audio extraído del video
  -> se REEMPLAZA el audio extraído por el original (mejor calidad).
- Misma modalidad ya presente -> duplicado (no se inserta nada).

La usan el endpoint de subida del panel admin y el importador CLI para que
ambos caminos se comporten idéntico.
"""
import logging
from pathlib import Path

from .database import execute, q1
from .media_processing import (
    duration_of, extract_audio, extract_cover_from_video, has_video_stream,
    rel_media, safe_media_path, sha256_of, store_upload,
)

log = logging.getLogger("portal.library")

UNKNOWN_ARTIST = "Desconocido"

# Resultados posibles de la ingesta
CREATED = "creado"
VIDEO_ATTACHED = "video añadido a tema existente"
AUDIO_UPGRADED = "audio original reemplazó al extraído"
DUP_FILE = "duplicado (archivo idéntico)"
DUP_MODALITY = "duplicado (el tema ya tiene esa modalidad)"


def get_or_create_artist(name: str) -> int:
    name = name.strip()[:120] or UNKNOWN_ARTIST
    row = q1("SELECT id FROM artists WHERE name = ?", (name,))
    return row["id"] if row else execute("INSERT INTO artists (name) VALUES (?)", (name,))


def get_or_create_album(title: str, artist_id: int, year: int | None) -> int | None:
    title = (title or "").strip()[:160]
    if not title:
        return None
    row = q1("SELECT id FROM albums WHERE title = ? AND artist_id = ?", (title, artist_id))
    return row["id"] if row else execute(
        "INSERT INTO albums (title, artist_id, year) VALUES (?,?,?)", (title, artist_id, year))


def _find_song(title: str, artist_id: int, artist_name: str):
    """Busca el tema por título+artista; si el artista es desconocido (archivos
    sin etiquetas, típico en videos), intenta por título solamente."""
    row = q1("SELECT * FROM tracks WHERE title = ? COLLATE NOCASE AND artist_id = ?",
             (title.strip(), artist_id))
    if row:
        return row
    if artist_name.strip().lower() == UNKNOWN_ARTIST.lower():
        return q1("SELECT * FROM tracks WHERE title = ? COLLATE NOCASE LIMIT 1", (title.strip(),))
    # Caso inverso: el tema existente quedó con artista desconocido y ahora llega etiquetado
    unknown = q1("SELECT id FROM artists WHERE name = ?", (UNKNOWN_ARTIST,))
    if unknown:
        return q1("SELECT * FROM tracks WHERE title = ? COLLATE NOCASE AND artist_id = ?",
                  (title.strip(), unknown["id"]))
    return None


def _fill_missing_metadata(track, artist_id: int, album: str, year: int | None,
                           genre_id: int | None, incoming_has_artist: bool) -> None:
    """Completa metadatos vacíos del tema existente sin pisar los ya asignados."""
    updates, params = [], []
    if incoming_has_artist and track["artist_id"] != artist_id:
        existing_artist = q1("SELECT name FROM artists WHERE id = ?", (track["artist_id"],))
        if existing_artist and existing_artist["name"] == UNKNOWN_ARTIST:
            updates.append("artist_id = ?"); params.append(artist_id)
    if not track["album_id"] and album:
        updates.append("album_id = ?"); params.append(get_or_create_album(album, artist_id, year))
    if not track["year"] and year:
        updates.append("year = ?"); params.append(year)
    if not track["genre_id"] and genre_id:
        updates.append("genre_id = ?"); params.append(genre_id)
    if updates:
        params.append(track["id"])
        execute(f"UPDATE tracks SET {', '.join(updates)} WHERE id = ?", tuple(params))  # columnas fijas, valores parametrizados


def ingest(tmp: Path, ext: str, info: dict, *, title: str, artist: str,
           album: str = "", year: int | None = None, genre_id: int | None = None,
           cover_tmp: Path | None = None, cover_ext: str | None = None) -> tuple[int | None, str]:
    """Ingesta un archivo validado (magic bytes + ffprobe ya verificados).
    Mueve `tmp` a su destino solo en caso de éxito. Devuelve (track_id, resultado)."""
    title = title.strip()[:160]
    artist = artist.strip()[:120] or UNKNOWN_ARTIST
    is_video = ext == ".mp4" and has_video_stream(info)
    file_hash = sha256_of(tmp)

    # 1) Dedup por contenido exacto (por modalidad)
    col = "video_hash_sha256" if is_video else "file_hash_sha256"
    if q1(f"SELECT id FROM tracks WHERE {col} = ?", (file_hash,)):  # col es constante interna
        return None, DUP_FILE

    artist_id = get_or_create_artist(artist)
    existing = _find_song(title, artist_id, artist)
    duration = duration_of(info)

    # 2) FUSIÓN: el tema ya existe -> adjuntar la modalidad que falta
    if existing is not None:
        incoming_has_artist = artist.lower() != UNKNOWN_ARTIST.lower()
        if is_video:
            if existing["video_path"]:
                return existing["id"], DUP_MODALITY
            final_video = store_upload(tmp, ext, "video")
            sets = ["video_path = ?", "video_hash_sha256 = ?"]
            params: list = [rel_media(final_video), file_hash]
            if not existing["cover_path"]:
                auto = extract_cover_from_video(final_video)
                if auto:
                    sets.append("cover_path = ?"); params.append(rel_media(auto))
            if duration > (existing["duration_seconds"] or 0):
                sets.append("duration_seconds = ?"); params.append(duration)
            params.append(existing["id"])
            execute(f"UPDATE tracks SET {', '.join(sets)} WHERE id = ?", tuple(params))
            _fill_missing_metadata(existing, artist_id, album, year, genre_id, incoming_has_artist)
            log.info("Video adjuntado al tema id=%s (%s)", existing["id"], title)
            return existing["id"], VIDEO_ATTACHED
        # Llega audio
        if not existing["audio_extracted"]:
            return existing["id"], DUP_MODALITY
        final_audio = store_upload(tmp, ext, "audio")
        try:
            old = safe_media_path(existing["audio_path"])
            old.unlink(missing_ok=True)
        except ValueError:
            log.error("Ruta insegura al reemplazar audio del track=%s", existing["id"])
        execute(
            "UPDATE tracks SET audio_path = ?, file_hash_sha256 = ?, audio_extracted = 0 WHERE id = ?",
            (rel_media(final_audio), file_hash, existing["id"]),
        )
        _fill_missing_metadata(existing, artist_id, album, year, genre_id, incoming_has_artist)
        log.info("Audio original reemplazó al extraído en track id=%s (%s)", existing["id"], title)
        return existing["id"], AUDIO_UPGRADED

    # 3) Tema nuevo
    video_path = audio_path = cover_path = None
    video_hash = None
    audio_extracted = 0
    if is_video:
        final_video = store_upload(tmp, ext, "video")
        video_path, video_hash = rel_media(final_video), file_hash
        audio_file = extract_audio(final_video)
        if audio_file is None:
            final_video.unlink(missing_ok=True)
            return None, "rechazado (no se pudo extraer el audio del video)"
        audio_path = rel_media(audio_file)
        audio_extracted = 1
        auto = extract_cover_from_video(final_video)
        if auto:
            cover_path = rel_media(auto)
        file_hash = None  # el audio no proviene de un archivo original
    else:
        audio_path = rel_media(store_upload(tmp, ext, "audio"))

    if cover_tmp is not None and cover_ext:
        cover_path = rel_media(store_upload(cover_tmp, cover_ext, "cover"))

    album_id = get_or_create_album(album, artist_id, year)
    tid = execute(
        """INSERT INTO tracks (title, artist_id, album_id, genre_id, year, duration_seconds,
           video_path, audio_path, cover_path, file_hash_sha256, video_hash_sha256, audio_extracted)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (title, artist_id, album_id, genre_id, year, duration,
         video_path, audio_path, cover_path, file_hash, video_hash, audio_extracted),
    )
    return tid, CREATED
