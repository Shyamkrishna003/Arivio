"""
Uploaded label image handling.

Every uploaded image is decoded, downscaled and re-encoded before it is stored
or sent anywhere. That does four things at once:

  - Validates it. A file is an image because Pillow could decode it, not
    because the client said so in a Content-Type header.

  - Strips metadata. Phone cameras write EXIF, and EXIF routinely carries GPS
    coordinates. A user photographing a product in their kitchen must not
    have their home location travel with it to a third-party vision API, to
    our disk, or to any other user who later sees the product image.
    Re-encoding from raw pixels drops every EXIF tag, including that one.

  - Bounds the cost. Label text stays legible far below full sensor
    resolution, so downscaling cuts the vision-model bill and the round trip
    without hurting the read.

  - Normalises the format. One encoder, one MIME type downstream.

Files are named by content hash, so re-uploading the same photo overwrites
rather than accumulates, and the name leaks nothing about who uploaded it.
"""

import hashlib
import io
import os
from dataclasses import dataclass
from typing import Optional

from PIL import Image, ImageOps

from app.core.config import get_settings

settings = get_settings()

# Formats Pillow must be able to decode. HEIC is deliberately absent: iPhones
# shoot it by default but Pillow cannot read it without a plugin, so it is
# rejected with a clear message rather than failing obscurely later.
ACCEPTED_MIME = {
    "image/jpeg", "image/jpg", "image/png",
    "image/webp", "image/gif", "image/bmp", "image/tiff",
}

STORED_FORMAT = "JPEG"
STORED_EXTENSION = "jpg"
STORED_MIME = "image/jpeg"
STORED_QUALITY = 85


class ImageRejected(Exception):
    """The upload is not a usable image. The message is shown to the user."""


class ImageStorageUnavailable(Exception):
    """
    The image is fine but we could not store it — the upload directory is
    missing, unwritable or full.

    Distinct from ImageRejected because it is our fault, not the user's: it
    must not tell someone their photo was bad, and it maps to a 503 rather
    than a 422.

    The common cause is ownership. docker-compose bind-mounts ./backend into
    the container, which runs as root, so an uploads/ directory first created
    by the container is root-owned — and a later local `uvicorn` run as your
    own user then cannot write to it.
    """


@dataclass
class StoredImage:
    """A normalised image, on disk and in memory."""
    sha256: str
    # Ready to send to a vision model without re-reading from disk.
    data: bytes
    mime: str
    url_path: str
    width: int
    height: int


def _upload_dir() -> str:
    path = os.path.abspath(settings.UPLOAD_DIR)
    try:
        os.makedirs(path, exist_ok=True)
    except OSError as e:
        raise ImageStorageUnavailable(
            f"The upload directory {path} could not be created: {e}"
        ) from e
    return path


def normalize_image(raw: bytes, declared_mime: Optional[str] = None) -> tuple[bytes, int, int]:
    """
    Decode, orient, downscale and re-encode. Raises ImageRejected on anything
    that is not a decodable image.

    Returns (jpeg_bytes, width, height).
    """
    if not raw:
        raise ImageRejected("The uploaded file is empty.")

    limit_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    if len(raw) > limit_bytes:
        raise ImageRejected(
            f"That image is larger than the {settings.MAX_UPLOAD_SIZE_MB}MB limit. "
            "Try a photo taken at a lower resolution."
        )

    if declared_mime and declared_mime.lower() not in ACCEPTED_MIME:
        raise ImageRejected(
            f"'{declared_mime}' images aren't supported. "
            "Use JPEG, PNG or WebP — on iPhone, set Camera → Formats to "
            "'Most Compatible', or share the photo rather than the original file."
        )

    try:
        image = Image.open(io.BytesIO(raw))
        # Pillow is lazy; force a decode so a truncated or corrupt file fails
        # here rather than halfway through the resize below.
        image.load()
    except Exception as e:  # noqa: BLE001 — Pillow raises many unrelated types
        raise ImageRejected(
            "That file could not be read as an image. If it came from an "
            "iPhone it may be HEIC, which we can't decode yet."
        ) from e

    # Apply the EXIF orientation tag, then discard it with the rest of the
    # metadata. Without this a portrait photo reaches the model rotated 90°,
    # and rotated label text reads far worse.
    image = ImageOps.exif_transpose(image)

    # JPEG has no alpha channel, and a palette image would encode poorly.
    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")

    max_edge = settings.OCR_MAX_IMAGE_EDGE
    if max(image.size) > max_edge:
        image.thumbnail((max_edge, max_edge), Image.LANCZOS)

    buffer = io.BytesIO()
    # A fresh image object built from pixels only — no EXIF, no ICC, no
    # thumbnail, nothing that came in with the upload.
    image.save(buffer, format=STORED_FORMAT, quality=STORED_QUALITY, optimize=True)
    return buffer.getvalue(), image.width, image.height


def stored_image_url(image_id: str) -> Optional[str]:
    """
    Turn a client-supplied image id back into a URL, or None.

    The id is a content hash the client got from an upload, and it ends up in
    `Product.image_url` — so it is validated rather than trusted. Two checks:

      - it must be exactly 64 hex characters, which rules out `../` and every
        other way of naming a file outside the upload directory
      - the file must actually exist, so a product cannot be given an
        image_url pointing at nothing

    Without the first check a caller could set a product's image to any path
    on the server that the static mount can reach.
    """
    if not image_id or len(image_id) != 64:
        return None
    if any(c not in "0123456789abcdef" for c in image_id.lower()):
        return None

    filename = f"{image_id.lower()}.{STORED_EXTENSION}"
    if not os.path.exists(os.path.join(_upload_dir(), filename)):
        return None
    return f"/uploads/{filename}"


def store_image(raw: bytes, declared_mime: Optional[str] = None) -> StoredImage:
    """Normalise an upload and write it under UPLOAD_DIR, named by content hash."""
    data, width, height = normalize_image(raw, declared_mime)

    # Hash the normalised bytes, not the upload: the same photo re-encoded by
    # a different client should land on the same cache entry.
    digest = hashlib.sha256(data).hexdigest()
    filename = f"{digest}.{STORED_EXTENSION}"
    path = os.path.join(_upload_dir(), filename)

    # Content-addressed, so an existing file with this name is byte-identical
    # and rewriting it would be pure work.
    if not os.path.exists(path):
        # Write-then-rename, so a crash mid-write cannot leave a truncated
        # image at a name that later reads as complete.
        tmp_path = f"{path}.{os.getpid()}.tmp"
        try:
            with open(tmp_path, "wb") as f:
                f.write(data)
            os.replace(tmp_path, path)
        except OSError as e:
            # Leave nothing half-written behind for the next request to find.
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise ImageStorageUnavailable(
                f"Could not write to {os.path.dirname(path)}: {e}"
            ) from e

    return StoredImage(
        sha256=digest,
        data=data,
        mime=STORED_MIME,
        url_path=f"/uploads/{filename}",
        width=width,
        height=height,
    )
