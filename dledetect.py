#!/usr/bin/env python3
"""Автоконфиг DLE-сайтов: параметры парсера берутся из самой вёрстки.

DLE — самый частый движок русских аниме-сайтов. Вместо свободного кода модели
здесь определяются каталог, шаблон карточки, пагинация, селекторы и режим
плеера — и рендерер собирает предсказуемый парсер.
"""

from __future__ import annotations

import re

import aihtml
import recon

DEFAULT_PAGE_SUFFIX = "page/{page}/"
DLE_LINK = r"/\d+-[a-z0-9\-]+\.html"


def is_dle(html: str) -> bool:
    lowered = (html or "").lower()
    return "/engine/" in lowered or "dle_" in lowered or 'id="dle-content"' in lowered


def _home(url: str) -> str:
    return recon.fetch_html(url.rstrip("/") + "/")[1]


def _detail_url(nav: dict) -> str:
    return nav.get("detail_url") or ""


def _pick_description(html: str) -> str:
    """Селектор описания: «текстовый» блок, но не комментарии."""
    best, best_len = "", 0
    for match in re.finditer(r'<div[^>]+class="([^"]*)"', html):
        classes = match.group(1).split()
        joined = " ".join(classes).lower()
        if not any(word in joined for word in ("fdesc", "full-text", "full-story", "description")):
            continue
        # Комментарии тоже «full-text», но это не описание тайтла.
        if any(word in joined for word in ("comm", "comment")):
            continue
        segment = html[match.end():match.end() + 4000]
        plain = re.sub(r"<[^>]+>", " ", segment)
        length = len(plain.strip())
        if length > best_len:
            best_len, best = length, "." + ".".join(classes[:2])
    return best or ".fdesc"


def _pick_genres(html: str) -> str:
    """Жанры — повторяющиеся ссылки одного раздела, но не каталог."""
    prefix_re = re.compile(r'href="(?:https?://[^/"]+)?(/(?:genres?|xfsearch|tags?)/)', re.I)
    counts: dict[str, int] = {}
    for match in prefix_re.finditer(html):
        prefix = match.group(1)
        counts[prefix] = counts.get(prefix, 0) + 1
    if not counts:
        return "a[href*='/genre/']"
    # /genres/ — именно жанры; /xfsearch/ часто служит фильтрами типа.
    if counts.get("/genres/"):
        return "a[href*='/genres/']"
    best = max(counts.items(), key=lambda pair: pair[1])
    return f"a[href*='{best[0]}']"


def _pick_player(html: str) -> tuple[str, str]:
    """Режим плеера: iframe прямо на странице или relay по id."""
    if re.search(r'data-iframe="([^"]+)"', html) or "<iframe" in html.lower():
        return "iframe", ""
    if 'data-player-anime-id' in html:
        return "relay", "/engine/ajax/controller.php?mod=player&id={id}&slot={slot}"
    return "iframe", ""


def _pick_search(html: str) -> str:
    form = re.search(r"<form[^>]*action=\"([^\"]*)\"[^>]*>(.*?)</form>", html, re.S | re.I)
    if form:
        names = re.findall(r'name="([^"]+)"', form.group(2))
        query_key = next((name for name in names if name.lower() in ("story", "q", "search", "query")), "")
        if query_key:
            action = form.group(1) or "/"
            return f"{action}?{query_key}={{query}}"
    return "/?do=search&subaction=search&story={query}"


def config(base_url: str, kind: str = "aniyomi") -> dict:
    """Готовый конфиг для forge_aniyomi_dle. Пустой словарь — сайт не DLE."""
    base = base_url.rstrip("/")
    home = _home(base)
    if not is_dle(home):
        return {}

    nav = aihtml.discover_catalog(base, kind)
    catalog_url = nav.get("catalog_url") or base + "/"
    catalog_path = catalog_url[len(base):] or "/"
    if not catalog_path.endswith("/"):
        catalog_path += "/"

    page_suffix = DEFAULT_PAGE_SUFFIX
    pagination = nav.get("pagination_url", "")
    match = re.search(r"/page/2/?$", pagination)
    if match:
        page_suffix = "page/{page}/"
    elif re.search(r"PAGEN_1=2", pagination, re.I):
        page_suffix = "?PAGEN_1={page}"

    detail_url = _detail_url(nav)
    detail = recon.fetch_html(detail_url)[1] if detail_url else ""

    player_mode, relay = _pick_player(detail or home)

    return {
        "catalog": catalog_path,
        "latest": catalog_path,
        "search": _pick_search(home),
        "page_suffix": page_suffix,
        "page_marker": "page/",
        "link_re": DLE_LINK,
        "title_sel": "h1",
        "poster_sel": "img",
        "desc_sel": _pick_description(detail),
        "genre_sel": _pick_genres(detail or home),
        "relay": relay,
        "player_mode": player_mode,
    }
