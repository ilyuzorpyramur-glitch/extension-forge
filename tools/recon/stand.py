#!/usr/bin/env python3
"""Headless check of an installed Aniyomi/Tadami extension source.

Runs the source class inside the running app process via Frida and prints a
PASS/FAIL report for: popular, latest, search, details, episodes, video.

Usage:
  sudo systemd-run --unit=stand --collect --quiet /bin/bash -c \
    "/usr/bin/python3 tools/stand.py ru.anistar.AniStar > /tmp/stand.log 2>&1"
"""

import json
import os
import subprocess
import sys
import time

FRIDA_PY = "/home/work/.local/lib/python3.12/site-packages"
DEVICE = os.environ.get("ADB_DEVICE", "emulator-5554")
PKG = os.environ.get("TADAMI_PKG", "com.tadami.aurora")
TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stand.js")


def adb(*args):
    return subprocess.run(["adb", "-s", DEVICE] + list(args), capture_output=True, text=True).stdout


def main():
    if len(sys.argv) < 2:
        print("usage: stand.py <package-suffix>  e.g. ru.anistar.AniStar")
        return 2

    suffix = sys.argv[1]
    package = "eu.kanade.tachiyomi.animeextension." + suffix.rsplit(".", 1)[0]
    path = adb("shell", "pm", "path", package).strip().replace("package:", "")
    apk = path.splitlines()[0] if path else ""
    script = open(TEMPLATE).read().replace("%SUFFIX%", suffix).replace("%APK%", apk)

    pid = adb("shell", "pidof", PKG).strip().splitlines()
    if not pid:
        print(json.dumps({"error": "app not running", "package": PKG}))
        return 1

    sys.path.insert(0, FRIDA_PY)
    import frida

    session = frida.get_usb_device(timeout=20).attach(int(pid[0]))
    sc = session.create_script(script)
    result = {}

    def on_message(msg, data):
        if msg.get("type") == "send":
            result.update(msg["payload"])
        elif msg.get("type") == "error":
            result["scriptError"] = msg.get("description")

    sc.on("message", on_message)
    sc.load()
    time.sleep(25)

    steps = result.get("steps", [])
    failed = [s for s in steps if not s.get("ok")]
    report = {
        "source": result.get("source"),
        "error": result.get("error") or result.get("scriptError"),
        "passed": len(steps) - len(failed),
        "failed": len(failed),
        "steps": steps,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not failed and steps else 1


if __name__ == "__main__":
    sys.exit(main())
