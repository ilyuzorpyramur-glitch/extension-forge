#!/usr/bin/env python3
"""Разведка SPA-сайтов: собирает JS-бандлы и вытаскивает карту API.

  python3 tools/spa_recon.py https://anijoy.ru

Печатает найденные эндпоинты и, если их достаточно, готовую декларацию
(sites/<id>.yaml) для `forge.py generate`.
"""

import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
SITES = Path("/home/work/repos/extension-forge/sites")


def get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            import gzip

            raw = gzip.decompress(raw)
        return raw.decode("utf-8", "replace")


def scripts(html, base):
    srcs = re.findall(r"<script[^>]+src=[\"']([^\"']+)[\"']", html)
    out = []
    for src in srcs:
        if src.startswith("//"):
            src = "https:" + src
        elif src.startswith("/"):
            src = urllib.parse.urljoin(base, src)
        elif not src.startswith("http"):
            continue
        out.append(src)
    return list(dict.fromkeys(out))


API_RE = re.compile(r"[\"'`](/(?:api|v1|v2)/[A-Za-z0-9/_{}$:.-]{2,60})[\"'`]")


def endpoints(html, base, limit=10):
    found = {}
    for src in scripts(html, base)[:limit]:
        try:
            js = get(src)
        except Exception:
            continue
        for path in API_RE.findall(js):
            found.setdefault(path, src)
        for path in re.findall(r"[\"'`](https://[a-z0-9.-]+/api/[A-Za-z0-9/_{}$:.-]{2,60})[\"'`]", js):
            found.setdefault(path, src)
    return found


def main():
    url = sys.argv[1]
    html = get(url)
    host = urllib.parse.urlparse(url).netloc
    site_id = re.sub(r"[^a-z0-9]", "", host.split(".")[-2])

    spa = ("<h1" not in html) and ("og:image" not in html) and ("<iframe" not in html)
    print(f"→ {host}: SPA={spa}, html={len(html)} байт, скриптов={len(scripts(html, url))}")

    eps = endpoints(html, url)
    print(f"  найдено эндпоинтов: {len(eps)}")
    for path, src in list(eps.items())[:25]:
        print(f"    {path}   ← {src.split('/')[-1]}")

    if not eps:
        print("  ✖ карта API не найдена — нужен ручной разбор бандлов")
        return

    def pick(*keys):
        for path in eps:
            if all(k in path for k in keys):
                return path
        return ""

    catalog = pick("manga", "catalog") or pick("anime", "catalog") or pick("catalog")
    details = pick("anime") or pick("manga") or ""
    chapters = pick("episode") or pick("chapter") or ""
    pages = pick("watch") or pick("video") or pick("player") or ""

    if not catalog:
        print("  ⚠ каталог не опознан, декларация не собрана")
        return

    yaml_text = (
        f"id: {site_id}\n"
        f"kind: aniyomi\n"
        f"name: {host.split('.')[0].capitalize()}\n"
        f"base_url: https://{host}\n"
        f"lang: ru\n"
        f"api_style: json_api\n"
        f"api:\n"
        f'  catalog: "{catalog}"\n'
        + (f'  details: "{details}"\n' if details else "")
        + (f'  chapters: "{chapters}"\n' if chapters else "")
        + (f'  pages: "{pages}"\n' if pages else "")
        + "selectors: {}\n"
    )
    path = SITES / f"{site_id}.yaml"
    path.write_text(yaml_text, encoding="utf-8")
    print(f"  ✔ декларация: {path}")
    print(yaml_text)


if __name__ == "__main__":
    main()
