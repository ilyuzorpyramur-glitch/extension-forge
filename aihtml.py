#!/usr/bin/env python3
"""AI-парсер для HTML-сайтов.

Разведка понимает только API-сайты; для обычной вёрстки шаблон даёт скелет с
TODO. Здесь модель получает образцы страниц и рабочий пример соседнего
модуля — и возвращает готовый файл источника, который дальше проверяют
сборка и стенд.
"""

from __future__ import annotations

import re
import urllib.request
from pathlib import Path

import gemini
import recon

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
MAX_SAMPLE = 6000


def fetch(url: str) -> str:
    status, body = recon.fetch_html(url)
    return body if status == 200 else ""


def trim(html: str, limit: int = MAX_SAMPLE) -> str:
    """Убирает скрипты и стили: модели нужна разметка, а не килобайты JS."""
    text = re.sub(r"<script.*?</script>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<svg.*?</svg>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    text = re.sub(r"\s+", " ", text)
    return text[:limit]


CATALOG_WORDS = {
    "aniyomi": ("аниме", "сериалы", "фильмы", "каталог", "озвучка", "мультфильмы"),
    "mihon": ("манга", "манхва", "каталог", "читать"),
    "lnreader": ("ранобэ", "новеллы", "книги", "каталог"),
}


def _item_links(html: str) -> list[str]:
    """Ссылки на карточки тайтлов. У DLE это /12345-slug.html."""
    links = re.findall(r'href="([^"]+)"', html)
    clean = [link for link in links if not any(
        bad in link.lower() for bad in (".css", ".js", ".png", ".jpg", "/templates/", "/engine/", "#", "rss.xml")
    )]
    dle = [link for link in clean if re.search(r"/\d{2,}-[^/]*\.html", link)]
    if dle:
        return dle
    return [link for link in clean if re.search(r"/(anime|manga|ranobe|novel)/[\w-]{3,}/$", link)
            and not re.search(r"/(anime|manga|ranobe)/(page|tag)/", link)]


def _pagination_link(html: str) -> str:
    for href in re.findall(r'href="([^"]+)"', html):
        if re.search(r"(page/2|PAGEN_1=2|\?page=2|start=)", href, re.I):
            return href
    return ""


def discover_catalog(base_url: str, kind: str = "aniyomi") -> dict:
    """Находит раздел каталога и шаблоны ссылок по самой вёрстке.

    Разведка ищет API, но у обычных сайтов его нет: каталог — это страница
    раздела. Без этого модель выдумывает пути вида /anime/page/2/.
    """
    base = base_url.rstrip("/")
    home = fetch(base + "/")
    if not home:
        return {}

    words = CATALOG_WORDS.get(kind, CATALOG_WORDS["aniyomi"])
    candidates: list[tuple[int, str]] = [(0, base + "/")]
    # Типовые разделы: на них часто нет ссылки в меню, но каталог живёт там.
    for section in ("/anime/", "/manga/", "/ranobe/", "/catalog/"):
        candidates.append((0, base + section))
    for href, text in re.findall(r'href="([^"]+)"[^>]*>([^<]{2,40})', home):
        label = text.strip().lower()
        if not any(word in label for word in words):
            continue
        url = href if href.startswith("http") else base + href
        if url.rstrip("/") == base:
            continue
        candidates.append((1, url))

    best = {"score": -1, "url": "", "items": [], "pagination": ""}
    fallback = {"score": -1, "url": "", "items": [], "pagination": ""}
    for _priority, url in candidates[:8]:
        html = fetch(url)
        if len(html) < 2000:
            continue
        items = _item_links(html)
        pagination = _pagination_link(html)
        score = len(items) * 2 + (12 if pagination else 0)
        # Раздел вида /anime/ — это каталог, а не главная с подборками.
        if re.search(r"/(anime|manga|ranobe|novel)/?$", url):
            score += 10
        entry = {"score": score, "url": url, "items": items, "pagination": pagination}
        # Каталог — это страница с листанием. Главная без пагинации годится
        # только как запасной вариант.
        if pagination and score > best["score"]:
            best = entry
        elif not pagination and score > fallback["score"]:
            fallback = entry

    # Раздел вида /anime/ — это и есть каталог: он шире любой подборки.
    sections = []
    for _priority, url in candidates[:8]:
        if not re.search(r"/(anime|manga|ranobe|novel)/?$", url):
            continue
        html = fetch(url)
        items = _item_links(html)
        if len(items) >= 5:
            sections.append({"score": len(items), "url": url, "items": items,
                             "pagination": _pagination_link(html)})
    if sections:
        sections.sort(key=lambda item: (bool(item["pagination"]), item["score"]), reverse=True)
        best = sections[0]

    if not best["url"]:
        best = fallback
    if not best["url"]:
        return {}

    if best["pagination"] and not best["pagination"].startswith("http"):
        best["pagination"] = base + best["pagination"]

    sample_items = list(dict.fromkeys(best["items"]))[:3]
    detail_url = ""
    for link in sample_items:
        candidate = link if link.startswith("http") else base + link
        page = fetch(candidate)
        if len(page) > 3000:
            detail_url = candidate
            break

    return {
        "catalog_url": best["url"],
        "item_count": best["score"],
        "sample_items": sample_items,
        "detail_url": detail_url,
        "pagination_url": best["pagination"],
    }


def samples(base_url: str, list_path: str = "") -> dict:
    """Главная, список, карточка тайтла — то, по чему видно структуру сайта."""
    base = base_url.rstrip("/")
    home = fetch(base + "/")
    listing_url = base + (list_path or "/")
    listing = fetch(listing_url) if listing_url != base + "/" else home

    detail_url = ""
    detail = ""
    # Ссылки на css/js/служебные скрипты отбрасываем, карточку ищем по смыслу
    # и по размеру: у настоящей страницы тайтла разметки заметно больше.
    bad = (".css", ".js", ".png", ".jpg", ".svg", ".xml", "/templates/", "/static/",
           "/uploads/", "engine/", "opensearch", "search", "login", "register", "rss")
    candidates = []
    for link in re.findall(r'href="([^"#]+)"', listing):
        low = link.lower()
        if any(marker in low for marker in bad):
            continue
        score = 0
        if re.search(r"\d{2,}", low):
            score += 2
        if any(word in low for word in ("anime", "seria", "release", "animez")):
            score += 2
        if len(low.split("/")) >= 2:
            score += 1
        if score:
            candidates.append((score, link))

    for _score, link in sorted(candidates, reverse=True)[:3]:
        url = base + link if link.startswith("/") else link
        page = fetch(url)
        if len(page) > 2000:
            detail_url, detail = url, page
            break

    detail = fetch(detail_url) if detail_url else ""

    return {
        "home": trim(home),
        "listing": trim(listing),
        "detail": trim(detail),
        "detail_url": detail_url,
    }


def reference(repo: Path, exclude: str = "") -> str:
    """Рабочий модуль как образец API: у legacy-плагина свои сигнатуры."""
    for path in sorted(repo.glob("src/ru/*/src/**/*.kt")):
        if exclude and exclude in str(path):
            continue
        if path.name in ("Dto.kt", "KodikExtractor.kt"):
            continue
        text = path.read_text(encoding="utf-8")
        if "class " in text and "override" in text:
            return text[:3200]
    return ""


PROMPT = """Ты пишешь расширение Aniyomi (legacy-плагин) для русского сайта.

Сайт: {name} ({base_url})
Класс: {cls}
Пакет: {pkg}
baseUrl: {base_url}

Задача: вернуть ПОЛНЫЙ файл {cls}.kt, который компилируется и работает.
Требования:
- только Kotlin-код, без markdown и пояснений;
- API как в примере ниже (AnimeHttpSource, AnimeRequest/AnimeResponse, SAnime/SEpisode/SVideo);
- разбор через org.jsoup.Jsoup, абсолютные url через baseUrl + путь;
- серии — по убыванию номера; poster_url абсолютный; video — ссылка на плеер или iframe;
- никаких TODO, заглушек и выдуманных эндпоинтов: только то, что видно в образцах.

Пример рабочего модуля (структура и стиль):
```kotlin
{reference}
```

Найденные адреса (используй их как шаблоны, не выдумывай пути):
- каталог: {catalog_url}
- карточка тайтла: {detail_url}
- пагинация: {pagination_url}
- примеры карточек: {sample_items}

Образец главной страницы:
```html
{home}
```

Образец списка тайтлов:
```html
{listing}
```

Образец карточки тайтла ({detail_url}):
```html
{detail}
```
"""


def build(site, module_dir: Path, repo: Path) -> tuple[bool, str]:
    """Пишет файл источника. Возвращает (успех, что сделано/почему нет)."""
    if not gemini.available():
        return False, "нет ключа Gemini — HTML-сайт оставлен скелетом"

    samples_data = samples(site.base_url, (site.selectors or {}).get("list_path", ""))
    if not samples_data.get("home") or len(samples_data["home"]) < 500:
        return False, "страницы не открылись — нечего разбирать"

    sources = sorted(module_dir.rglob("*.kt"))
    if not sources:
        return False, "нет файла-скелета"
    target = sources[0]

    navigation = discover_catalog(site.base_url, site.kind)
    prompt = PROMPT.format(
        catalog_url=navigation.get("catalog_url") or f"{site.base_url}/",
        detail_url_found=navigation.get("detail_url", ""),
        pagination_url=navigation.get("pagination_url", "не найдена — ищи в разметке"),
        sample_items=", ".join(navigation.get("sample_items", [])[:3]) or "нет",
        name=site.name,
        base_url=site.base_url,
        cls=target.stem,
        pkg=".".join(target.parts[-6:-4]),
        reference=reference(repo, exclude=f"/{site.site_id}/"),
        home=samples_data["home"],
        listing=samples_data["listing"],
        detail=samples_data["detail"] or "карточка не открылась",
        detail_url=samples_data["detail_url"],
    )
    code = _extract(gemini.ask(prompt, max_tokens=6000, temperature=0.15))
    if not _looks_like_source(code):
        # Один повтор: модель часто отвечает прозой, если её об этом не толкнуть.
        retry = prompt + (
            "\n\nВАЖНО: ответ должен содержать ТОЛЬКО код Kotlin, без пояснений,"
            " без markdown, начиная с package и заканчивая закрывающей скобкой класса."
        )
        code = _extract(gemini.ask(retry, max_tokens=6000, temperature=0.1))
    if not _looks_like_source(code):
        return False, "модель вернула не код (две попытки)"
    if "TODO(" in code:
        # Модель любит оставлять заглушки: просим дописать полностью.
        again = prompt + (
            "\n\nВАЖНО: не оставляй TODO(...), заглушек и пустых методов."
            " Реализуй каждый метод полностью по образцам разметки выше."
            " Ответ — только код Kotlin."
        )
        code = _extract(gemini.ask(again, max_tokens=6000, temperature=0.1))
        if not _looks_like_source(code) or "TODO(" in code:
            return False, "в ответе остались TODO (две попытки)"
    if code.count("{") != code.count("}"):
        return False, "несбалансированные скобки"

    target.write_text(code.rstrip() + "\n", encoding="utf-8")
    return True, f"AI-парсер написан ({len(code)} символов)"


FENCE = re.compile(r"```[a-zA-Z]*\s*\n(.*?)```", re.S)


def _extract(answer: str) -> str:
    """Код из ответа: блок в ```…```, иначе часть от package/import до конца."""
    if not answer:
        return ""
    blocks = FENCE.findall(answer)
    if blocks:
        longest = max(blocks, key=len)
        return longest.strip()
    text = answer.strip()
    start = re.search(r"^\s*(package|import)\s+", text, re.M)
    if start:
        text = text[start.start():]
    return text.strip()


def _looks_like_source(code: str) -> bool:
    """Минимальная проверка: это Kotlin-класс источника, а не проза."""
    if not code or "class " not in code or "override" not in code:
        return False
    if code.count("{") != code.count("}"):
        return False
    return "fun " in code
