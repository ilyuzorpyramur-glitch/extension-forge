#!/usr/bin/env python3
"""Инвентарь модулей репозиториев: модуль -> пакет/класс/APK.

Сканирует Kotlin/TS исходники и печатает таблицу, готовую для стенда и аудита.

Usage:
  python3 repo_inventory.py anime|manga|novel [--json]
"""

import argparse
import json
import os
import re
import subprocess

REPOS = {
    "anime": ("/home/work/repos/aniyomi-ru-anime-repo", "eu.kanade.tachiyomi.animeextension"),
    "manga": ("/home/work/repos/mihon-ru-manga-repo", "eu.kanade.tachiyomi.extension"),
    "novel": ("/home/work/repos/lnreader-ru-plugins", "lnreader"),
}

SRC_RE = re.compile(r"^\s*(?:open\s+|abstract\s+)?class\s+(\w+)\s*(?:\(|:)", re.M)
PKG_RE = re.compile(r"^\s*package\s+([\w.]+)", re.M)


def anime_or_manga(kind):
    root, base_pkg = REPOS[kind]
    apks = {}
    apk_dir = os.path.join(root, "apk")
    if os.path.isdir(apk_dir):
        for name in os.listdir(apk_dir):
            if not name.endswith(".apk"):
                continue
            parts = name[:-4].split("-v")[0].split(".")
            if len(parts) >= 2:
                apks[parts[-1]] = os.path.join(apk_dir, name)

    rows = []
    for lang in ("ru", "all", "en"):
        lang_dir = os.path.join(root, "src", lang)
        if not os.path.isdir(lang_dir):
            continue
        for module in sorted(os.listdir(lang_dir)):
            module_dir = os.path.join(lang_dir, module)
            if not os.path.isdir(module_dir):
                continue
            sources = []
            for dirpath, _, files in os.walk(module_dir):
                for fname in files:
                    if not fname.endswith(".kt"):
                        continue
                    path = os.path.join(dirpath, fname)
                    text = open(path, encoding="utf-8", errors="replace").read()
                    pkg = PKG_RE.search(text)
                    if not pkg:
                        continue
                    for match in SRC_RE.finditer(text):
                        cls = match.group(1)
                        header = text[match.start():match.start() + 400]
                        if "Source" not in header:
                            continue
                        # Источник — тот класс, что наследует базовый класс
                        # расширения; DTO и хелперы отсекаем по рангу.
                        rank = 0
                        if "HttpSource" in header or "CatalogueSource" in header:
                            rank = 2
                        elif re.search(r":\s*\w*Source\s*\(", header):
                            rank = 2
                        elif "Source" in header:
                            rank = 1
                        sources.append({
                            "class": cls,
                            "fqcn": pkg.group(1) + "." + cls,
                            "file": os.path.relpath(path, root),
                            "rank": rank,
                        })
            version = ""
            gradle = os.path.join(module_dir, "build.gradle")
            if os.path.exists(gradle):
                m = re.search(r"extVersionCode\s*=\s*(\d+)", open(gradle).read())
                if m:
                    version = m.group(1)
            sources.sort(key=lambda s: -s["rank"])
            rows.append({
                "module": module,
                "lang": lang,
                "sources": sources,
                "apk": apks.get(module, ""),
                "extVersionCode": version,
            })
    return rows


def novel():
    root = REPOS["novel"][0]
    rows = []
    for dirpath, _, files in os.walk(os.path.join(root, "plugins")):
        for fname in files:
            if not fname.endswith(".ts"):
                continue
            path = os.path.join(dirpath, fname)
            text = open(path, encoding="utf-8", errors="replace").read()
            pid = re.search(r"\bid\s*=\s*'([^']+)'", text)
            name = re.search(r"\bname\s*=\s*'([^']+)'", text)
            site = re.search(r"\bsite\s*=\s*'([^']+)'", text)
            version = re.search(r"\bversion\s*=\s*'([^']+)'", text)
            icon = re.search(r"\bicon\s*=\s*'([^']+)'", text)
            icon_path = icon.group(1) if icon else ""
            rows.append({
                "file": os.path.relpath(path, root),
                "id": pid.group(1) if pid else "",
                "name": name.group(1) if name else "",
                "site": site.group(1) if site else "",
                "version": version.group(1) if version else "",
                "icon": icon_path,
                "iconExists": bool(icon_path) and os.path.exists(os.path.join(root, icon_path)),
            })
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("kind", choices=["anime", "manga", "novel"])
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    rows = novel() if args.kind == "novel" else anime_or_manga(args.kind)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return

    print(f"{'модуль':<18}{'версия':<8}{'apk':<6}источники")
    print("-" * 78)
    for r in rows:
        if args.kind == "novel":
            mark = "OK" if r["iconExists"] else "--"
            print(f"{r['id'] or r['file']:<18}{r['version']:<8}{mark:<6}{r['name']} | {r['site']}")
        else:
            apk = "да" if r["apk"] else "нет"
            srcs = ", ".join(s["fqcn"].split(".")[-1] for s in r["sources"])
            print(f"{r['module']:<18}{r['extVersionCode']:<8}{apk:<6}{srcs or 'НЕ НАЙДЕН'}")
    missing = [r for r in rows if args.kind != "novel" and not r["sources"]]
    print(f"\nвсего {len(rows)}; без источника: {len(missing)}")


if __name__ == "__main__":
    main()
