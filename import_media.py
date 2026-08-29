"""Importador masivo de medios locales con FUSIÓN de modalidades.
Uso: python import_media.py /ruta/a/carpeta [--genre "Rock"]

Cada canción aparece UNA sola vez en la biblioteca aunque exista en dos
archivos (video MP4 y audio extraído): la coincidencia por título+artista
adjunta el video al tema existente o reemplaza el audio extraído por el
original. Pipeline idéntico a la subida web: extensión permitida, magic
bytes, ffprobe, hash anti-duplicado, extracción de audio y portada."""
import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from server.database import execute, init_db, q1  # noqa: E402
from server.library import UNKNOWN_ARTIST, ingest  # noqa: E402
from server.media_processing import (  # noqa: E402
    ALLOWED_MEDIA_EXT, ensure_dirs, ffprobe_info, magic_matches_extension,
)


def tags_from(info: dict, path: Path) -> dict:
    tags = {k.lower(): v for k, v in (info.get("format", {}).get("tags") or {}).items()}
    return {
        "title": (tags.get("title") or path.stem).strip(),
        "artist": (tags.get("artist") or tags.get("album_artist") or UNKNOWN_ARTIST).strip(),
        "album": (tags.get("album") or "").strip(),
        "year": _year(tags.get("date") or tags.get("year")),
    }


def _year(v) -> int | None:
    try:
        y = int(str(v)[:4])
        return y if 1900 <= y <= 2100 else None
    except (TypeError, ValueError):
        return None


def import_file(path: Path, genre_id: int | None) -> str:
    ext = path.suffix.lower()
    if ext not in ALLOWED_MEDIA_EXT:
        return "rechazado (extensión)"
    with open(path, "rb") as f:
        head = f.read(16)
    if not magic_matches_extension(head, ext):
        return "rechazado (magic bytes)"
    info = ffprobe_info(path)
    if info is None:
        return "rechazado (ffprobe)"

    # Copia a temporal para no mover el original del usuario
    fd, tmp_name = tempfile.mkstemp(suffix=ext)
    os.close(fd)
    shutil.copy2(path, tmp_name)
    tmp = Path(tmp_name)
    try:
        meta = tags_from(info, path)
        _tid, result = ingest(tmp, ext, info, title=meta["title"], artist=meta["artist"],
                              album=meta["album"], year=meta["year"], genre_id=genre_id)
        return result
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Importa una carpeta de medios al portal.")
    parser.add_argument("folder", help="Carpeta con archivos mp4/mp3/m4a/flac")
    parser.add_argument("--genre", help="Género a asignar (se crea si no existe)")
    args = parser.parse_args()

    init_db()
    ensure_dirs()
    folder = Path(args.folder).expanduser().resolve()
    if not folder.is_dir():
        print(f"No es una carpeta: {folder}")
        sys.exit(1)

    genre_id = None
    if args.genre:
        row = q1("SELECT id FROM genres WHERE name = ?", (args.genre,))
        genre_id = row["id"] if row else execute("INSERT INTO genres (name) VALUES (?)", (args.genre,))

    # Los videos primero: así cada tema nace con su video y el audio suelto
    # posterior se fusiona (o se descarta) en lugar de crear una entrada aparte.
    files = [p for p in sorted(folder.rglob("*")) if p.is_file()]
    files.sort(key=lambda p: 0 if p.suffix.lower() == ".mp4" else 1)

    stats: dict[str, int] = {}
    for path in files:
        result = import_file(path, genre_id)
        stats[result] = stats.get(result, 0) + 1
        print(f"  {path.name}: {result}")

    print("\nResumen:")
    for k, v in sorted(stats.items()):
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
