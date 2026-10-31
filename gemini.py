#!/usr/bin/env python3
"""Gemini — только для того, что не решается правилами.

Принцип: детерминированный код делает всё, что может. Модель зовётся в трёх
случаях — неоднозначный тип сайта, неизвестный селектор, неизвестная ошибка
сборки. Каждый ответ кешируется, поэтому повторная ошибка денег не стоит.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

FORGE_ROOT = Path(__file__).resolve().parent
CACHE_DIR = FORGE_ROOT / "knowledge" / "ai_cache"
MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
FALLBACK_MODEL = "gemini-2.0-flash"
ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
MAX_CALLS = int(os.environ.get("GEMINI_MAX_CALLS", "40"))

_calls = 0


def api_key() -> str:
    """Ключ: переменная окружения, затем файлы рядом с генератором."""
    for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    for path in (Path.home() / ".gemini_key", FORGE_ROOT / ".gemini_key"):
        try:
            value = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if value:
            return value
    return ""


def available() -> bool:
    return bool(api_key())


def status() -> str:
    if not available():
        return "Gemini: ключа нет — работаю только на правилах"
    return f"Gemini: {MODEL}, вызовов в этом прогоне {_calls}/{MAX_CALLS}"


def _cache_path(prompt: str) -> Path:
    digest = hashlib.sha256((MODEL + "\x00" + prompt).encode("utf-8")).hexdigest()
    return CACHE_DIR / f"{digest}.json"


def _post(model: str, key: str, payload: dict) -> dict:
    request = urllib.request.Request(
        ENDPOINT.format(model=model, key=key),
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        return json.loads(response.read().decode("utf-8"))


def ask(prompt: str, *, max_tokens: int = 1200, temperature: float = 0.2, use_cache: bool = True) -> str:
    """Один короткий вопрос. Пустая строка, если модель недоступна."""
    global _calls
    key = api_key()
    if not key:
        return ""

    cache_file = _cache_path(prompt + f"|{max_tokens}")
    if use_cache and cache_file.exists():
        try:
            return json.loads(cache_file.read_text(encoding="utf-8"))["text"]
        except (OSError, ValueError, KeyError):
            pass

    if _calls >= MAX_CALLS:
        print(f"  ⚠ Gemini: лимит вызовов на прогон ({MAX_CALLS}) исчерпан")
        return ""

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
    }
    text = ""
    for model in (MODEL, FALLBACK_MODEL):
        try:
            _calls += 1
            data = _post(model, key, payload)
            candidates = data.get("candidates") or []
            if not candidates:
                continue
            parts = (candidates[0].get("content") or {}).get("parts") or []
            text = "".join(part.get("text", "") for part in parts).strip()
            if text:
                break
        except urllib.error.HTTPError as exc:
            # 400/403 — ключ или модель; 429 — квота. Второй раз не пробуем.
            print(f"  ⚠ Gemini {model}: HTTP {exc.code}")
            if exc.code in (400, 403, 429):
                return ""
        except Exception as exc:  # сеть, таймаут
            print(f"  ⚠ Gemini {model}: {exc}")
            continue

    if text and use_cache:
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(
                json.dumps({"ts": int(time.time()), "model": MODEL, "text": text}, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError:
            pass
    return text


def ask_json(prompt: str, *, max_tokens: int = 1200) -> dict:
    """Ответ строго в JSON: модель просят вернуть объект без пояснений."""
    raw = ask(prompt + "\n\nОтветь только JSON-объектом, без markdown и пояснений.", max_tokens=max_tokens)
    if not raw:
        return {}
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1] if "```" in raw[3:] else raw
        raw = raw.split("\n", 1)[-1] if raw.lstrip().startswith(("json", "JSON")) else raw
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end < start:
        return {}
    try:
        return json.loads(raw[start:end + 1])
    except ValueError:
        return {}


if __name__ == "__main__":
    print(status())
    if available():
        print(ask("Ответь одним словом: работает?", max_tokens=20))
