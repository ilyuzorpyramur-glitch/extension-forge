"""Умное определение HTML-структуры сайта.

Три уровня, от простого к тяжёлому:
  1. статистика по DOM (без зависимостей) — группируем ссылки по родителю;
  2. mlscraper — обучение селекторов по примерам (если установлен);
  3. Scrapling — адаптивный парсинг и обход антибота (если установлен).

Результат — словарь селекторов, который генератор кладёт в декларацию.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from urllib.parse import urljoin, urlsplit

try:  # необязательные усилители
    import mlscraper  # noqa: F401
    HAS_MLSCRAPER = True
except Exception:
    HAS_MLSCRAPER = False

try:
    from scrapling import Fetcher
    HAS_SCRAPLING = True
except Exception:
    HAS_SCRAPLING = False

TITLE_ATTRS = ("title", "aria-label")
COVER_HINTS = ("img", "cover", "poster", "thumb")


def fetch(url: str) -> str:
    """HTML с антибот-обходом, если доступен Scrapling."""
    if HAS_SCRAPLING:
        try:
            page = Fetcher.get(url, timeout=30)
            body = getattr(page, "html_content", None) or getattr(page, "body", None)
            if body:
                return body if isinstance(body, str) else body.decode("utf-8", "replace")
        except Exception:
            pass
    import urllib.request

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/120.0 Safari/537.36",
            "Accept-Language": "ru,en;q=0.8",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", "replace")


def _anchors(html: str, base: str) -> list[dict]:
    """Собирает ссылки: href, текст, title, ближайшая картинка, подпись родителя."""
    result = []
    pattern = re.compile(
        r'<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.I | re.S
    )
    for match in pattern.finditer(html):
        href, inner = match.group(1), match.group(2)
        if href.startswith(("#", "javascript:", "mailto:")):
            continue
        text = re.sub(r"<[^>]+>", " ", inner)
        text = re.sub(r"\s+", " ", text).strip()
        title = ""
        title_match = re.search(r'title="([^"]+)"', match.group(0))
        if title_match:
            title = title_match.group(1)
        image = ""
        img_match = re.search(r'<img\b[^>]*src="([^"]+)"', inner, re.I)
        if img_match:
            image = img_match.group(1)
        if not text and not image:
            continue
        before = html[max(0, match.start() - 400): match.start()]
        classes = re.findall(r'class="([^"]+)"', before)[-3:]
        result.append(
            {
                "href": urljoin(base, href),
                "text": text,
                "title": title,
                "image": urljoin(base, image) if image else "",
                "near": " ".join(classes),
            }
        )
    return result


def _signature(item: dict) -> str:
    path = urlsplit(item["href"]).path
    parts = [part for part in path.split("/") if part]
    if len(parts) >= 2:
        return "/" + parts[0] + "/"
    return path


def learn_list(html: str, base: str, min_items: int = 6) -> dict:
    """Ищет повторяющийся список карточек и поля внутри карточки."""
    anchors = _anchors(html, base)
    if not anchors:
        return {}
    groups: dict[str, list[dict]] = defaultdict(list)
    for item in anchors:
        groups[_signature(item)].append(item)

    best = None
    for signature, items in groups.items():
        unique = {item["href"] for item in items}
        if len(unique) < min_items:
            continue
        with_cover = sum(1 for item in items if item["image"])
        score = len(unique) + with_cover
        if best is None or score > best[0]:
            best = (score, signature, items)
    if not best:
        return {}

    _, signature, items = best
    near = Counter(item["near"] for item in items if item["near"]).most_common(1)
    card_class = near[0][0].split()[-1] if near else ""
    has_cover = any(item["image"] for item in items)
    has_title_attr = any(item["title"] for item in items)

    return {
        "list_url": base,
        "item_selector": f'a[href*="{signature}"]' if signature else "a[href]",
        "card_selector": f".{card_class}" if card_class else "",
        "title_attr": "title" if has_title_attr else "",
        "cover_inside": has_cover,
        "items_found": len(items),
        "sample": [{"title": item["title"] or item["text"], "url": item["href"]} for item in items[:3]],
        "engine": "statistical" if not HAS_MLSCRAPER else "statistical+mlscraper",
    }


def learn_with_mlscraper(html: str, base: str, samples: list[dict]) -> dict:
    """Дообучение селекторов по вручную подтверждённым примерам (mlscraper)."""
    if not HAS_MLSCRAPER or not samples:
        return {}
    try:
        from mlscraper.html import Page
        from mlscraper.training import train_scraper

        page = Page(html)
        # samples: [{"title": "...", "url": "/manga/x"}] — обучаем на тексте заголовков
        scraper = train_scraper(page, samples)
        return {"mlscraper": str(scraper)}
    except Exception:
        return {}
