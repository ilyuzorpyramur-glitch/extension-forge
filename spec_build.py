"""Сборка спеки v1 по живой вёрстке. Без модели: только наблюдаемые факты."""

from __future__ import annotations

import re

import recon

SKIP = (".css", ".js", ".png", ".jpg", ".jpeg", ".ico", ".svg", "/templates/", "/engine/", "rss", "#", "mailto:")


def _abs(base: str, url: str) -> str:
    if url.startswith("http"):
        return url
    if url.startswith("//"):
        return "https:" + url
    return base.rstrip("/") + "/" + url.lstrip("/")


def _links(html: str) -> list[str]:
    return [h for h in re.findall(r'href="([^"]+)"', html) if not any(b in h.lower() for b in SKIP)]


def _shape(url: str) -> str:
    path = re.sub(r"^https?://[^/]+", "", url)
    path = re.sub(r"\d+", "#", path)
    path = re.sub(r"[a-z][a-z0-9\-]{3,}", "w", path)
    return path


CANDIDATE_PATHS = ("/list?offset=0", "/manga/", "/anime/", "/catalog/", "/projects", "/title/")


def _anchors(html: str) -> list[tuple[str, str]]:
    pairs = re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.{0,80}?)</a>', html, re.S)
    result = []
    for href, text in pairs:
        text = re.sub(r"<[^>]+>", "", text).strip()
        if len(text) < 3 or any(bad in href.lower() for bad in SKIP):
            continue
        result.append((href, text))
    return result


def pick_catalog(base: str) -> tuple[str, str]:
    """Каталог: главная либо типовой раздел, где реально есть карточки."""
    best_path, best_pattern, best_hits = "", "", 0
    for candidate in ("/",) + CANDIDATE_PATHS:
        status, html = recon.fetch_html(base + candidate)
        if status != 200 or len(html) < 2000:
            continue
        pattern = pick_link_re(html, base)
        hits = len(re.findall(pattern, html))
        if hits > best_hits:
            best_path, best_pattern, best_hits = candidate, pattern, hits
    if best_path:
        return best_path, best_pattern

    _, home = recon.fetch_html(base + "/")
    return "/", pick_link_re(home, base)


LINK_PATTERNS = (
    r'href="/\d+-[a-z0-9\-]+\.html"',                 # DLE
    r'href="/list/[^"/]+"',                              # readmanga family: /list/<slug>
    r'href="/(?:manga|anime|title)/[a-z0-9\-_]{4,}/?"',
    r'href="/[a-z0-9\-]+/[a-z0-9\-]{5,}/"',
    r'href="/[a-z0-9_\-]{6,}"',
)


def _detail_like(base: str, pattern: str, html: str) -> bool:
    """Проверка: страница по ссылке действительно похожа на тайтл."""
    match = re.search(pattern, html)
    if not match:
        return False
    href = re.search(r'href="([^"]+)"', match.group(0))
    if not href:
        return False
    url = _abs(base, href.group(1))
    status, page = recon.fetch_html(url)
    if status != 200 or len(page) < 1000:
        return False
    heading = re.search(r"<h1[^>]*>(.*?)</h1>", page, re.S | re.I)
    title = re.sub(r"<[^>]+>", " ", heading.group(1)).strip() if heading else ""
    return len(title) >= 2


def pick_link_re(html: str, base: str = "") -> str:
    """Форма ссылки на карточку: шаблон, который ведёт на настоящую страницу тайтла."""
    for pattern in LINK_PATTERNS:
        hits = len(re.findall(pattern, html))
        if not 8 <= hits <= 900:
            continue
        if not base or _detail_like(base, pattern, html):
            return pattern
    return r'href="/[^"]+"'


def pick_page_template(html: str) -> tuple[str, str, int]:
    for href in re.findall(r'href="([^"]+)"', html):
        if re.search(r"/page/2/?$", href):
            return "path", "page/{n}/", 0
        if re.search(r"[?&]page=2\b", href):
            return "query", "page={n}", 0
        if re.search(r"PAGEN_1=2", href, re.I):
            return "query", "PAGEN_1={n}", 0

    # offset-пагинация: шаг = разница между соседними значениями
    offsets = sorted({int(value) for value in re.findall(r"offset=(\d+)", html)})
    if len(offsets) >= 2:
        steps = [right - left for left, right in zip(offsets, offsets[1:]) if right > left]
        if steps:
            step = min(steps)
            return "offset", "offset={n}", step
    return "", "", 0


def pick_card_markers(html: str, link_re: str) -> dict:
    """Границы карточки и поля внутри неё."""
    match = re.search(link_re, html)
    if not match:
        return {}
    start = html.rfind("<", 0, match.start())
    # карточка — от ближайшего контейнера до закрытия ссылки
    head = html[max(0, start - 400):match.start()]
    block_start = "<article" if "<article" in head.lower() else ""
    if not block_start:
        for tag in ("<div", "<li", "<tr"):
            if tag in head.lower():
                block_start = tag
                break
    block_end = "</article>" if block_start == "<article" else ("</div>" if block_start == "<div" else "")
    return {"link_re": link_re, "block_start": block_start, "block_end": block_end}


def pick_title_marker(html: str, link_re: str) -> dict:
    match = re.search(link_re, html)
    if not match:
        return {}
    window = html[match.start():match.start() + 1200]
    alt = re.search(r'alt="([^"]{2,90})"', window)
    if alt:
        return {"mode": "regex", "pattern": r'alt="([^"]+)"'}
    return {"mode": "text"}


def pick_poster_marker(html: str, link_re: str) -> dict:
    match = re.search(link_re, html)
    if not match:
        return {}
    window = html[match.start():match.start() + 1200]
    img = re.search(r'(?:data-src|src)="([^"]+\.(?:jpg|jpeg|png|webp)[^"]*)"', window, re.I)
    if img:
        return {"mode": "regex", "pattern": r'(?:data-src|src)="([^"]+\.(?:jpg|jpeg|png|webp)[^"]*)"'}
    return {}


def pick_desc_marker(detail: str) -> dict:
    for name in ("fdesc", "full-text", "full-story", "description", "post-content", "entry-content", "anime-desc"):
        match = re.search(rf'class="[^"]*{name}[^"]*"[^>]*>', detail)
        if match:
            return {"mode": "class", "name": name}
    return {}


def pick_genres(detail: str) -> str:
    for prefix in ("/genres/", "/genre/", "/tags/", "/tag/"):
        if len(re.findall(rf'href="[^"]*{prefix}', detail)) >= 2:
            return f"a[href*='{prefix}']"
    return ""


def pick_player(detail: str) -> dict:
    if 'data-iframe="' in detail:
        return {"mode": "iframe", "pattern": r'data-iframe="([^"]+)"'}
    if "<iframe" in detail.lower():
        return {"mode": "iframe", "pattern": r'<iframe[^>]*src="([^"]+)"'}
    if "data-player-anime-id" in detail:
        return {"mode": "relay"}
    if re.search(r"\.(?:mp4|m3u8)", detail):
        return {"mode": "direct"}
    return {}


def build(site_url: str, kind: str = "aniyomi") -> dict:
    base = site_url.rstrip("/")
    status, home = recon.fetch_html(base + "/")
    if status != 200 or len(home) < 1500:
        return {"base": base, "error": f"главная: HTTP {status}"}

    spec: dict = {"base": base, "engine": "html"}

    try:
        import dledetect

        if dledetect.is_dle(home):
            cfg = dledetect.config(base, kind)
            if cfg:
                spec.update(
                    {
                        "engine": "dle",
                        "catalog": cfg["catalog"],
                        "page": {"kind": "path", "template": cfg["page_suffix"]},
                        "link_re": cfg["link_re"],
                        "desc": {"mode": "class", "name": cfg["desc_sel"].lstrip(".").split(".")[0]},
                        "genres": cfg["genre_sel"],
                        "player": {"mode": cfg["player_mode"]},
                    }
                )
    except Exception as exc:  # noqa: BLE001
        spec["dle_error"] = str(exc)[:80]

    if "link_re" not in spec:
        catalog_path, link_re = pick_catalog(base)
        _, catalog = recon.fetch_html(base + catalog_path)
        kind_page, template, step = pick_page_template(catalog)
        spec["catalog"] = catalog_path
        spec["page"] = {"kind": kind_page, "template": template, "step": step}
        spec["link_re"] = link_re
        spec.update(pick_card_markers(catalog, link_re))
        spec["title"] = pick_title_marker(catalog, link_re)
        spec["poster"] = pick_poster_marker(catalog, link_re)

    # детальная страница — по первой карточке
    first = re.search(spec["link_re"], home)
    if first:
        detail_url = _abs(base, first.group(0))
        _, detail = recon.fetch_html(detail_url)
        spec.setdefault("desc", pick_desc_marker(detail))
        spec.setdefault("genres", pick_genres(detail))
        spec.setdefault("player", pick_player(detail))
        spec["detail_url"] = detail_url

    return spec
