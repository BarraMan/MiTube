"""Capa de base de datos SQLite: conexiones con PRAGMAs seguros y helpers parametrizados."""
import os
import sqlite3
import threading
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
# MITUBE_DB permite ubicar la BD en un volumen (Docker) o ruta personalizada
DB_PATH = Path(os.environ.get("MITUBE_DB", BASE_DIR / "portal.db"))
SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"
MEDIA_DIR = BASE_DIR / "media"

_local = threading.local()


def get_db() -> sqlite3.Connection:
    """Una conexión por hilo, con WAL y foreign keys activados."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 15000")
        _local.conn = conn
    return conn


def init_db() -> None:
    conn = get_db()
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    # Migraciones defensivas para BD creadas con versiones anteriores del esquema
    for ddl in (
        "ALTER TABLE tracks ADD COLUMN video_hash_sha256 TEXT",
        "ALTER TABLE tracks ADD COLUMN audio_extracted INTEGER NOT NULL DEFAULT 0",
    ):
        try:
            conn.execute(ddl)
        except sqlite3.OperationalError:
            pass  # la columna ya existe
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_tracks_video_hash ON tracks(video_hash_sha256)")
    conn.commit()


def q(sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    """SELECT parametrizado."""
    return get_db().execute(sql, params).fetchall()


def q1(sql: str, params: tuple = ()) -> sqlite3.Row | None:
    return get_db().execute(sql, params).fetchone()


def execute(sql: str, params: tuple = ()) -> int:
    """INSERT/UPDATE/DELETE parametrizado. Devuelve lastrowid."""
    conn = get_db()
    cur = conn.execute(sql, params)
    conn.commit()
    return cur.lastrowid


def escape_like(value: str) -> str:
    """Escapa % y _ para usarse con LIKE ... ESCAPE '\\'."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def get_setting(key: str, default: str = "") -> str:
    row = q1("SELECT value FROM settings WHERE key = ?", (key,))
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    execute("INSERT INTO settings (key, value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
