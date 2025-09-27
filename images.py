import os
import base64
from io import BytesIO
from typing import Tuple, Optional
from openai import OpenAI

BASE_URL = "https://openrouter.ai/api/v1"

_client: Optional[OpenAI] = None

def _ensure_client():
    global _client
    if _client is None:
        api_key = os.getenv("OPENROUTER_API_KEY")
        if api_key:
            _client = OpenAI(base_url=BASE_URL, api_key=api_key)

def available() -> bool:
    _ensure_client()
    return _client is not None

def _extra_headers() -> dict:
    h = {}
    site = os.getenv("OPENROUTER_SITE")
    title = os.getenv("OPENROUTER_TITLE")
    if site: h["HTTP-Referer"] = site
    if title: h["X-Title"] = title
    return h

def generate_image(prompt: str, size: str = "1024x1024") -> Tuple[str, bytes]:

    # Возвращает (filename, image_bytes). Бросает исключение при ошибке.

    _ensure_client()
    if _client is None:
        raise RuntimeError("Image SDK not configured")

    model = os.getenv("OPENROUTER_IMAGE_MODEL", "black-forest-labs/flux-1-schnell:free")
    resp = _client.images.generate(
        model=model,
        prompt=prompt,
        size=size,
        extra_headers=_extra_headers() or None,
    )
    b64 = resp.data[0].b64_json
    img_bytes = base64.b64decode(b64)
    # имя файла
    return ("image.png", img_bytes)
