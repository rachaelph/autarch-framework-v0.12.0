"""Image references for multimodal (vision) model providers.

autarch's model seam is text-first (``ModelProvider.complete``), but vision-capable providers
also implement ``complete_vision(prompt, images, system)`` — where each image is an :class:`ImageRef`.
An ImageRef can point at a URL, a local file, or raw bytes; ``to_data_uri()`` renders it into the
form chat/vision APIs accept (an http(s) URL as-is, else a base64 ``data:`` URI).
"""
from __future__ import annotations

import base64
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence


@dataclass
class ImageRef:
    """A reference to an image for a vision provider: a URL, a local file path, or raw bytes.

    ``detail`` is an OpenAI/Azure vision hint ("auto" | "low" | "high"). Build one with the
    ``from_path`` / ``from_url`` / ``from_bytes`` helpers.
    """

    url: Optional[str] = None      # http(s) URL or an already-formed data: URI
    path: Optional[str] = None     # local file path
    data: Optional[bytes] = None   # raw image bytes
    mime: Optional[str] = None     # e.g. "image/png"; inferred from the path when omitted
    detail: str = "auto"

    @classmethod
    def from_path(cls, path, *, detail: str = "auto") -> "ImageRef":
        return cls(path=str(path), detail=detail)

    @classmethod
    def from_url(cls, url, *, detail: str = "auto") -> "ImageRef":
        return cls(url=str(url), detail=detail)

    @classmethod
    def from_bytes(cls, data: bytes, *, mime: str = "image/png", detail: str = "auto") -> "ImageRef":
        return cls(data=bytes(data), mime=mime, detail=detail)

    def to_data_uri(self) -> str:
        """A URL usable as a chat ``image_url``: the http(s)/data URL as-is, else a base64 data URI
        built from the local file or raw bytes."""
        if self.url:
            return self.url
        raw, mime = self.data, self.mime
        if raw is None and self.path:
            p = Path(self.path)
            raw = p.read_bytes()
            mime = mime or mimetypes.guess_type(p.name)[0] or "image/png"
        if raw is None:
            raise ValueError("ImageRef has no url, path, or data")
        b64 = base64.b64encode(raw).decode("ascii")
        return f"data:{mime or 'image/png'};base64,{b64}"


def openai_vision_content(prompt: str, images: Sequence[ImageRef]) -> List[dict]:
    """Build the ``content`` parts for an OpenAI/Azure chat message: one text part plus one
    ``image_url`` part per image. Shared by the Azure and MAF vision paths."""
    parts: List[dict] = [{"type": "text", "text": prompt or ""}]
    for img in images or ():
        parts.append({
            "type": "image_url",
            "image_url": {"url": img.to_data_uri(), "detail": getattr(img, "detail", "auto") or "auto"},
        })
    return parts
