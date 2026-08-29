"""Panel de administración: CRUD de usuarios, géneros y biblioteca (solo rol admin)."""
import logging
import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from ..database import execute, get_setting, q, q1, set_setting
from ..library import (
    AUDIO_UPGRADED, CREATED, DUP_FILE, DUP_MODALITY, VIDEO_ATTACHED,
    get_or_create_album, get_or_create_artist, ingest,
)
from ..media_processing import (
    ALLOWED_IMAGE_EXT, ALLOWED_MEDIA_EXT, MAX_UPLOAD_BYTES,
    ffprobe_info, magic_matches_extension, safe_media_path,
)
from ..security import (
    EMAIL_RE, USERNAME_RE, admin_user, destroy_user_sessions, hash_password,
    validate_password_strength,
)

log = logging.getLogger("portal.admin")
router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(admin_user)])


# ---------- Usuarios ----------

class UserIn(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    email: str = Field(min_length=5, max_length=254)
    password: str | None = None
    role: str = Field(default="user", pattern="^(user|admin)$")
    is_active: bool = True


@router.get("/users")
def list_users(admin=Depends(admin_user)):
    return [dict(r) for r in q(
        "SELECT id, username, email, role, is_active, failed_login_attempts, created_at "
        "FROM users ORDER BY id")]


@router.post("/users", status_code=201)
def create_user(data: UserIn, admin=Depends(admin_user)):
    if not USERNAME_RE.match(data.username):
        raise HTTPException(400, "Usuario inválido.")
    if not EMAIL_RE.match(data.email):
        raise HTTPException(400, "Correo inválido.")
    if not data.password:
        raise HTTPException(400, "La contraseña es obligatoria.")
    err = validate_password_strength(data.password)
    if err:
        raise HTTPException(400, err)
    if q1("SELECT id FROM users WHERE username = ? OR email = ?", (data.username, data.email)):
        raise HTTPException(409, "Usuario o correo ya existe.")
    uid = execute("INSERT INTO users (username, email, password_hash, role, is_active) VALUES (?,?,?,?,?)",
                  (data.username, data.email, hash_password(data.password), data.role, int(data.is_active)))
    return {"id": uid}


@router.put("/users/{user_id}")
def update_user(user_id: int, data: UserIn, admin=Depends(admin_user)):
    target = q1("SELECT * FROM users WHERE id = ?", (user_id,))
    if target is None:
        raise HTTPException(404, "Usuario no encontrado.")
    if user_id == admin["id"] and (data.role != "admin" or not data.is_active):
        raise HTTPException(400, "No puedes suspenderte ni quitarte el rol admin a ti mismo.")
    if not USERNAME_RE.match(data.username) or not EMAIL_RE.match(data.email):
        raise HTTPException(400, "Usuario o correo inválido.")
    dup = q1("SELECT id FROM users WHERE (username = ? OR email = ?) AND id != ?",
             (data.username, data.email, user_id))
    if dup:
        raise HTTPException(409, "Usuario o correo ya existe.")
    execute("UPDATE users SET username=?, email=?, role=?, is_active=? WHERE id=?",
            (data.username, data.email, data.role, int(data.is_active), user_id))
    if data.password:
        err = validate_password_strength(data.password)
        if err:
            raise HTTPException(400, err)
        execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(data.password), user_id))
    if not data.is_active:
        destroy_user_sessions(user_id)
    return {"ok": True}


@router.delete("/users/{user_id}")
def delete_user(user_id: int, admin=Depends(admin_user)):
    if user_id == admin["id"]:
        raise HTTPException(400, "No puedes eliminar tu propia cuenta.")
    if q1("SELECT id FROM users WHERE id = ?", (user_id,)) is None:
        raise HTTPException(404, "Usuario no encontrado.")
    destroy_user_sessions(user_id)
    execute("DELETE FROM users WHERE id = ?", (user_id,))
    log.info("Usuario eliminado id=%s por admin=%s", user_id, admin["id"])
    return {"ok": True}


# ---------- Configuración del portal ----------

class SettingsIn(BaseModel):
    public_registration: bool


@router.get("/settings")
def get_settings(admin=Depends(admin_user)):
    return {"public_registration": get_setting("public_registration", "1") == "1"}


@router.put("/settings")
def update_settings(data: SettingsIn, admin=Depends(admin_user)):
    set_setting("public_registration", "1" if data.public_registration else "0")
    log.info("Registro público %s por admin=%s",
             "activado" if data.public_registration else "desactivado", admin["id"])
    return {"ok": True, "public_registration": data.public_registration}


# ---------- Géneros ----------

class GenreIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)


@router.post("/genres", status_code=201)
def create_genre(data: GenreIn, admin=Depends(admin_user)):
    name = data.name.strip()
    if not name:
        raise HTTPException(400, "Nombre vacío.")
    if q1("SELECT id FROM genres WHERE name = ?", (name,)):
        raise HTTPException(409, "El género ya existe.")
    return {"id": execute("INSERT INTO genres (name) VALUES (?)", (name,))}


@router.put("/genres/{genre_id}")
def update_genre(genre_id: int, data: GenreIn, admin=Depends(admin_user)):
    if q1("SELECT id FROM genres WHERE id = ?", (genre_id,)) is None:
        raise HTTPException(404, "Género no encontrado.")
    execute("UPDATE genres SET name = ? WHERE id = ?", (data.name.strip(), genre_id))
    return {"ok": True}


@router.delete("/genres/{genre_id}")
def delete_genre(genre_id: int, admin=Depends(admin_user)):
    used = q1("SELECT COUNT(*) AS n FROM tracks WHERE genre_id = ?", (genre_id,))
    if used and used["n"] > 0:
        raise HTTPException(409, f"No se puede borrar: {used['n']} temas usan este género.")
    execute("DELETE FROM genres WHERE id = ?", (genre_id,))
    return {"ok": True}


# ---------- Biblioteca ----------

async def _receive_validated(file: UploadFile, allowed_ext: set[str]) -> tuple[Path, str]:
    """Recibe un upload a archivo temporal validando extensión, tamaño y magic bytes."""
    ext = Path(file.filename or "").suffix.lower()
    if ext not in allowed_ext:
        raise HTTPException(400, f"Extensión no permitida: {ext or '(ninguna)'}")
    head = await file.read(16)
    if not magic_matches_extension(head, ext):
        raise HTTPException(400, "El contenido del archivo no coincide con su extensión.")
    fd, tmp_name = tempfile.mkstemp(suffix=ext)
    size = len(head)
    try:
        with os.fdopen(fd, "wb") as tmp:
            tmp.write(head)
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, "Archivo demasiado grande.")
                tmp.write(chunk)
    except HTTPException:
        os.unlink(tmp_name)
        raise
    return Path(tmp_name), ext


@router.post("/tracks", status_code=201)
async def upload_track(
    media: UploadFile,
    title: str = Form(min_length=1, max_length=160),
    artist: str = Form(min_length=1, max_length=120),
    album: str = Form(default="", max_length=160),
    year: int | None = Form(default=None, ge=1900, le=2100),
    genre_id: int | None = Form(default=None, ge=1),
    cover: UploadFile | None = None,
    admin=Depends(admin_user),
):
    if genre_id and q1("SELECT id FROM genres WHERE id = ?", (genre_id,)) is None:
        raise HTTPException(400, "Género inexistente.")
    tmp, ext = await _receive_validated(media, ALLOWED_MEDIA_EXT)
    ctmp = cext = None
    try:
        info = ffprobe_info(tmp)
        if info is None:
            raise HTTPException(400, "El archivo no es un medio válido (ffprobe lo rechazó).")
        if cover and cover.filename:
            ctmp, cext = await _receive_validated(cover, ALLOWED_IMAGE_EXT)

        tid, result = ingest(tmp, ext, info, title=title, artist=artist, album=album,
                             year=year, genre_id=genre_id, cover_tmp=ctmp, cover_ext=cext)
        if result == DUP_FILE:
            raise HTTPException(409, "Este archivo ya existe en la biblioteca (duplicado exacto).")
        if result == DUP_MODALITY:
            raise HTTPException(409, "El tema ya existe con esa modalidad (no se duplica en la lista).")
        if tid is None:
            raise HTTPException(400, f"No se pudo procesar el archivo: {result}.")
        log.info("Ingesta id=%s (%s) por admin=%s", tid, result, admin["id"])
        mensajes = {
            CREATED: "Tema creado.",
            VIDEO_ATTACHED: "Video adjuntado al tema existente (sin duplicar).",
            AUDIO_UPGRADED: "Audio original reemplazó al extraído del video.",
        }
        return {"id": tid, "result": result, "message": mensajes.get(result, result)}
    finally:
        for p in (tmp, ctmp):
            if p is not None and p.exists():
                p.unlink(missing_ok=True)


class TrackEdit(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    artist: str = Field(min_length=1, max_length=120)
    album: str = Field(default="", max_length=160)
    year: int | None = Field(default=None, ge=1900, le=2100)
    genre_id: int | None = Field(default=None, ge=1)


@router.put("/tracks/{track_id}")
def edit_track(track_id: int, data: TrackEdit, admin=Depends(admin_user)):
    if q1("SELECT id FROM tracks WHERE id = ?", (track_id,)) is None:
        raise HTTPException(404, "Tema no encontrado.")
    if data.genre_id and q1("SELECT id FROM genres WHERE id = ?", (data.genre_id,)) is None:
        raise HTTPException(400, "Género inexistente.")
    artist_id = get_or_create_artist(data.artist)
    album_id = get_or_create_album(data.album, artist_id, data.year)
    execute("UPDATE tracks SET title=?, artist_id=?, album_id=?, genre_id=?, year=? WHERE id=?",
            (data.title.strip(), artist_id, album_id, data.genre_id, data.year, track_id))
    return {"ok": True}


@router.delete("/tracks/{track_id}")
def delete_track(track_id: int, admin=Depends(admin_user)):
    row = q1("SELECT * FROM tracks WHERE id = ?", (track_id,))
    if row is None:
        raise HTTPException(404, "Tema no encontrado.")
    for col in ("video_path", "audio_path", "cover_path"):
        rel = row[col]
        if rel:
            try:
                safe_media_path(rel).unlink(missing_ok=True)
            except ValueError:
                log.error("Ruta insegura al borrar track=%s col=%s", track_id, col)
    execute("DELETE FROM tracks WHERE id = ?", (track_id,))
    log.info("Track eliminado id=%s por admin=%s", track_id, admin["id"])
    return {"ok": True}
