"""Validate raster images and atomically write bounded WebP covers."""
from __future__ import annotations

import hashlib
import io
import os
import tempfile
from urllib.parse import urlsplit

from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError

from .http import FetchError

MAX_IMAGE_EDGE = 640
WEBP_QUALITY = 80

def _font(size: int, bold: bool = False):
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        if os.path.isfile(path):
            try:
                return ImageFont.truetype(path, size=size)
            except OSError:
                continue
    return ImageFont.load_default(size=size)


def _site_tile(media_id: str, url: str, cache_dir: str) -> tuple[str, int, int]:
    """Create a restrained local fallback that remains recognizable in a grid."""
    width, height = 640, 360
    domain = (urlsplit(url).hostname or "bookmark").lower()
    display_domain = domain.removeprefix("www.")[:42]
    digest = hashlib.sha1(domain.encode("utf-8", "ignore")).digest()
    accents = [(21, 135, 119), (194, 82, 61), (214, 156, 52), (52, 105, 163)]
    accent = accents[digest[0] % len(accents)]
    background = (244, 242, 236)
    ink = (28, 31, 34)
    muted = (95, 99, 102)

    image = Image.new("RGB", (width, height), background)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, width, 12), fill=accent)
    draw.rectangle((40, 46, 116, 122), fill=ink)
    initials = "".join(part[0] for part in display_domain.split(".") if part)[:2].upper() or "B"
    initials_font = _font(34, bold=True)
    box = draw.textbbox((0, 0), initials, font=initials_font)
    draw.text(
        (78 - (box[2] - box[0]) / 2, 84 - (box[3] - box[1]) / 2 - box[1]),
        initials,
        font=initials_font,
        fill=(255, 255, 255),
    )
    draw.text((40, 166), "BOOKMARK COVER", font=_font(15, bold=True), fill=accent)
    draw.text((40, 205), display_domain, font=_font(34, bold=True), fill=ink)
    draw.line((40, 288, 600, 288), fill=(207, 205, 199), width=1)
    draw.text((40, 306), "Flowinone local fallback", font=_font(16), fill=muted)

    os.makedirs(cache_dir, exist_ok=True)
    target_path = os.path.abspath(os.path.join(cache_dir, f"{media_id}.webp"))
    handle = tempfile.NamedTemporaryFile(prefix=f".{media_id}-", suffix=".webp", dir=cache_dir, delete=False)
    temp_path = handle.name
    handle.close()
    try:
        image.save(temp_path, format="WEBP", quality=WEBP_QUALITY, method=6)
        os.replace(temp_path, target_path)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
    return target_path, width, height


def _convert_to_webp(media_id: str, payload: bytes, cache_dir: str, *, min_width: int = 80) -> tuple[str, int, int]:
    try:
        with Image.open(io.BytesIO(payload)) as source:
            source.load()
            image = ImageOps.exif_transpose(source)
            if image.width < min_width or image.height < 60:
                raise FetchError(f"image dimensions are too small: {image.width}x{image.height}")
            if image.mode in {"RGBA", "LA"} or (image.mode == "P" and "transparency" in image.info):
                rgba = image.convert("RGBA")
                flattened = Image.new("RGB", rgba.size, (244, 242, 236))
                flattened.paste(rgba, mask=rgba.getchannel("A"))
                image = flattened
            else:
                image = image.convert("RGB")
            image.thumbnail((MAX_IMAGE_EDGE, MAX_IMAGE_EDGE), Image.Resampling.LANCZOS)
            width, height = image.size

            os.makedirs(cache_dir, exist_ok=True)
            target_path = os.path.abspath(os.path.join(cache_dir, f"{media_id}.webp"))
            handle = tempfile.NamedTemporaryFile(prefix=f".{media_id}-", suffix=".webp", dir=cache_dir, delete=False)
            temp_path = handle.name
            handle.close()
            try:
                image.save(temp_path, format="WEBP", quality=WEBP_QUALITY, method=6)
                os.replace(temp_path, target_path)
            finally:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
            return target_path, width, height
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise FetchError("response was not a valid raster image") from exc
