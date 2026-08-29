"""Seguridad: hasheo argon2id, sesiones server-side, políticas de contraseña y bloqueo."""
import hashlib
import logging
import re
import secrets
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Depends, HTTPException, Request

from .database import execute, q1

log = logging.getLogger("portal.security")

ph = PasswordHasher()  # argon2id por defecto

SESSION_HOURS = 24
MAX_FAILED = 5
LOCK_MINUTES = 15

USERNAME_RE = re.compile(r"^[a-zA-Z0-9_]{3,32}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")


def hash_password(password: str) -> str:
    return ph.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return ph.verify(password_hash, password)
    except VerifyMismatchError:
        return False
    except Exception:
        log.exception("Error verificando hash de contraseña")
        return False


def validate_password_strength(password: str) -> str | None:
    """Devuelve un mensaje de error o None si la contraseña es válida."""
    if len(password) < 10:
        return "La contraseña debe tener al menos 10 caracteres."
    if len(password) > 128:
        return "La contraseña es demasiado larga (máx. 128)."
    if not re.search(r"[a-z]", password):
        return "Debe incluir al menos una minúscula."
    if not re.search(r"[A-Z]", password):
        return "Debe incluir al menos una mayúscula."
    if not re.search(r"[0-9]", password):
        return "Debe incluir al menos un dígito."
    if not re.search(r"[^a-zA-Z0-9]", password):
        return "Debe incluir al menos un símbolo."
    return None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(user_id: int, user_agent: str, ip: str) -> str:
    token = secrets.token_urlsafe(32)  # 256 bits
    execute(
        "INSERT INTO sessions (token_hash, user_id, expires_at, user_agent, ip) VALUES (?,?,?,?,?)",
        (_token_hash(token), user_id, _iso(_now() + timedelta(hours=SESSION_HOURS)), user_agent[:256], ip[:64]),
    )
    return token


def destroy_session(token: str) -> None:
    execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))


def destroy_user_sessions(user_id: int) -> None:
    execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))


def purge_expired_sessions() -> None:
    execute("DELETE FROM sessions WHERE expires_at < ?", (_iso(_now()),))


def get_session_user(token: str):
    """Valida token y devuelve fila de usuario, renovando la sesión (sliding)."""
    if not token or len(token) > 128:
        return None
    row = q1(
        """SELECT s.id AS session_id, s.expires_at, u.* FROM sessions s
           JOIN users u ON u.id = s.user_id
           WHERE s.token_hash = ? AND u.is_active = 1""",
        (_token_hash(token),),
    )
    if row is None:
        return None
    if row["expires_at"] < _iso(_now()):
        execute("DELETE FROM sessions WHERE id = ?", (row["session_id"],))
        return None
    # Renovación deslizante
    execute(
        "UPDATE sessions SET expires_at = ? WHERE id = ?",
        (_iso(_now() + timedelta(hours=SESSION_HOURS)), row["session_id"]),
    )
    return row


def _extract_token(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    # Solo para elementos <video>/<a> que no pueden mandar cabeceras:
    return request.query_params.get("t")


def current_user(request: Request):
    token = _extract_token(request)
    user = get_session_user(token) if token else None
    if user is None:
        raise HTTPException(status_code=401, detail="Sesión inválida o expirada.")
    return user


def admin_user(user=Depends(current_user)):
    if user["role"] != "admin":
        log.warning("Acceso admin denegado a user_id=%s", user["id"])
        raise HTTPException(status_code=403, detail="Se requiere rol de administrador.")
    return user


def register_failed_login(user_row) -> None:
    attempts = user_row["failed_login_attempts"] + 1
    locked_until = None
    if attempts >= MAX_FAILED:
        locked_until = _iso(_now() + timedelta(minutes=LOCK_MINUTES))
        log.warning("Cuenta bloqueada por intentos fallidos: user_id=%s", user_row["id"])
    execute(
        "UPDATE users SET failed_login_attempts = ?, locked_until = ? WHERE id = ?",
        (attempts, locked_until, user_row["id"]),
    )


def reset_failed_logins(user_id: int) -> None:
    execute("UPDATE users SET failed_login_attempts = 0, locked_until = NULL WHERE id = ?", (user_id,))


def is_locked(user_row) -> bool:
    lu = user_row["locked_until"]
    return bool(lu) and lu > _iso(_now())
