"""Исполнитель спеки v1 + самопроверка. Прототип будущего движка в приложении."""

from __future__ import annotations

import re

import recon


def _abs(base: str, url: str) -> str:
    if url.startswith("http"):
        return url
    if url.startswith("//"):
        return "https:" + url
    return base.rstrip("/") + "/" + url.lstrip("/")


def page_url(spec: dict, number: int) -> str:
    base = spec["base"].rstrip("/")
    page = spec.get("page") or {}
    template = page.get("template") or ""
    if number <= 1 or not template:
        return base + spec.get("catalog", "/")
    if page.get("kind") == "path":
        return base + spec.get("catalog", "/").rstrip("/") + "/" + template.replace("{n}", str(number)) + "/"
    if page.get("kind") == "offset":
        step = int(page.get("step") or 50)
        value = (number - 1) * step
        catalog = spec.get("catalog", "/")
        if "offset=" in catalog:
            # offset уже в адресе каталога — подменяем значение
            return base + re.sub(r"offset=\d+", f"offset={value}", catalog)
        separator = "&" if "?" in catalog else "?"
        return base + catalog + separator + template.replace("{n}", str(value))
    separator = "&" if "?" in template else "?"
    return base + spec.get("catalog", "/") + separator + template.replace("{n}", str(number))


def cards(spec: dict, html: str) -> list[dict]:
    """Карточки: ссылка, заголовок, постер."""
    pattern = re.compile(spec.get("link_re") or r"/\d+-[a-z0-9\-]+\.html")
    result, seen = [], set()
    for match in pattern.finditer(html):
        href = match.group(0)
        if href in seen:
            continue
        seen.add(href)
        window = html[match.start():match.start() + 900]
        title = ""
        title_spec = spec.get("title") or {}
        if title_spec.get("mode") == "regex":
            found = re.search(title_spec["pattern"], window)
            title = found.group(1).strip() if found else ""
        if not title:
            # текст самой ссылки: <a href="...">Название</a>
            tag_end = html.find(">", match.end())
            closing = html.find("</a>", tag_end + 1) if tag_end > 0 else -1
            if tag_end > 0 and closing > tag_end:
                inner = re.sub(r"<[^>]+>", " ", html[tag_end + 1:closing])
                title = re.sub(r"\s+", " ", inner).strip()[:120]
        if not title:
            alt = re.search(r'alt="([^"]{2,90})"', window)
            title = alt.group(1).strip() if alt else ""
        poster = ""
        poster_spec = spec.get("poster") or {}
        if poster_spec.get("pattern"):
            found = re.search(poster_spec["pattern"], window, re.I)
            poster = _abs(spec["base"], found.group(1)) if found else ""
        result.append({"url": _abs(spec["base"], href), "title": title, "poster": poster})
    return result


def details(spec: dict, html: str) -> dict:
    title_match = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S | re.I)
    title = re.sub(r"<[^>]+>", " ", title_match.group(1)).strip() if title_match else ""
    poster = ""
    meta = re.search(r'property="og:image"[^>]*content="([^"]+)"', html)
    if meta:
        poster = meta.group(1)
    description = ""
    name = (spec.get("desc") or {}).get("name")
    if name:
        block = re.search(rf'class="[^"]*{name}[^"]*"[^>]*>(.*?)</div>', html, re.S)
        if block:
            description = re.sub(r"<[^>]+>", " ", block.group(1))
            description = re.sub(r"\s+", " ", description).strip()
    genres = ""
    selector = spec.get("genres") or ""
    prefix = re.search(r"href\*='([^']+)'", selector)
    if prefix:
        found = re.findall(rf'href="[^"]*{re.escape(prefix.group(1))}[^"]*"[^>]*>([^<]+)<', html)
        genres = ", ".join(dict.fromkeys(item.strip() for item in found))[:80]
    if not genres:
        found = re.findall(r'href="[^"]*/genres?/[^"]*"[^>]*>([^<]+)<', html)
        genres = ", ".join(dict.fromkeys(item.strip() for item in found))[:80]
    return {"title": title, "poster": poster, "desc_len": len(description), "genres": genres}


MEDIA = re.compile(r"\[([^\]\[]{2,20})\]\s*(https?://[^\"'\s,\]]+?\.(?:mp4|m3u8))", re.I)
PLAIN = re.compile(r"https?://[^\"'\s,\]]+?\.(?:mp4|m3u8)", re.I)


def media(spec: dict, detail_html: str) -> list[dict]:
    player = spec.get("player") or {}
    mode = player.get("mode")
    if mode == "iframe":
        found = re.search(player.get("pattern") or r'<iframe[^>]*src="([^"]+)"', detail_html)
        if not found:
            return []
        _, page = recon.fetch_html(_abs(spec["base"], found.group(1)))
    elif mode == "direct":
        page = detail_html
    else:
        return []

    items = [{"quality": q.strip(), "url": u} for q, u in MEDIA.findall(page)]
    if not items:
        items = [{"quality": "", "url": u} for u in dict.fromkeys(PLAIN.findall(page))]
    return items


def check(spec: dict) -> dict:
    """Пять проверок: каталог, листание, детали, медиа, поиск."""
    report = {"base": spec["base"], "engine": spec.get("engine"), "steps": {}, "score": 0}

    status, first = recon.fetch_html(page_url(spec, 1))
    list1 = cards(spec, first)
    ok_catalog = len(list1) >= 10
    report["steps"]["catalog"] = {"ok": ok_catalog, "count": len(list1),
                                  "sample": [item["title"] for item in list1[:3]]}
    if ok_catalog:
        report["score"] += 1

    if (spec.get("page") or {}).get("template"):
        _, second = recon.fetch_html(page_url(spec, 2))
        list2 = cards(spec, second)
        overlap = len({item["url"] for item in list1} & {item["url"] for item in list2})
        fresh = len({item["url"] for item in list2} - {item["url"] for item in list1})
        ok_scroll = fresh >= 5 and overlap < len(list2)
        report["steps"]["scroll"] = {"ok": ok_scroll, "fresh": fresh, "overlap": overlap}
        if ok_scroll:
            report["score"] += 1
    else:
        report["steps"]["scroll"] = {"ok": False, "error": "шаблон пагинации не найден"}

    if list1:
        item = list1[0]
        _, detail = recon.fetch_html(item["url"])
        facts = details(spec, detail)
        ok_details = bool(facts["title"]) and bool(facts["poster"]) and facts["desc_len"] > 80
        report["steps"]["details"] = {"ok": ok_details, **{k: (v[:60] if isinstance(v, str) else v)
                                                           for k, v in facts.items()}}
        if ok_details:
            report["score"] += 1

        videos = media(spec, detail)
        report["steps"]["media"] = {"ok": bool(videos), "count": len(videos),
                                    "sample": [v["quality"] or "без метки" for v in videos[:3]]}
        if videos:
            report["score"] += 1
    else:
        report["steps"]["details"] = {"ok": False, "error": "нет карточек"}
        report["steps"]["media"] = {"ok": False, "error": "нет карточек"}

    return report
