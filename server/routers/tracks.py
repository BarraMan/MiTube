"""Biblioteca: listado con filtros/orden/búsqueda, metadatos y conteo de reproducciones."""
import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from ..database import escape_like, execute, q, q1
from ..security import current_user

log = logging.getLogger("portal.tracks")
router = APIRouter(prefix="/api", tags=["tracks"])

SORTS = {
    "title": "t.title COLLATE NOCASE ASC",
    "artist": "ar.name COLLATE NOCASE ASC, t.title COLLATE NOCASE ASC",
    "year": "t.year DESC, t.title COLLATE NOCASE ASC",
    "plays": "t.play_count DESC, t.title COLLATE NOCASE ASC",
    "recent": "t.created_at DESC, t.id DESC",
}

TRACK_SELECT = """
SELECT t.id, t.title, t.year, t.duration_seconds, t.play_count, t.created_at,
       t.video_path IS NOT NULL AS has_video,
       t.cover_path IS NOT NULL AS has_cover,
       ar.id AS artist_id, ar.name AS artist,
       al.id AS album_id, al.title AS album,
       g.id AS genre_id, g.name AS genre
FROM tracks t
JOIN artists ar ON ar.id = t.artist_id
LEFT JOIN albums al ON al.id = t.album_id
LEFT JOIN genres g ON g.id = t.genre_id
"""


def _track_dict(r) -> dict:
    return {
        "id": r["id"], "title": r["title"], "year": r["year"],
        "duration": r["duration_seconds"], "play_count": r["play_count"],
        "has_video": bool(r["has_video"]), "has_cover": bool(r["has_cover"]),
        "artist": r["artist"], "artist_id": r["artist_id"],
        "album": r["album"], "album_id": r["album_id"],
        "genre": r["genre"], "genre_id": r["genre_id"],
        "created_at": r["created_at"],
    }


@router.get("/tracks")
def list_tracks(
    user=Depends(current_user),
    genre_id: int | None = Query(None, ge=1),
    artist_id: int | None = Query(None, ge=1),
    album_id: int | None = Query(None, ge=1),
    q_text: str | None = Query(None, alias="q", max_length=100),
    sort: str = Query("recent"),
    page: int = Query(1, ge=1),
    per_page: int = Query(100, ge=1, le=200),
):
    order = SORTS.get(sort)
    if order is None:
        raise HTTPException(400, "Orden no válido.")
    where, params = [], []
    if genre_id:
        where.append("t.genre_id = ?"); params.append(genre_id)
    if artist_id:
        where.append("t.artist_id = ?"); params.append(artist_id)
    if album_id:
        where.append("t.album_id = ?"); params.append(album_id)
    if q_text and q_text.strip():
        like = f"%{escape_like(q_text.strip())}%"
        where.append("""(t.title LIKE ? ESCAPE '\\' OR ar.name LIKE ? ESCAPE '\\'
                        OR IFNULL(al.title,'') LIKE ? ESCAPE '\\')""")
        params += [like, like, like]
    sql = TRACK_SELECT + (" WHERE " + " AND ".join(where) if where else "")
    # Conteo total (mismos filtros, sin la paginación) para la navegación
    count_sql = (
        "SELECT COUNT(*) AS n FROM tracks t "
        "JOIN artists ar ON ar.id = t.artist_id "
        "LEFT JOIN albums al ON al.id = t.album_id "
        "LEFT JOIN genres g ON g.id = t.genre_id"
    )
    if where:
        count_sql += " WHERE " + " AND ".join(where)
    total = q1(count_sql, tuple(params))["n"]
    sql += f" ORDER BY {order} LIMIT ? OFFSET ?"
    params += [per_page, (page - 1) * per_page]
    rows = q(sql, tuple(params))
    total_pages = (total + per_page - 1) // per_page if total else 0
    return {
        "tracks": [_track_dict(r) for r in rows],
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": total_pages,
        "has_next": page < total_pages,
        "has_prev": page > 1,
    }


@router.get("/tracks/{track_id}")
def get_track(track_id: int, user=Depends(current_user)):
    r = q1(TRACK_SELECT + " WHERE t.id = ?", (track_id,))
    if r is None:
        raise HTTPException(404, "Tema no encontrado.")
    return _track_dict(r)


@router.get("/meta")
def meta(user=Depends(current_user)):
    return {
        "genres": [dict(r) for r in q("SELECT id, name FROM genres ORDER BY name COLLATE NOCASE")],
        "artists": [dict(r) for r in q(
            "SELECT ar.id, ar.name, COUNT(t.id) AS tracks FROM artists ar "
            "LEFT JOIN tracks t ON t.artist_id = ar.id GROUP BY ar.id ORDER BY ar.name COLLATE NOCASE")],
        "albums": [dict(r) for r in q(
            "SELECT al.id, al.title, al.year, ar.name AS artist FROM albums al "
            "LEFT JOIN artists ar ON ar.id = al.artist_id ORDER BY al.title COLLATE NOCASE")],
    }


@router.post("/tracks/{track_id}/play")
def register_play(track_id: int, user=Depends(current_user)):
    """Incrementa play_count con protección anti doble conteo (1 evento por sesión/5 min)."""
    if q1("SELECT id FROM tracks WHERE id = ?", (track_id,)) is None:
        raise HTTPException(404, "Tema no encontrado.")
    recent = q1(
        """SELECT id FROM play_events WHERE track_id = ? AND user_id = ?
           AND played_at > datetime('now', '-5 minutes') ORDER BY id DESC LIMIT 1""",
        (track_id, user["id"]),
    )
    if recent:
        return {"counted": False}
    execute("INSERT INTO play_events (track_id, user_id) VALUES (?,?)", (track_id, user["id"]))
    execute("UPDATE tracks SET play_count = play_count + 1 WHERE id = ?", (track_id,))
    return {"counted": True}
