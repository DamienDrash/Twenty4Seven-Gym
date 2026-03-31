"""QR code generation for studio check-in links."""

from __future__ import annotations

import io
from base64 import b64encode

import qrcode
import qrcode.image.svg
from PIL import Image  # type: ignore[import-untyped]


def generate_qr_data_uri(url: str) -> str:
    """Return a data:image/svg+xml;base64,… URI for the given URL."""
    image = qrcode.make(url, image_factory=qrcode.image.svg.SvgImage)
    return f"data:image/svg+xml;base64,{b64encode(image.to_string()).decode('ascii')}"


def generate_qr_png_bytes(url: str, box_size: int = 10) -> bytes:
    """Return raw PNG bytes for a QR code."""
    img: Image.Image = qrcode.make(url, box_size=box_size, border=4)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
