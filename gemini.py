#!/usr/bin/env python3
"""Gemini — только для того, что не решается правилами.

Принцип: детерминированный код делает всё, что может. Модель зовётся в трёх
случаях — неоднозначный тип сайта, неизвестный селектор, неизвестная ошибка
сборки. Каждый ответ кешируется, поэтому повторная ошибка денег не стоит.

Транспорт — curl: он умеет socks5, а VPS ходит в Google только через VPN
(напрямую отвечает «User location is not supported»).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

FORGE_ROOT = Path(__file__).resolve().parent
CACHE_DIR = FORGE_ROOT / "knowledge" / "ai_cache"
ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

# Порядок важен: первый рабочий и используется. 3.8-flash отвечает 503 под
# нагрузкой, 2.5-flash закрыт для новых ключей.
MODELS = [m.strip() for m in os.environ.get(
    "GEMINI_MODELS", "gemini-3-flash-preview,gemini-flash-lite-latest,gemini-3.8-flash"
).split(",") if m.strip()]
MAX_CALLS = int(os.environ.get("GEMINI_MAX_CALLS", "40"))

_calls = 0
_cooldown: dict[str, float] = {}


def api_key() -> str:
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


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1.5):
            return True
    except OSError:
        return False


def proxy() -> str:
    """Прокси для Google: явный GEMINI_PROXY или локальный socks от VPN."""
    explicit = os.environ.get("GEMINI_PROXY", "").strip()
    if explicit:
        return explicit
    if os.environ.get("GEMINI_DIRECT") == "1":
        return ""
    if _port_open("127.0.0.1", 1080):
        return "socks5h://127.0.0.1:1080"
    return ""


def available() -> bool:
    return bool(api_key())


def status() -> str:
    if not available():
        return "Gemini: ключа нет — работаю только на правилах"
    path = proxy() or "напрямую"
    return f"Gemini: {MODELS[0]} через {path}, вызовов {_calls}/{MAX_CALLS}"


def _cache_path(prompt: str) -> Path:
    digest = hashlib.sha256((MODELS[0] + "\x00" + prompt).encode("utf-8")).hexdigest()
    return CACHE_DIR / f"{digest}.json"


def _post(model: str, key: str, payload: dict, timeout: int = 120) -> dict:
    url = ENDPOINT.format(model=model) + "?key=" + key
    body = json.dumps(payload).encode("utf-8")
    use_proxy = proxy()

    if use_proxy and shutil.which("curl"):
        command = ["curl", "-sS", "--max-time", str(timeout), "--proxy", use_proxy,
                   "-X", "POST", "-H", "Content-Type: application/json",
                   "--data-binary", "@-", url]
        result = subprocess.run(command, input=body, capture_output=True)
        raw = result.stdout.decode("utf-8", "replace")
    else:
        request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
        opener = urllib.request.urlopen
        if use_proxy:
            handlers = []
            if use_proxy.startswith("socks"):
                try:
                    import socks  # PySocks
                    import sockshandler

                    host, port = use_proxy.split("://", 1)[1].split(":")
                    handlers.append(sockshandler.SocksiPyHandler(socks.SOCKS5, host, int(port), True))
                except ImportError:
                    pass
            if handlers:
                opener = urllib.request.build_opener(*handlers).open
        with opener(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", "replace")

    try:
        data = json.loads(raw)
    except ValueError:
        return {"error": {"message": raw[:200]}}
    return data


def _text_from(data: dict) -> str:
    candidates = data.get("candidates") or []
    if not candidates:
        return ""
    parts = (candidates[0].get("content") or {}).get("parts") or []
    return "".join(part.get("text", "") for part in parts).strip()


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
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }

    for model in MODELS:
        if _cooldown.get(model, 0) > time.time():
            continue
        for attempt in range(2):
            try:
                _calls += 1
                data = _post(model, key, payload)
            except Exception as exc:
                print(f"  ⚠ Gemini {model}: {str(exc)[:80]}")
                break

            text = _text_from(data)
            candidate = (data.get("candidates") or [{}])[0]
            if not text and candidate.get("finishReason") == "MAX_TOKENS" and attempt == 0:
                payload["generationConfig"]["maxOutputTokens"] = min(4096, max_tokens * 3)
                continue
            if text:
                if use_cache:
                    try:
                        CACHE_DIR.mkdir(parents=True, exist_ok=True)
                        cache_file.write_text(
                            json.dumps({"ts": int(time.time()), "model": model, "text": text}, ensure_ascii=False),
                            encoding="utf-8",
                        )
                    except OSError:
                        pass
                return text

            error = (data.get("error") or {})
            message = str(error.get("message", ""))
            if error.get("code") == 429 or "quota" in message.lower():
                # Квота на модель кончилась: минуту к ней не ходим.
                _cooldown[model] = time.time() + 120
                break
            # 503 — перегрузка: одна пауза, потом следующая модель.
            if "high demand" in message or error.get("code") == 503:
                if attempt == 0:
                    time.sleep(3)
                    continue
                break
            print(f"  ⚠ Gemini {model}: {message[:90]}")
            break
    return ""


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
