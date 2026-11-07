"""Generated picture cards: a closing call-to-action card and insert cards made from images
(screenshots, tweets, charts, paintings), as short clips the sequence can place like any shot.

Cards are drawn at the creator's picture shape (4:3 by default) on a plain background, so a boxed
edit shows them exactly like a burned-in screenshot would be."""
from __future__ import annotations

import subprocess
from pathlib import Path

from .util import CACHE, log, probe

FONT_DIR = CACHE / "fonts"
CARD_SECONDS = 8.0


def _font(size: int, weight: int = 600, name: str = "Montserrat.ttf"):
    from PIL import ImageFont
    path = FONT_DIR / name
    if not path.exists():
        return ImageFont.load_default(size=size)
    f = ImageFont.truetype(str(path), size)
    try:
        f.set_variation_by_axes([weight])
    except Exception:   # a static font, or no wght axis
        pass
    return f


def cta_png(lines: list[str], out: Path, size: tuple[int, int] = (1440, 1080), bg: str = "black") -> Path:
    """A text card: the first line large, the rest smaller, centred, like a creator's end card."""
    from PIL import Image, ImageDraw
    w, h = size
    img = Image.new("RGB", (w, h), bg)
    draw = ImageDraw.Draw(img)
    fg = "white" if bg == "black" else "black"
    sizes = [int(h * 0.13)] + [int(h * 0.055)] * (len(lines) - 1)
    fonts = []
    for i, (ln, s) in enumerate(zip(lines, sizes)):   # shrink a long line until it fits the card
        f = _font(s, 700 if i == 0 else 500)
        while s > 12 and draw.textlength(ln, font=f) > w * 0.88:
            s = int(s * 0.92)
            f = _font(s, 700 if i == 0 else 500)
        sizes[i] = s
        fonts.append(f)
    gaps = [int(s * 0.6) for s in sizes]
    heights = [draw.textbbox((0, 0), ln, font=f)[3] for ln, f in zip(lines, fonts)]
    total = sum(heights) + sum(gaps[:-1]) if lines else 0
    y = (h - total) / 2
    for ln, f, hh, g in zip(lines, fonts, heights, gaps):
        tw = draw.textlength(ln, font=f)
        draw.text(((w - tw) / 2, y), ln, font=f, fill=fg)
        y += hh + g
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return out


def still_clip(picture: Path, out: Path, size: tuple[int, int] = (1440, 1080), seconds: float = CARD_SECONDS,
               bg: str = "black", push: float = 0.06, fps: int = 30) -> Path:
    """A picture as a clip at the card shape: fitted inside, plain background, a slow push."""
    if out.exists() and out.stat().st_mtime >= picture.stat().st_mtime:
        return out
    w, h = size
    n = int(seconds * fps)
    vf = (f"scale={w}:{h}:force_original_aspect_ratio=decrease:flags=lanczos,"
          f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color={bg},"
          f"scale={w * 2}:{h * 2}:flags=lanczos,"
          f"zoompan=z='1+{push}*on/{n}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={w}x{h}:fps={fps},"
          "format=yuv420p")
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-framerate", str(fps), "-t", f"{seconds:.2f}",
                    "-i", str(picture), "-vf", vf, "-frames:v", str(n), "-c:v", "libx264", "-crf", "14",
                    "-preset", "fast", "-an", str(out)], check=True, timeout=600)
    return out


def cta_clip(job: Path, text: str, size: tuple[int, int] = (1440, 1080), seconds: float = CARD_SECONDS,
             bg: str = "black") -> Path:
    """The closing card for a job: `text` lines separated by '|'."""
    lines = [ln.strip() for ln in text.split("|") if ln.strip()] or ["."]
    png = job / "work" / "cards" / "cta.png"
    stamp = png.with_suffix(".txt")
    want = f"{text}|{size}|{bg}"
    if not png.exists() or not stamp.exists() or stamp.read_text(encoding="utf-8") != want:
        cta_png(lines, png, size, bg)
        stamp.write_text(want, encoding="utf-8")
        (job / "work" / "cards" / "cta.mp4").unlink(missing_ok=True)
    return still_clip(png, job / "work" / "cards" / "cta.mp4", size, seconds, bg, push=0.0)


def insert_clips(job: Path, pictures: list[Path], size: tuple[int, int] = (1440, 1080),
                 bg: str = "black") -> list[Path]:
    out = []
    for p in pictures:
        if p.suffix.lower() in (".mp4", ".mov", ".mkv", ".webm"):
            out.append(p)       # a clip already
            continue
        clip = job / "work" / "cards" / f"card-{p.stem}.mp4"
        out.append(still_clip(p, clip, size, bg=bg))
    if out:
        log(f"cards: {len(out)} insert clip(s)")
    return out


def card_size(picture_aspect: str | None, canvas: tuple[int, int]) -> tuple[int, int]:
    """Card pixels for the creator's picture shape (4:3, 16:9, 1:1, 9:16) or the canvas shape."""
    shapes = {"4:3": (1440, 1080), "16:9": (1920, 1080), "1:1": (1080, 1080), "4:5": (1080, 1350), "9:16": (1080, 1920)}
    return shapes.get(picture_aspect or "", (canvas[0], canvas[1]))
