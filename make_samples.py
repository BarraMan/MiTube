"""Genera contenido de demostración: videos y audios sintéticos con FFmpeg,
portadas con PIL, y los registra en la BD usando el mismo pipeline del portal."""
import math
import os
import subprocess
import sys
import uuid
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PIL import Image, ImageDraw, ImageFilter  # noqa: E402

from server.database import execute, init_db, q1  # noqa: E402
from server.media_processing import (  # noqa: E402
    AUDIO_DIR, COVER_DIR, VIDEO_DIR, duration_of, ensure_dirs, extract_audio,
    ffprobe_info, rel_media, sha256_of,
)
from server.library import get_or_create_album, get_or_create_artist  # noqa: E402

DUR = 24

# (titulo, artista, album, año, genero, tiene_video, fuente_video, notas_midi, paleta)
SAMPLES = [
    ("Neón Nocturno", "Circuito Austral", "Ciudad Fractal", 2024, "Synthwave", True,
     "gradients=s=1280x720:speed=0.03:c0=0x0f0f23:c1=0xff2d78:c2=0x1fb6ff", [57, 60, 64, 67], ("#ff2d78", "#1a0b2e")),
    ("Motor de Iones", "Circuito Austral", "Ciudad Fractal", 2024, "Synthwave", True,
     "gradients=s=1280x720:speed=0.05:c0=0x090918:c1=0x00e5a0:c2=0x6157ff", [45, 52, 57, 60], ("#00e5a0", "#090918")),
    ("Bruma de Cobre", "Vera Lumen", "Estelas", 2023, "Ambient", True,
     "gradients=s=1280x720:speed=0.02:c0=0x241a10:c1=0xd98e32:c2=0x7a3803", [48, 55, 62, 67], ("#d98e32", "#241a10")),
    ("Jardín Binario", "Vera Lumen", "Estelas", 2023, "Ambient", False,
     None, [50, 57, 64, 69], ("#79d98e", "#0e2416")),
    ("Ruta 57 Sur", "Los Voltios", "Kilómetro Cero", 2022, "Rock", True,
     "gradients=s=1280x720:speed=0.08:c0=0x1c0b0b:c1=0xe23636:c2=0xffb020", [40, 47, 52, 55], ("#e23636", "#1c0b0b")),
    ("Asfalto y Sal", "Los Voltios", "Kilómetro Cero", 2022, "Rock", False,
     None, [43, 50, 55, 59], ("#ffb020", "#241505")),
    ("Marea Interior", "Selva MX", "Raíces", 2025, "Electrónica", True,
     "gradients=s=1280x720:speed=0.04:c0=0x061a26:c1=0x1fb6ff:c2=0x0a5c8c", [52, 59, 64, 68], ("#1fb6ff", "#061a26")),
    ("Pulso de Obsidiana", "Selva MX", "Raíces", 2025, "Electrónica", True,
     "gradients=s=1280x720:speed=0.06:c0=0x14061f:c1=0x9b5cff:c2=0xff2d78", [50, 53, 57, 62], ("#9b5cff", "#14061f")),
]


def midi_hz(n: int) -> float:
    return 440.0 * (2 ** ((n - 69) / 12))


def audio_filter(notes: list[int]) -> str:
    """Arpegio simple: 4 notas en bucle con envolvente, señal musical audible."""
    exprs = []
    for i, n in enumerate(notes):
        f = midi_hz(n)
        # Equivalente a mod(t+off,2) sin usar comas (rompen el parser de aevalsrc)
        off = i * 0.5
        m = f"((t+{off})-2*floor((t+{off})/2))"
        exprs.append(
            f"0.22*sin(2*PI*{f:.2f}*t)*(0.5+0.5*sin(2*PI*0.5*t+{i}*PI/2))*exp(-1.5*{m})"
        )
    return f"aevalsrc={'+'.join(exprs)}:d={DUR}:s=44100"


def make_cover(title: str, artist: str, colors: tuple[str, str]) -> Path:
    accent, bg = colors
    img = Image.new("RGB", (640, 640), bg)
    d = ImageDraw.Draw(img)
    ac = tuple(int(accent[i:i + 2], 16) for i in (1, 3, 5))
    bgc = tuple(int(bg[i:i + 2], 16) for i in (1, 3, 5))
    for y in range(640):
        t = y / 640
        col = tuple(int(bgc[k] + (ac[k] - bgc[k]) * t * 0.55) for k in range(3))
        d.line([(0, y), (640, y)], fill=col)
    # Anillos concéntricos estilo vinilo
    cx, cy = 320, 300
    for r in range(60, 260, 22):
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=ac, width=2)
    d.ellipse([cx - 26, cy - 26, cx + 26, cy + 26], fill=ac)
    img = img.filter(ImageFilter.GaussianBlur(0.4))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 500, 640, 640], fill=(0, 0, 0))
    try:
        from PIL import ImageFont
        font_b = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 36)
        font_s = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 24)
    except Exception:
        font_b = font_s = None
    d.text((32, 526), title, fill="#ffffff", font=font_b)
    d.text((32, 580), artist, fill=accent, font=font_s)
    out = COVER_DIR / f"{uuid.uuid4().hex}.jpg"
    img.save(out, quality=88)
    return out


def run(args: list[str]) -> None:
    subprocess.run(["ffmpeg", "-y", "-v", "error", *args], check=True, timeout=300)


def main() -> None:
    init_db()
    ensure_dirs()
    genres = {}
    for *_, g, _hv, _src, _n, _c in [(0, 0, 0, 0, s[4], s[5], s[6], s[7], s[8]) for s in SAMPLES]:
        pass
    for s in SAMPLES:
        gname = s[4]
        if gname not in genres:
            row = q1("SELECT id FROM genres WHERE name = ?", (gname,))
            genres[gname] = row["id"] if row else execute("INSERT INTO genres (name) VALUES (?)", (gname,))

    for title, artist, album, year, genre, has_video, vsrc, notes, colors in SAMPLES:
        if q1("SELECT t.id FROM tracks t JOIN artists a ON a.id=t.artist_id WHERE t.title=? AND a.name=?",
              (title, artist)):
            print(f"Ya existe: {title}")
            continue
        af = audio_filter(notes)
        video_path = None
        if has_video:
            vfile = VIDEO_DIR / f"{uuid.uuid4().hex}.mp4"
            run(["-f", "lavfi", "-i", vsrc, "-f", "lavfi", "-i", af,
                 "-t", str(DUR), "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
                 "-c:a", "aac", "-b:a", "160k", "-shortest", str(vfile)])
            audio_file = extract_audio(vfile)
            if audio_file is None:
                raise RuntimeError(f"extract_audio falló para {title}")
            video_path, audio_rel = rel_media(vfile), rel_media(audio_file)
            hash_src = vfile
        else:
            afile = AUDIO_DIR / f"{uuid.uuid4().hex}.m4a"
            run(["-f", "lavfi", "-i", af, "-t", str(DUR), "-c:a", "aac", "-b:a", "160k", str(afile)])
            audio_rel = rel_media(afile)
            hash_src = afile
        info = ffprobe_info(hash_src)
        cover = make_cover(title, artist, colors)
        artist_id = get_or_create_artist(artist)
        album_id = get_or_create_album(album, artist_id, year)
        h = sha256_of(hash_src)
        # Semántica de hashes: file_hash = audio original; video_hash = video original
        file_hash = None if has_video else h
        video_hash = h if has_video else None
        execute(
            """INSERT INTO tracks (title, artist_id, album_id, genre_id, year, duration_seconds,
               video_path, audio_path, cover_path, file_hash_sha256, video_hash_sha256, audio_extracted)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (title, artist_id, album_id, genres[genre], year, duration_of(info) if info else DUR,
             video_path, audio_rel, rel_media(cover), file_hash, video_hash, 1 if has_video else 0),
        )
        print(f"Creado: {title} ({'video+audio' if has_video else 'solo audio'})")


if __name__ == "__main__":
    main()
