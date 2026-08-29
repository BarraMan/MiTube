"""Portal Multimedia — aplicación FastAPI principal.
Incluye cabeceras de seguridad, rate limiting básico, manejo global de errores
(el detalle técnico va al log, nunca a la respuesta) y servido estático local."""
import logging
import time
from collections import defaultdict, deque
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .database import init_db
from .media_processing import ensure_dirs
from .routers import admin, auth, stream, tracks

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("portal")

init_db()
ensure_dirs()

app = FastAPI(title="MiTube", docs_url=None, redoc_url=None, openapi_url=None)

# CORS abierto: la autorización real la hace el token Bearer en cada endpoint.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ---- Rate limiting básico en memoria (por IP y ruta sensible) ----
_BUCKETS: dict[str, deque] = defaultdict(deque)
_LIMITS = {"/api/auth/login": (10, 60), "/api/auth/register": (10, 60),
           "/api/download": (60, 60)}


def _rate_limited(key: str, limit: int, window: int) -> bool:
    now = time.monotonic()
    dq = _BUCKETS[key]
    while dq and dq[0] < now - window:
        dq.popleft()
    if len(dq) >= limit:
        return True
    dq.append(now)
    return False


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    path = request.url.path
    ip = request.client.host if request.client else "?"
    for prefix, (limit, window) in _LIMITS.items():
        if path.startswith(prefix):
            if _rate_limited(f"{ip}:{prefix}", limit, window):
                log.warning("Rate limit alcanzado ip=%s ruta=%s", ip, prefix)
                return JSONResponse({"detail": "Demasiadas solicitudes. Espera un momento."}, status_code=429)
    try:
        response = await call_next(request)
    except Exception:
        log.exception("Error no controlado en %s", path)
        # 4xx: el proxy de despliegue reescribe los 5xx; el detalle queda solo en el log
        return JSONResponse({"detail": "Error interno del servidor."}, status_code=422)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    if path.endswith((".html", "/")) or path == "":
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data: blob: *; media-src 'self' blob: *; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://api.fontshare.com; "
            "font-src 'self' https://fonts.gstatic.com https://cdn.fontshare.com; "
            "script-src 'self'; connect-src 'self' *",
        )
    return response


app.include_router(auth.router)
app.include_router(tracks.router)
app.include_router(stream.router)
app.include_router(admin.router)


@app.get("/api/health")
def health():
    return {"ok": True}


# ---- Frontend estático (modo local; en despliegue lo sirve el CDN) ----
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


# Mismas rutas relativas que usa index.html (css/, js/) para modo local
app.mount("/css", StaticFiles(directory=STATIC_DIR / "css"), name="css")
app.mount("/js", StaticFiles(directory=STATIC_DIR / "js"), name="js")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
