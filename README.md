# MiTube — Portal Multimedia (estilo YouTube Music)

Portal responsivo de reproducción de música y video con **switch Audio/Video en tiempo real**, modo cinema, cola con autoplay, buscador global, descargas y panel de administración. Backend FastAPI + SQLite; frontend HTML/CSS/JS vanilla en modo oscuro.

## Requisitos

- Python 3.11+
- FFmpeg y ffprobe en el PATH (`sudo apt install ffmpeg`)

## Instalación y arranque

```bash
cd media-portal
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 1. Crear el administrador inicial (la BD se inicializa sola)
ADMIN_INITIAL_PASSWORD='TuClaveSegura#2026' python seed.py
# Si omites la variable, se genera una clave aleatoria y se muestra UNA sola vez.

# 2. (Opcional) Generar contenido de demostración
python make_samples.py

# 3. Arrancar el servidor
python -m uvicorn server.main:app --host 0.0.0.0 --port 8000
```

Abre `http://localhost:8000` en el navegador.

## Importación masiva (con fusión de modalidades)

```bash
python import_media.py /ruta/a/tu/musica --genre "Rock"
# o el atajo preconfigurado (videos primero, luego audios):
bash import_all.sh
```

Recorre la carpeta recursivamente (videos primero), valida cada archivo (extensión, magic bytes, ffprobe), evita duplicados por hash SHA-256, extrae la rendition de audio de los videos y lee metadatos de las etiquetas.

**Cada canción aparece UNA sola vez en la lista** aunque exista en dos archivos. La coincidencia por título+artista (insensible a mayúsculas) fusiona modalidades:

| Situación | Resultado |
|---|---|
| Llega el video de un tema que ya existe solo-audio | El video se **adjunta** al tema (se conserva el audio original) |
| Llega el audio original de un tema cuyo audio fue extraído del video | El audio original **reemplaza** al extraído |
| Llega la misma modalidad que ya existe | Se descarta como duplicado (409 en la web) |
| Archivo idéntico byte a byte (hash) | Se descarta como duplicado exacto |

Si tu biblioteca son videos de los que tú mismo extraes el audio, basta con importar la carpeta de videos: el portal extrae el audio automáticamente y ambas renditions comparten exactamente la misma pista, por lo que el switch Audio/Video conmuta sin desvío perceptible. Si además importas tus audios extraídos, se fusionan sin duplicar. La misma lógica aplica en la subida del panel admin.

## Pruebas

Con el servidor corriendo:

```bash
ADMIN_INITIAL_PASSWORD='TuClaveSegura#2026' python -m pytest tests/ -v
```

Cubren: registro/login/logout, bloqueo tras 5 intentos fallidos, 403 de admin para usuarios normales, Range Requests (206/416), Content-Disposition, escape de LIKE, intentos de inyección SQL, rechazo de subida por magic bytes y protecciones del panel admin.

## Estructura

```
media-portal/
├── server/            # FastAPI: main, database, security, media_processing, routers/
├── static/            # index.html, css/app.css, js/ (app, player, admin, api, ui)
├── media/             # video/, audio/, covers/ (archivos con nombre UUID)
├── tests/             # pytest
├── seed.py            # admin inicial
├── import_media.py    # importador masivo CLI
├── make_samples.py    # contenido de demostración sintético
└── server/schema.sql  # esquema SQLite (idempotente)
```

## Seguridad implementada

- Contraseñas con **argon2id**; política robusta validada en frontend y backend.
- Sesiones server-side de 256 bits (SHA-256 en BD), expiración deslizante de 24 h, token nuevo en cada login (anti session-fixation), invalidación en logout y al suspender usuarios.
- Bloqueo de cuenta 15 min tras 5 intentos fallidos; mensajes de error genéricos.
- 100% SQL parametrizado; `LIKE` con escape de `%`/`_`; orden por lista blanca.
- Autorización por rol verificada en backend en cada endpoint admin (403 real, no solo UI).
- Subidas: lista blanca de extensiones + **magic bytes** + validación ffprobe + renombrado UUID + límite de tamaño + hash anti-duplicados. FFmpeg siempre con lista de argumentos (nunca `shell=True`).
- Anti path-traversal redundante: el cliente solo envía IDs; las rutas de BD se re-validan contra `media/` con `Path.resolve()`.
- Rate limiting en login/registro/descargas; cabeceras `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy` y CSP en HTML.
- Frontend sin `innerHTML` con datos (solo `textContent`/`createElement`); errores amigables con el detalle técnico solo en el log del servidor.

## Despliegue en producción con nginx

El directorio `deploy/` incluye la configuración lista:

```bash
# 1. Instalar el proyecto en /opt/mitube
sudo useradd -r -s /usr/sbin/nologin mitube
sudo mkdir -p /opt/mitube && sudo cp -r . /opt/mitube && cd /opt/mitube
sudo -u mitube python3 -m venv .venv
sudo -u mitube .venv/bin/pip install -r requirements.txt
sudo -u mitube ADMIN_INITIAL_PASSWORD='TuClave#Segura' .venv/bin/python seed.py

# 2. Importar tu biblioteca (como tu usuario, con acceso a ~/inventario)
python import_media.py ~/inventario/multimedia/audio
python import_media.py ~/inventario/multimedia/video
sudo chown -R mitube:mitube /opt/mitube/media /opt/mitube/portal.db*

# 3. Servicio systemd
sudo cp deploy/mitube.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now mitube

# 4. nginx + HTTPS
sudo cp deploy/nginx-mitube.conf /etc/nginx/sites-available/mitube.conf
# Edita server_name y root en el archivo, luego:
sudo ln -s /etc/nginx/sites-available/mitube.conf /etc/nginx/sites-enabled/
sudo apt install certbot python3-certbot-nginx && sudo certbot --nginx -d mitube.tudominio.mx
sudo nginx -t && sudo systemctl reload nginx
```

Detalles ya resueltos en la configuración incluida:

- nginx sirve `static/` directamente y proxya `/api/` a uvicorn en `127.0.0.1:8000` (uvicorn nunca queda expuesto).
- `proxy_buffering off` + `proxy_request_buffering off`: el streaming con Range Requests y las subidas fluyen sin buffering a disco.
- `client_max_body_size 512m` para las subidas del panel admin (el límite de la app es 500 MB).
- `--proxy-headers --forwarded-allow-ips 127.0.0.1` en uvicorn: el rate limiting ve la IP real del cliente vía `X-Forwarded-For`.
- HSTS, `nosniff`, `X-Frame-Options` y `Referrer-Policy` a nivel nginx; la cookie de sesión del frontend añade `Secure` automáticamente bajo HTTPS.
- Unidad systemd endurecida: usuario sin privilegios, `NoNewPrivileges`, `ProtectSystem=full`, `PrivateTmp`.

## fail2ban (capa extra sobre nginx)

El directorio `deploy/fail2ban/` incluye filtros y jails listos:

```bash
sudo apt install fail2ban
sudo cp deploy/fail2ban/filter.d/*.conf /etc/fail2ban/filter.d/
sudo cp deploy/fail2ban/jail.d/mitube.local /etc/fail2ban/jail.d/
# Ajusta ignoreip en mitube.local a tu red local antes de recargar
sudo fail2ban-client reload
sudo fail2ban-client status mitube-auth
```

Jails incluidos:

- **mitube-auth**: fuerza bruta contra `/api/auth/login|register` (respuestas 401/423/429). 8 intentos en 10 min → ban de 1 h, con ban incremental hasta 1 semana. Complementa el bloqueo de cuenta de la app: la app protege la cuenta, fail2ban corta la IP en el firewall.
- **mitube-probe**: escaneo de la API — rachas de 403 sobre `/api/admin` (sondeo del panel) y de 404 sobre `/api/` (enumeración de rutas). 20 en 5 min → ban de 30 min.
- **recidive**: reincidentes de cualquier jail → ban de 1 semana en todos los puertos.

Los filtros están validados contra el formato `combined` de nginx (el predeterminado). Para probarlos con tu log real: `sudo fail2ban-regex /var/log/nginx/access.log /etc/fail2ban/filter.d/mitube-auth.conf`.

## Notas del entorno de vista previa

- En la vista previa desplegada de este entorno, el proxy limita las subidas a 10 MB por petición y la autenticación usa token Bearer (los iframes aislados bloquean cookies).
