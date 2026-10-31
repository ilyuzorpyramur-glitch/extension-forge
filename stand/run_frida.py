#!/usr/bin/env python3
"""Запуск Frida-скрипта в процессе приложения с ожиданием финального сообщения.

Usage:
  python3 run_frida.py <pid> <script.js> [timeout_sec]

Печатает все сообщения скрипта (по одному JSON в строке) и завершается, как
только придёт сообщение с полем "final" или истечёт таймаут.
"""

import json
import os
import sys
import threading
import time

_extra = os.environ.get("FRIDA_PYTHON_PATH")
if not _extra:
    # Ставим frida глобально не всегда: на этом хосте она лежит в .local.
    for _candidate in ("/home/work/.local/lib/python3.12/site-packages",):
        if os.path.isdir(_candidate):
            _extra = _candidate
            break
if _extra:
    sys.path.insert(0, _extra)
import frida  # noqa: E402

pid = int(sys.argv[1])
script_path = sys.argv[2]
timeout = float(sys.argv[3]) if len(sys.argv) > 3 else 180.0

src = open(script_path, encoding="utf-8").read()
device = frida.get_usb_device(timeout=20)
session = device.attach(pid)
script = session.create_script(src)

done = threading.Event()


def on_message(message, data):
    line = json.dumps(message, ensure_ascii=False)
    # Резать JSON нельзя: обрезанная строка ломает разбор и результат теряется.
    print(line, flush=True)
    payload = message.get("payload") or {}
    if isinstance(payload, dict) and (payload.get("final") or payload.get("error")):
        done.set()
    # Скрипт упал — ждать полный таймаут бессмысленно.
    if message.get("type") == "error" and "TypeError" in str(message.get("description", "")):
        done.set()


script.on("message", on_message)
script.load()
done.wait(timeout)
try:
    script.unload()
except Exception:
    pass
