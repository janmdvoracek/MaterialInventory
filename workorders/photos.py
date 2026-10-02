"""Shrinking an uploaded job photo before it is stored.

The server has a small disk and a phone photo is 2-6 MB, so every upload is
re-encoded here — whichever form it came through — to a JPEG no longer than
`PHOTO_MAX_SIDE` on its long side, which lands around 200-400 KB. The original
is not kept. Re-encoding also drops the EXIF block, GPS position included,
which nothing in the app reads and a photo of a depot need not carry.
"""

from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image, ImageOps
from pillow_heif import register_heif_opener

# HEIC is what an iPhone shoots by default, and Pillow cannot open it alone.
register_heif_opener()

# Enough to read a delivery note photographed whole; a phone shoots 4000+.
PHOTO_MAX_SIDE = 1600
PHOTO_JPEG_QUALITY = 80


class UnreadablePhoto(Exception):
    """The upload carries a photo's extension but does not decode as one."""


def shrink_photo(upload):
    """Return `upload` re-encoded as a small JPEG, as a fresh upload.

    An `UploadedFile` and not a `ContentFile`, because that is what everything
    downstream tells a new photo by (`views._apply_photo`). Raises
    `UnreadablePhoto` for anything Pillow cannot decode — a renamed PDF, a
    truncated file, or a picture past Pillow's decompression-bomb limit, which
    would otherwise be hundreds of MB of RAM on the server.
    """
    try:
        with Image.open(upload) as image:
            # JPEG only (a no-op for anything else): decode at a reduced scale
            # straight away rather than at full size and then throw most away.
            image.draft('RGB', (PHOTO_MAX_SIDE, PHOTO_MAX_SIDE))
            # A phone stores a portrait shot sideways plus an EXIF flag saying
            # so; the flag goes with the EXIF block, so the pixels turn now.
            image = ImageOps.exif_transpose(image)
            image.thumbnail((PHOTO_MAX_SIDE, PHOTO_MAX_SIDE))
            image = _flatten(image)
            buffer = BytesIO()
            image.save(buffer, 'JPEG', quality=PHOTO_JPEG_QUALITY, optimize=True)
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise UnreadablePhoto from exc
    return SimpleUploadedFile('photo.jpg', buffer.getvalue(), content_type='image/jpeg')


def _flatten(image):
    """RGB for JPEG: transparency (a PNG screenshot) goes onto white, not black."""
    if image.mode in ('RGBA', 'LA') or (image.mode == 'P' and 'transparency' in image.info):
        image = image.convert('RGBA')
        background = Image.new('RGB', image.size, 'white')
        background.paste(image, mask=image.getchannel('A'))
        return background
    return image.convert('RGB')
