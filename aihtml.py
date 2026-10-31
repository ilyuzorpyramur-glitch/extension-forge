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
    try:
        request = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ru,en;q=0.8"})
        with urllib.request.urlopen(request, timeout=30) as response:
            return recon.decode_html(response.read(), response.headers.get("Content-Type", ""))
    except Exception:
        return ""


def trim(html: str, limit: int = MAX_SAMPLE) -> str:
    """Убирает скрипты и стили: модели нужна разметка, а не килобайты JS."""
    text = re.sub(r"<script.*?</script>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<svg.*?</svg>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    text = re.sub(r"\s+", " ", text)
    return text[:limit]


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

    prompt = PROMPT.format(
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
    code = gemini.ask(prompt, max_tokens=6000, temperature=0.15)
    code = code.strip()
    if code.startswith("```"):
        code = code.split("```")[1]
        code = code.split("\n", 1)[1] if code.lower().startswith("kotlin") else code
    if "class " not in code or "override" not in code:
        return False, "модель вернула не код"
    if "TODO(" in code:
        return False, "в ответе остались TODO"
    if code.count("{") != code.count("}"):
        return False, "несбалансированные скобки"

    target.write_text(code.rstrip() + "\n", encoding="utf-8")
    return True, f"AI-парсер написан ({len(code)} символов)"
