#!/usr/bin/env python3
"""Аудит манга-расширений стендом: инвентарь -> список источников -> Frida.

Устанавливает/подгружает APK, гоняет stand_batch.js батчами и складывает
отчёт в /tmp/audit_anime.json.

Usage:
  python3 audit_anime.py [--batch 4] [--only anidub,anistar] [--rebuild-apks]
"""

import argparse
import json
import os
import subprocess
import sys
import time

DEVICE = "emulator-5554"
PKG = "com.tadami.aurora"
ROOT = "/home/work/repos/mihon-ru-manga-repo"
WORKDIR = "/home/work/.openclaw/workspace/claude"
TMP = "/data/local/tmp"


def sh(*args, check=False):
    r = subprocess.run(args, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"{args}: {r.stderr.strip()}")
    return r.stdout.strip()


def adb(*args, check=False):
    return sh("adb", "-s", DEVICE, *args, check=check)


def inventory():
    out = subprocess.run([sys.executable, os.path.join(WORKDIR, "tools/repo_inventory.py"), "manga", "--json"],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def apk_for(module, rows):
    local = rows.get(module, "")
    if local:
        remote = f"{TMP}/{module}.apk"
        adb("push", local, remote)
        adb("shell", "su", "0", "chmod", "444", remote)
        return remote
    installed = adb("shell", "pm", "path", f"eu.kanade.tachiyomi.extension.ru.{module}")
    if installed.startswith("package:"):
        return installed.split("package:")[1].splitlines()[0]
    return ""
    remote = f"{TMP}/{module}.apk"
    adb("push", local, remote)
    adb("shell", "su", "0", "chmod", "444", remote)
    return remote


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=4)
    ap.add_argument("--only", default="")
    ap.add_argument("--out", default="/tmp/audit_manga.json")
    ap.add_argument("--no-pages", action="store_true")
    args = ap.parse_args()

    def restart_app():
        adb("shell", "am", "force-stop", PKG)
        time.sleep(2)
        adb("shell", "am", "start", "-n", f"{PKG}/eu.kanade.tachiyomi.ui.main.MainActivity")
        time.sleep(13)
        current = adb("shell", "pidof", PKG).split()
        return current[0] if current else ""

    def ensure_app():
        current = adb("shell", "pidof", PKG).split()
        if not current:
            adb("shell", "am", "start", "-n", f"{PKG}/eu.kanade.tachiyomi.ui.main.MainActivity")
            time.sleep(14)
            current = adb("shell", "pidof", PKG).split()
        return current[0] if current else ""

    pid = ensure_app()

    rows = {r["module"]: r["apk"] for r in inventory() if r["lang"] == "ru"}
    items = [r for r in inventory() if r["lang"] == "ru"]
    if args.only:
        wanted = set(args.only.split(","))
        items = [r for r in items if r["module"] in wanted]

    results = []
    for start in range(0, len(items), args.batch):
        chunk = items[start:start + args.batch]
        specs = []
        for r in chunk:
            # основной класс источника — тот, что не DTO
            fqcns = [s["fqcn"] for s in r["sources"]]
            apk = apk_for(r["module"], rows)
            specs.append({"fqcn": fqcns[0] if fqcns else "", "fqcns": fqcns, "apk": apk,
                          "module": r["module"], "pages": not args.no_pages})
        script = open(os.path.join(WORKDIR, "tools/stand_manga_batch.js"), encoding="utf-8").read()
        script = script.replace("%SOURCES%", json.dumps(specs, ensure_ascii=False))
        path = f"/tmp/stand_batch_{start}.js"
        open(path, "w", encoding="utf-8").write(script)
        print(f"--- батч {start // args.batch + 1}: " + ", ".join(s["module"] for s in specs), flush=True)
        pid = restart_app() or ensure_app()
        if not pid:
            print("  приложение не поднялось, пропуск батча", flush=True)
            continue
        out = subprocess.run(["sudo", "-n", "/usr/bin/python3", os.path.join(WORKDIR, "tools/run_frida.py"),
                              pid, path, "600"],
                             capture_output=True, text=True).stdout
        for line in out.splitlines():
            try:
                msg = json.loads(line)
            except Exception:
                continue
            payload = msg.get("payload") or {}
            if payload.get("source") and "steps" in payload:
                if not payload.get("module"):
                    payload["module"] = next((s["module"] for s in specs if payload["source"] in [s["fqcn"]] + s["fqcns"]), "?")
                results.append(payload)
                print(f"  {payload['module']}: {payload.get('passed')}/{len(payload['steps'])}", flush=True)
        time.sleep(4)  # пауза: сайты с антиботом не любят частые запросы
        got = {r.get("module") for r in results}
        for spec in specs:
            if spec["module"] not in got:
                results.append({"module": spec["module"], "source": spec["fqcn"],
                                "steps": [{"name": "load", "ok": False, "error": "нет ответа: приложение упало или таймаут"}],
                                "passed": 0, "failed": 1})
        time.sleep(2)

    json.dump(results, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    bad = [r for r in results if r.get("failed")]
    print(f"\nвсего проверено {len(results)}; с ошибками {len(bad)}")
    for r in bad:
        fails = [s["name"] for s in r["steps"] if not s["ok"]]
        print(f"  {r['module']}: {', '.join(fails)}")


if __name__ == "__main__":
    main()
