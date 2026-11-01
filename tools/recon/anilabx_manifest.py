#!/usr/bin/env python3
"""Манифест портирования AniLabX → репозитории.

Собирает список парсеров AniLabX по типам, сверяет с модулями в репозиториях
и печатает таблицу: что уже есть, что дублируется, что нужно портировать.

Usage:
  python3 anilabx_manifest.py            # таблица
  python3 anilabx_manifest.py --json     # машинный вывод
"""

import argparse
import json
import os
import re
import sys

REPOS = {
    "anime": "/home/work/repos/aniyomi-ru-anime-repo",
    "manga": "/home/work/repos/mihon-ru-manga-repo",
    "novel": "/home/work/repos/lnreader-ru-plugins",
}

# Тип AniLabX -> целевой репозиторий (по решению владельца)
TYPE_TO_REPO = {
    "anime": "anime",
    "dorama": "anime",
    "manga": "manga",
    "light_novel": "novel",
    "book": "novel",
    "novel": "novel",
    "services": None,  # сервисные (kodik) отдельными расширениями не портируем
}

PARSERS = {
    "anime": [
        "anidub", "anidub.life", "anidub_old", "anilibria", "animevost", "anistar",
        "kdk_anistar", "kdk_dream_cast", "senu", "sovetromantica", "studiomir",
        "vk_amazingdubbing", "vk_aninyatv", "vk_studioamber",
    ],
    "dorama": ["doramysu"],
    "light_novel": ["novel_tl", "rulate", "ruranobe"],
    "manga": [
        "desu", "honey-manga", "mangaplus_ru", "readmanga", "remanga", "rumix",
        "selfmanga", "senkuro", "translab", "usagi", "zazaza",
    ],
    "services": ["kodik"],
}

# Соответствие имён расширений в репозиториях (папка модуля -> парсеры)
MODULE_ALIASES = {
    "anidub": ["anidub", "anidub.life", "anidub_old"],
    "anistar": ["anistar", "kdk_anistar"],
    "kodikdreamcast": ["kdk_dream_cast"],
    "mangaplus": ["mangaplus_ru"],
    "justsu": ["jut.su"],
}


def modules(repo_key):
    root = os.path.join(REPOS[repo_key], "src")
    if not os.path.isdir(root):
        return set()
    names = set()
    for lang in ("ru", "all", "en", "multi"):
        path = os.path.join(root, lang)
        if os.path.isdir(path):
            names.update(d for d in os.listdir(path) if os.path.isdir(os.path.join(path, d)))
    return names


def lnreader_plugins():
    root = os.path.join(REPOS["novel"], "plugins")
    found = set()
    for dirpath, _, files in os.walk(root):
        for f in files:
            if f.endswith(".ts"):
                found.add(os.path.splitext(f)[0])
    return found


NOVEL_ALIASES = {
    "novel_tl": ["novelTL"],
    "rulate": ["Rulate[rulate]", "Bllate[rulate]", "Erolate[rulate]"],
    "ruranobe": ["Ruranobe"],
    "ranobehub": ["ranobehub"],
}


def status_for(parser, repo_key, mods):
    if repo_key == "novel":
        for alias in NOVEL_ALIASES.get(parser, []):
            if alias in mods:
                return "есть", alias
    key = parser.replace("vk_", "")
    candidates = {parser, key, parser.replace(".", "_"), parser.replace("-", "_")}
    for module, aliases in MODULE_ALIASES.items():
        if parser in aliases:
            candidates.add(module)
    for c in candidates:
        if c in mods:
            return "есть", c
    return "нет", ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    mods = {k: modules(k) for k in ("anime", "manga")}
    mods["novel"] = lnreader_plugins()

    rows = []
    for typ, names in PARSERS.items():
        repo_key = TYPE_TO_REPO.get(typ)
        for name in names:
            if repo_key is None:
                rows.append({"parser": name, "type": typ, "repo": "-", "status": "не портируем", "module": ""})
                continue
            status, module = status_for(name, repo_key, mods[repo_key])
            rows.append({"parser": name, "type": typ, "repo": repo_key, "status": status, "module": module})

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return

    width = max(len(r["parser"]) for r in rows) + 2
    print(f"{'парсер':<{width}}{'тип':<13}{'репозиторий':<10}статус")
    print("-" * (width + 40))
    for r in rows:
        mark = "OK " if r["status"] == "есть" else "-- "
        print(f"{mark}{r['parser']:<{width}}{r['type']:<13}{r['repo']:<10}{r['status']} {r['module']}")

    todo = [r for r in rows if r["status"] == "нет"]
    print()
    print(f"итого: {len(rows)}, уже есть: {sum(1 for r in rows if r['status'] == 'есть')}, портировать: {len(todo)}")


if __name__ == "__main__":
    main()
