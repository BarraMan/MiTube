"""Crea el usuario administrador inicial.
La contraseña se toma de ADMIN_INITIAL_PASSWORD; si no existe, se genera una
aleatoria y se muestra UNA sola vez por consola. Nunca se guarda en claro."""
import os
import secrets
import string
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from server.database import execute, init_db, q1  # noqa: E402
from server.security import hash_password  # noqa: E402


def main() -> None:
    init_db()
    if q1("SELECT id FROM users WHERE role = 'admin' LIMIT 1"):
        print("Ya existe un administrador; no se hace nada.")
        return
    password = os.environ.get("ADMIN_INITIAL_PASSWORD")
    generated = False
    if not password:
        alphabet = string.ascii_letters + string.digits + "!@#$%&*"
        password = "".join(secrets.choice(alphabet) for _ in range(16))
        generated = True
    execute(
        "INSERT INTO users (username, email, password_hash, role) VALUES (?,?,?,?)",
        ("admin", "admin@portal.local", hash_password(password), "admin"),
    )
    print("Administrador creado: usuario 'admin'")
    if generated:
        print(f"Contraseña generada (guárdala ahora, no se volverá a mostrar): {password}")


if __name__ == "__main__":
    main()
