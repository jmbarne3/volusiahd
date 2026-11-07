"""A logo at the size the page shows it.

The program page draws a logo no wider than 160px, and providers upload
whatever they have — often a phone photo of a sign, 3000px wide and over a
megabyte. So every logo gets a second, smaller copy for the page, and the
original stays as it was for social previews and for whatever the layout
wants next.

Like `geocoding.py`, this does not touch models or the database. It turns the
bytes of an image into the bytes of a smaller one, and decides nothing about
when that should happen.
"""

import io
from pathlib import PurePosixPath

from django.core.files.base import ContentFile
from PIL import Image, ImageOps, UnidentifiedImageError

# Twice the 160px the page draws (`.program .logo` in site.css), so the logo is
# still sharp on a high-density screen.
DISPLAY_WIDTH = 320

JPEG_QUALITY = 85


class NotAnImage(Exception):
    """The file could not be read as an image. The original is left alone."""


def display_copy(data, name):
    """A copy of `data` no wider than DISPLAY_WIDTH, as a ContentFile.

    Never upscaled: a logo already narrower than that is re-encoded at its own
    size. A logo with any transparency stays a PNG — flattening one to JPEG
    puts it on a black box. Everything else becomes a JPEG, which is what
    makes a photo of a sign small.
    """
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise NotAnImage(str(exc)) from None

    # Phone photos are often stored sideways with a note saying which way is
    # up. Apply the note, because the copy will not carry it.
    image = ImageOps.exif_transpose(image)

    if image.width > DISPLAY_WIDTH:
        height = max(1, round(image.height * DISPLAY_WIDTH / image.width))
        image = image.resize((DISPLAY_WIDTH, height), Image.Resampling.LANCZOS)

    buffer = io.BytesIO()
    if _has_transparency(image):
        image.convert("RGBA").save(buffer, format="PNG", optimize=True)
        suffix = ".png"
    else:
        image.convert("RGB").save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True)
        suffix = ".jpg"

    return ContentFile(buffer.getvalue(), name=PurePosixPath(name).stem + suffix)


def _has_transparency(image):
    if image.mode in ("RGBA", "LA", "PA"):
        return image.getextrema()[-1][0] < 255
    return image.mode == "P" and "transparency" in image.info
