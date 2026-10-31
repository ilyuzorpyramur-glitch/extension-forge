"""Классификация 30 сайтов из дампов: серверный HTML, SPA, каталог, пагинация."""

from __future__ import annotations

import glob
import json
import re

import recon

TYPES = {"anime", "manga", "dorama", "book", "light_novel", "comics"}


def targets() -> list[dict]:
    files = sorted(glob.glob("/home/work/redroid/anilabx-parsers/pd/*.js")
                   + glob.glob("/home/work/redroid/anilabx-parsers/pd2/*.js"))
    result, seen = [], set()
    for path in files:
        try:
            data = json.load(open(path))
        except Exception:
            continue
        values = [value for value in data.values() if isinstance(value, str)]
        kind = next((value for value in values if value in TYPES), "")
        hosts = [value for value in values
                 if value.startswith("http") and "anilabx" not in value and "gmail" not in value]
        if not hosts:
            continue
        base = hosts[0].rstrip("/")
        host = re.sub(r"^https?://", "", base).split("/")[0]
        if host in seen:
            continue
        seen.add(host)
        result.append({"host": host, "base": base, "kind": kind})
    return result


def main() -> None:
    rows = []
    for site in targets():
        code, html = recon.fetch_html(site["base"] + "/")
        anchors = re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.{0,80}?)</a>', html, re.S)
        cards = 0
        for href, text in anchors:
            text = re.sub(r"<[^>]+>", "", text).strip()
            if len(text) >= 4 and href.count("/") >= 2 and not any(
                bad in href for bad in (".css", ".js", "rss", "#", "login", "register", "/tags/", "/tag/")
            ):
                cards += 1
        pagination = bool(re.search(r"(/page/2|PAGEN_1=2|page=2)", html, re.I))
        spa = any(marker in html for marker in ("id=\"__next\"", "id=\"app\"", "__NUXT__", "data-reactroot"))
        rows.append({
            "host": site["host"], "kind": site["kind"], "status": code,
            "size": len(html), "cards": cards, "pagination": pagination, "spa": spa,
        })
        print(f"{code:4} {site['host'][:28].ljust(30)} {len(html):7} карточек {cards:3} "
              f"пагинация {'да' if pagination else 'нет':3} {'SPA' if spa else ''}", flush=True)

    good = [row for row in rows if row["status"] == 200 and row["cards"] >= 8 and not row["spa"]]
    print(f"\nсерверный HTML с каталогом: {len(good)} из {len(rows)}")
    with open("/tmp/spec_sites.json", "w", encoding="utf-8") as handle:
        json.dump(rows, handle, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
