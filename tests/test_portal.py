"""Pruebas del portal contra el servidor en ejecución (http://localhost:9000).
Ejecutar: python -m pytest tests/ -v  (el servidor debe estar arrancado)."""
import os
import subprocess
import tempfile
import uuid
from pathlib import Path

import httpx
import pytest

BASE = os.environ.get("PORTAL_URL", "http://localhost:9000")
ADMIN_PASS = os.environ.get("ADMIN_INITIAL_PASSWORD", "CambiaEstaClave#2026")


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=BASE, timeout=30) as c:
        yield c


@pytest.fixture(scope="module")
def admin_token(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": ADMIN_PASS})
    assert r.status_code == 200
    return r.json()["token"]


@pytest.fixture(scope="module")
def user_token(client):
    name = f"u{uuid.uuid4().hex[:10]}"
    r = client.post("/api/auth/register", json={
        "username": name, "email": f"{name}@test.mx", "password": "Segura#2026xY"})
    assert r.status_code == 201
    return r.json()["token"]


def auth(tok):
    return {"Authorization": f"Bearer {tok}"}


# ---------- Autenticación ----------

def test_register_weak_password_rejected(client):
    r = client.post("/api/auth/register", json={
        "username": f"w{uuid.uuid4().hex[:8]}", "email": f"w{uuid.uuid4().hex[:8]}@t.mx",
        "password": "corta"})
    assert r.status_code == 400


def test_register_bad_email_rejected(client):
    r = client.post("/api/auth/register", json={
        "username": f"e{uuid.uuid4().hex[:8]}", "email": "no-es-correo",
        "password": "Segura#2026xY"})
    assert r.status_code == 400


def test_login_generic_error(client):
    r = client.post("/api/auth/login", json={"username": "noexiste_x", "password": "Aa1!aaaaaa"})
    assert r.status_code == 401
    assert "Credenciales" in r.json()["detail"]


def test_login_lockout_after_failures(client):
    name = f"l{uuid.uuid4().hex[:10]}"
    client.post("/api/auth/register", json={
        "username": name, "email": f"{name}@t.mx", "password": "Segura#2026xY"})
    for _ in range(5):
        r = client.post("/api/auth/login", json={"username": name, "password": "Mala#123456"})
    # Sexto intento: cuenta bloqueada (423) incluso con contraseña correcta
    r = client.post("/api/auth/login", json={"username": name, "password": "Segura#2026xY"})
    assert r.status_code == 423


def test_logout_invalidates_session(client):
    name = f"o{uuid.uuid4().hex[:10]}"
    r = client.post("/api/auth/register", json={
        "username": name, "email": f"{name}@t.mx", "password": "Segura#2026xY"})
    tok = r.json()["token"]
    assert client.post("/api/auth/logout", headers=auth(tok)).status_code == 200
    assert client.get("/api/auth/me", headers=auth(tok)).status_code == 401


# ---------- Autorización ----------

def test_tracks_requires_auth(client):
    assert client.get("/api/tracks").status_code == 401


def test_admin_denied_for_regular_user(client, user_token):
    assert client.get("/api/admin/users", headers=auth(user_token)).status_code == 403
    assert client.post("/api/admin/genres", json={"name": "X"}, headers=auth(user_token)).status_code == 403


# ---------- Streaming ----------

def test_range_request_206(client, user_token):
    r = client.get("/api/stream/1/audio", params={"t": user_token},
                   headers={"Range": "bytes=0-1023"})
    assert r.status_code == 206
    assert r.headers["content-range"].startswith("bytes 0-1023/")
    assert int(r.headers["content-length"]) == 1024


def test_range_open_ended(client, user_token):
    r = client.get("/api/stream/1/audio", params={"t": user_token},
                   headers={"Range": "bytes=100-"})
    assert r.status_code == 206


def test_range_invalid_416(client, user_token):
    r = client.get("/api/stream/1/audio", params={"t": user_token},
                   headers={"Range": "bytes=999999999-"})
    assert r.status_code == 416


def test_download_disposition(client, user_token):
    r = client.get("/api/download/1/audio", params={"t": user_token})
    assert r.status_code == 200
    assert r.headers["content-disposition"].startswith("attachment")


def test_stream_nonexistent_track(client, user_token):
    r = client.get("/api/stream/999999/audio", params={"t": user_token})
    assert r.status_code == 404


# ---------- Búsqueda / inyección ----------

def test_search_like_escape(client, user_token):
    # % y _ no deben actuar como comodines: literal sin resultados
    r = client.get("/api/tracks", params={"q": "%%%"}, headers=auth(user_token))
    assert r.status_code == 200
    assert r.json()["tracks"] == []


def test_sqli_attempt_harmless(client, user_token):
    r = client.get("/api/tracks", params={"q": "'; DROP TABLE tracks; --"}, headers=auth(user_token))
    assert r.status_code == 200
    r2 = client.get("/api/tracks", headers=auth(user_token))
    assert len(r2.json()["tracks"]) > 0  # la tabla sigue viva


def test_invalid_sort_rejected(client, user_token):
    r = client.get("/api/tracks", params={"sort": "title; DROP"}, headers=auth(user_token))
    assert r.status_code == 400


# ---------- Subidas (magic bytes) ----------

def test_upload_fake_extension_rejected(client, admin_token):
    # Un .mp3 cuyo contenido no es MP3 debe rechazarse por magic bytes
    files = {"media": ("malicioso.mp3", b"#!/bin/sh\necho hack", "audio/mpeg")}
    data = {"title": "X", "artist": "Y"}
    r = client.post("/api/admin/tracks", files=files, data=data, headers=auth(admin_token))
    assert r.status_code == 400
    assert "no coincide" in r.json()["detail"]


def test_upload_disallowed_extension(client, admin_token):
    files = {"media": ("script.sh", b"#!/bin/sh", "text/x-sh")}
    data = {"title": "X", "artist": "Y"}
    r = client.post("/api/admin/tracks", files=files, data=data, headers=auth(admin_token))
    assert r.status_code == 400


# ---------- Play count ----------

def test_play_count_no_double(client, user_token):
    r1 = client.post("/api/tracks/2/play", headers=auth(user_token))
    r2 = client.post("/api/tracks/2/play", headers=auth(user_token))
    assert r1.json()["counted"] in (True, False)
    if r1.json()["counted"]:
        assert r2.json()["counted"] is False


# ---------- Géneros ----------

def test_genre_crud_and_protection(client, admin_token):
    name = f"G{uuid.uuid4().hex[:6]}"
    r = client.post("/api/admin/genres", json={"name": name}, headers=auth(admin_token))
    assert r.status_code == 201
    gid = r.json()["id"]
    assert client.put(f"/api/admin/genres/{gid}", json={"name": name + "2"},
                      headers=auth(admin_token)).status_code == 200
    assert client.delete(f"/api/admin/genres/{gid}", headers=auth(admin_token)).status_code == 200


def test_admin_cannot_delete_self(client, admin_token):
    me = client.get("/api/auth/me", headers=auth(admin_token)).json()
    r = client.delete(f"/api/admin/users/{me['id']}", headers=auth(admin_token))
    assert r.status_code == 400


# ---------- Registro público activable/desactivable ----------

def test_settings_admin_only(client, user_token):
    assert client.get("/api/admin/settings", headers=auth(user_token)).status_code == 403
    r = client.put("/api/admin/settings", json={"public_registration": False}, headers=auth(user_token))
    assert r.status_code == 403


def test_registration_toggle(client, admin_token):
    try:
        # Desactivar: el registro público debe rechazarse con 403
        r = client.put("/api/admin/settings", json={"public_registration": False}, headers=auth(admin_token))
        assert r.status_code == 200 and r.json()["public_registration"] is False
        assert client.get("/api/auth/config").json()["public_registration"] is False
        name = f"reg{uuid.uuid4().hex[:9]}"
        r = client.post("/api/auth/register", json={
            "username": name, "email": f"{name}@t.mx", "password": "Segura#2026xY"})
        assert r.status_code == 403
        # El admin sigue pudiendo crear cuentas desde el panel
        r = client.post("/api/admin/users", json={
            "username": name, "email": f"{name}@t.mx", "password": "Segura#2026xY",
            "role": "user", "is_active": True}, headers=auth(admin_token))
        assert r.status_code == 201
    finally:
        # Reactivar para no afectar otras pruebas
        client.put("/api/admin/settings", json={"public_registration": True}, headers=auth(admin_token))
    assert client.get("/api/auth/config").json()["public_registration"] is True
    name2 = f"reg{uuid.uuid4().hex[:9]}"
    r = client.post("/api/auth/register", json={
        "username": name2, "email": f"{name2}@t.mx", "password": "Segura#2026xY"})
    assert r.status_code == 201


# ---------- Fusión de modalidades (sin duplicados en la lista) ----------

@pytest.fixture(scope="module")
def sample_files():
    """Genera un MP4 y su audio extraído (misma pista) con FFmpeg."""
    d = Path(tempfile.mkdtemp())
    mp4 = d / "cancion_fusion.mp4"
    m4a = d / "cancion_fusion.m4a"
    subprocess.run(["ffmpeg", "-y", "-v", "error",
                    "-f", "lavfi", "-i", "testsrc2=s=320x180:d=3",
                    "-f", "lavfi", "-i", "sine=frequency=523:duration=3",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-shortest", str(mp4)], check=True, timeout=120)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(mp4),
                    "-vn", "-c:a", "aac", str(m4a)], check=True, timeout=120)
    return mp4, m4a


def _count_tracks(client, tok, q):
    r = client.get("/api/tracks", params={"q": q}, headers=auth(tok))
    return r.json()["tracks"]


def test_merge_video_then_audio_single_entry(client, admin_token, sample_files):
    """Video primero -> tema creado; su audio extraído después -> se fusiona.
    La lista debe mostrar UNA sola entrada con ambas modalidades."""
    mp4, m4a = sample_files
    title = f"Fusion{uuid.uuid4().hex[:6]}"
    data = {"title": title, "artist": "Prueba Fusion"}
    r1 = client.post("/api/admin/tracks", files={"media": (mp4.name, mp4.read_bytes(), "video/mp4")},
                     data=data, headers=auth(admin_token))
    assert r1.status_code == 201 and r1.json()["result"] == "creado"
    r2 = client.post("/api/admin/tracks", files={"media": (m4a.name, m4a.read_bytes(), "audio/mp4")},
                     data=data, headers=auth(admin_token))
    assert r2.status_code == 201
    assert "audio original" in r2.json()["result"]
    assert r2.json()["id"] == r1.json()["id"]  # mismo tema, no uno nuevo
    tracks = _count_tracks(client, admin_token, title)
    assert len(tracks) == 1 and tracks[0]["has_video"] is True


def test_merge_audio_then_video_single_entry(client, admin_token, sample_files):
    """Audio primero -> tema solo-audio; el video después se ADJUNTA sin duplicar."""
    mp4, m4a = sample_files
    title = f"Fusion{uuid.uuid4().hex[:6]}"
    # Re-codificar para que el hash no choque con la otra prueba
    d = Path(tempfile.mkdtemp())
    m4a2, mp42 = d / "b.m4a", d / "b.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(m4a), "-c:a", "aac", "-b:a", "96k", str(m4a2)], check=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(mp4), "-c:v", "libx264", "-crf", "30",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k", str(mp42)], check=True)
    data = {"title": title, "artist": "Prueba Fusion"}
    r1 = client.post("/api/admin/tracks", files={"media": (m4a2.name, m4a2.read_bytes(), "audio/mp4")},
                     data=data, headers=auth(admin_token))
    assert r1.status_code == 201 and r1.json()["result"] == "creado"
    tracks = _count_tracks(client, admin_token, title)
    assert tracks[0]["has_video"] is False
    r2 = client.post("/api/admin/tracks", files={"media": (mp42.name, mp42.read_bytes(), "video/mp4")},
                     data=data, headers=auth(admin_token))
    assert r2.status_code == 201
    assert "video añadido" in r2.json()["result"]
    assert r2.json()["id"] == r1.json()["id"]
    tracks = _count_tracks(client, admin_token, title)
    assert len(tracks) == 1 and tracks[0]["has_video"] is True


def test_merge_same_modality_rejected(client, admin_token, sample_files):
    """Subir dos veces la misma modalidad del mismo tema -> 409, sin duplicar."""
    mp4, _ = sample_files
    title = f"Fusion{uuid.uuid4().hex[:6]}"
    d = Path(tempfile.mkdtemp())
    a, b = d / "a.mp4", d / "b.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(mp4), "-c:v", "libx264", "-crf", "28",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", str(a)], check=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(mp4), "-c:v", "libx264", "-crf", "32",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", str(b)], check=True)
    data = {"title": title, "artist": "Prueba Fusion"}
    r1 = client.post("/api/admin/tracks", files={"media": (a.name, a.read_bytes(), "video/mp4")},
                     data=data, headers=auth(admin_token))
    assert r1.status_code == 201
    r2 = client.post("/api/admin/tracks", files={"media": (b.name, b.read_bytes(), "video/mp4")},
                     data=data, headers=auth(admin_token))
    assert r2.status_code == 409
    assert len(_count_tracks(client, admin_token, title)) == 1
