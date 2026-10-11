"""Shrinking an uploaded job photo before it is stored.

The disk is small and phone photos are 2-6 MB, so every upload is re-encoded
to a JPEG of at most `PHOTO_MAX_SIDE` px (around 200-400 KB). This also drops
EXIF, including GPS. The original is not kept.
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
    """Return `upload` re-encoded as a small JPEG, as a new `UploadedFile`.

    An `UploadedFile`, because `clean_photo_upload` tells new uploads apart by
    that type. Raises `UnreadablePhoto` for anything Pillow can't decode,
    including images past its decompression-bomb limit.
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
