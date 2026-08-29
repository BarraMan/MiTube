"""Rutas de autenticación: registro, login con bloqueo progresivo, logout, perfil."""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..database import execute, get_setting, q1
from ..security import (
    EMAIL_RE, USERNAME_RE, create_session, current_user, destroy_session,
    hash_password, is_locked, purge_expired_sessions, register_failed_login,
    reset_failed_logins, validate_password_strength, verify_password,
)

log = logging.getLogger("portal.auth")
router = APIRouter(prefix="/api/auth", tags=["auth"])

GENERIC_LOGIN_ERROR = "Credenciales inválidas."


class RegisterIn(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    email: str = Field(min_length=5, max_length=254)
    password: str = Field(min_length=1, max_length=256)


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=1, max_length=256)


def _public_user(u) -> dict:
    return {"id": u["id"], "username": u["username"], "email": u["email"], "role": u["role"]}


@router.get("/config")
def public_config():
    """Configuración pública mínima para la pantalla de acceso (sin datos sensibles)."""
    return {"public_registration": get_setting("public_registration", "1") == "1"}


@router.post("/register", status_code=201)
def register(data: RegisterIn, request: Request):
    if get_setting("public_registration", "1") != "1":
        log.warning("Intento de registro con el registro público deshabilitado")
        raise HTTPException(403, "El registro público está deshabilitado. Contacta al administrador.")
    # Validación redundante en backend (el frontend ya validó)
    if not USERNAME_RE.match(data.username):
        raise HTTPException(400, "Usuario inválido: 3-32 caracteres alfanuméricos o _.")
    if not EMAIL_RE.match(data.email):
        raise HTTPException(400, "Correo electrónico inválido.")
    err = validate_password_strength(data.password)
    if err:
        raise HTTPException(400, err)
    if q1("SELECT id FROM users WHERE username = ? OR email = ?", (data.username, data.email)):
        raise HTTPException(409, "El usuario o correo ya está registrado.")
    uid = execute(
        "INSERT INTO users (username, email, password_hash) VALUES (?,?,?)",
        (data.username, data.email, hash_password(data.password)),
    )
    log.info("Usuario registrado id=%s", uid)
    token = create_session(uid, request.headers.get("user-agent", ""), request.client.host if request.client else "")
    user = q1("SELECT * FROM users WHERE id = ?", (uid,))
    return {"token": token, "user": _public_user(user)}


@router.post("/login")
def login(data: LoginIn, request: Request):
    purge_expired_sessions()
    user = q1("SELECT * FROM users WHERE username = ? OR email = ?", (data.username, data.username))
    if user is None:
        # Mensaje genérico: no revelar existencia de la cuenta
        raise HTTPException(401, GENERIC_LOGIN_ERROR)
    if is_locked(user):
        raise HTTPException(423, "Cuenta bloqueada temporalmente por intentos fallidos. Intenta en unos minutos.")
    if not user["is_active"]:
        raise HTTPException(401, GENERIC_LOGIN_ERROR)
    if not verify_password(user["password_hash"], data.password):
        register_failed_login(user)
        raise HTTPException(401, GENERIC_LOGIN_ERROR)
    reset_failed_logins(user["id"])
    # Token nuevo en cada login (anti session-fixation)
    token = create_session(user["id"], request.headers.get("user-agent", ""), request.client.host if request.client else "")
    log.info("Login correcto user_id=%s", user["id"])
    return {"token": token, "user": _public_user(user)}


@router.post("/logout")
def logout(request: Request, user=Depends(current_user)):
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        destroy_session(auth[7:].strip())
    return {"ok": True}


@router.get("/me")
def me(user=Depends(current_user)):
    return _public_user(user)
