import os
from typing import List, Tuple, Optional
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

def _model_candidates() -> List[str]:
    # 1) главное из .env
    primary = os.getenv("OPENROUTER_MODEL", "").strip()
    # 2) фолбэки — менять порядок/набор
    fallbacks = [
        "meta-llama/llama-3.1-8b-instruct:free",
        "google/gemma-2-9b-it:free",
        "nousresearch/hermes-3-llama-3.1-8b:free",
    ]
    out = []
    if primary:
        out.append(primary)
    out.extend([m for m in fallbacks if m and m != primary])
    return out

def _is_region_block(error_text: str) -> bool:
    et = (error_text or "").lower()
    return "not available in your region" in et or "403" in et

def chat_complete(
    system_prompt: str,
    dialog: List[Tuple[str, str]],
    user_text: str,
    model: Optional[str] = None,
    max_tokens: int = 400,
    temperature: float = 0.7,
) -> str:

    # Вызывает OpenRouter; при 403 (регион) — пробует следующую модель.
    # Если клиента/ключа нет — отдаёт заглушку.
    _ensure_client()

    # Собираем сообщения
    messages = [{"role": "system", "content": system_prompt}]
    for role, content in dialog[-10:]:
        messages.append({"role": "assistant" if role == "assistant" else "user", "content": content})
    messages.append({"role": "user", "content": user_text})

    if _client is None:
        # фолбэк-заглушка
        return f"(demo) Ты сказал: “{user_text}”. [{system_prompt}] Мой ответ: держись курса, всё получится! 🚀"

    models_to_try = [model] if model else _model_candidates()

    last_err = None
    for mdl in models_to_try:
        try:
            resp = _client.chat.completions.create(
                model=mdl,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                extra_headers=_extra_headers() or None,
            )
            return (resp.choices[0].message.content or "").strip()
        except Exception as e:
            err = str(e)
            last_err = err
            # если регион-блок — пробуем следующую модель
            if _is_region_block(err):
                continue
            # на другие ошибки — не мучаем пользователя, сразу выходим
            break

    return f"🙈 Не смог получить ответ от модели (попробовали {len(models_to_try)}): {last_err}"

