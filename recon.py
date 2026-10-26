"""Разведка сайта по одной ссылке: ищет JSON API и заполняет декларацию.

Задача — сделать `forge add <url>` самодостаточным: пользователь даёт ссылку,
генератор сам находит API, эндпоинты и поля. Если что-то не нашлось,
`profile()` вернёт список пробелов, чтобы их можно было дописать в YAML.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request

UA = "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"

LIST_HINTS = ("search", "catalog", "titles", "manga", "series", "comics", "list")
ITEM_ID_KEYS = ("id", "_id", "slug", "uuid", "url")
ITEM_TITLE_KEYS = ("name", "title", "name_ru", "title_ru", "rus_name", "name_en")
COVER_KEYS = ("cover_image", "coverImage", "cover_url", "cover", "image", "poster")
DESC_KEYS = ("description", "synopsis", "summary", "annotation")
STATUS_KEYS = ("status", "publication_status", "translation_status")
GENRE_KEYS = ("genres", "tags", "categories")
PAGE_KEYS = ("pages", "images", "page_list")
PAGINATION_TOTAL = ("total_pages", "pages", "last_page", "totalPages")
PAGINATION_CURRENT = ("current_page", "page", "currentPage")


def _get(url: str, headers: dict | None = None, timeout: int = 25) -> tuple[int, str]:
    request = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        return error.code, ""
    except Exception:
        return 0, ""


def _get_json(url: str, headers: dict | None = None):
    status, body = _get(url, headers)
    if status != 200 or not body[:1] in "[{":
        return None
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return None


def site_name(html: str, host: str) -> str:
    for pattern in (
        r'<meta[^>]+property="og:site_name"[^>]+content="([^"]+)"',
        r"<title>([^<]+)</title>",
    ):
        match = re.search(pattern, html, re.I)
        if match:
            name = re.sub(r"\s*[|—–-].*$", "", match.group(1)).strip()
            if 2 <= len(name) <= 30:
                return name
    return host.split(".")[0].capitalize()


ANIME_WORDS = (
    "аниме", "озвучка", "серия", "эпизод", "anime", "episode",
    "сериал", "плеер", "player", "video", "видео",
)
NOVEL_WORDS = ("ранобэ", "новелла", "ранобе", "novel", "light novel", "книга")
NSFW_HOSTS = ("hentai", "henta", "adult", "porn", "xxx", "nude", "erotic", "18plus")
NSFW_STRONG = ("хентай", "hentai", "порно", "porn", "xxx", "тентакли")
NSFW_MEDIUM = ("18+", "этти", "ecchi", "adult", "nsfw", "для взрослых", "эротика")


def detect_kind(html: str, url: str) -> str:
    """Определяет тип сайта: mihon (манга), aniyomi (аниме), lnreader (ранобэ)."""
    host = _host(url).lower()
    path = url.lower()
    text = html.lower()

    if any(word in host or word in path for word in ("ranobe", "novel", "книг", "book")):
        return "lnreader"
    if any(word in host or word in path for word in ("anime", "аниме", "video", "serial")):
        return "aniyomi"

    novel_hits = sum(1 for word in NOVEL_WORDS if word in text)
    anime_hits = sum(1 for word in ANIME_WORDS if word in text)
    if novel_hits >= 3 and novel_hits > anime_hits:
        return "lnreader"
    if anime_hits >= 4 and anime_hits > novel_hits * 2:
        return "aniyomi"
    return "mihon"


def detect_nsfw(html: str, url: str) -> bool:
    """Определяет, что сайт для взрослых (18+)."""
    host = _host(url).lower()
    if any(word in host for word in NSFW_HOSTS):
        return True
    text = html.lower()
    strong = sum(text.count(word) for word in NSFW_STRONG)
    medium = sum(text.count(word) for word in NSFW_MEDIUM)
    # «18+», «этти» и «эротика» встречаются и на обычных сайтах (жанр в
    # каталоге), поэтому смотрим на плотность: у взрослых сайтов явные
    # слова повторяются десятками раз.
    return strong >= 3 or medium >= 8


def detect_content_rating(html: str, url: str) -> str:
    """Три уровня рейтинга: safe | mixed | nsfw (как ContentWarning в Mihon)."""
    host = _host(url).lower()
    if any(word in host for word in NSFW_HOSTS):
        return "nsfw"
    text = html.lower()
    strong = sum(text.count(word) for word in NSFW_STRONG)
    medium = sum(text.count(word) for word in NSFW_MEDIUM)
    if strong >= 3:
        return "nsfw"
    if strong >= 1 or medium >= 3:
        return "mixed"
    return "safe"


def _walk_arrays(node, path=""):
    if isinstance(node, list):
        if node and isinstance(node[0], dict):
            yield path, node
        return
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _walk_arrays(value, f"{path}.{key}" if path else key)


def _pick_keys(item: dict, candidates) -> str:
    for key in candidates:
        if key in item:
            return key
    return ""


def detect_list(payload) -> dict:
    """Первый массив объектов, похожий на список тайтлов."""
    best = None
    for path, items in _walk_arrays(payload):
        sample = items[0]
        id_key = _pick_keys(sample, ITEM_ID_KEYS)
        title_key = _pick_keys(sample, ITEM_TITLE_KEYS)
        if not id_key or not title_key:
            continue
        score = 2 + (1 if _pick_keys(sample, COVER_KEYS) else 0)
        if best is None or score > best[0]:
            best = (score, path, items, id_key, title_key)
    if not best:
        return {}
    _, path, items, id_key, title_key = best
    sample = items[0]
    fields = {
        "id": id_key,
        "title": title_key,
        "cover": _pick_keys(sample, COVER_KEYS),
        "description": _pick_keys(sample, DESC_KEYS),
        "status": _pick_keys(sample, STATUS_KEYS),
        "genres": _pick_keys(sample, GENRE_KEYS),
    }
    return {
        "items": path,
        "fields": {key: value for key, value in fields.items() if value},
        "sample": sample,
    }


def find_pagination(payload) -> dict:
    found = {}

    def scan(node, path=""):
        if isinstance(node, dict):
            for key, value in node.items():
                if isinstance(value, (int, str)) and str(value).isdigit():
                    if key in PAGINATION_TOTAL:
                        found.setdefault("total_pages", f"{path}.{key}".strip("."))
                    if key in PAGINATION_CURRENT:
                        found.setdefault("current_page", f"{path}.{key}".strip("."))
                else:
                    scan(value, f"{path}.{key}" if path else key)

    scan(payload)
    return found


def _origin(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def _host(url: str) -> str:
    return urllib.parse.urlsplit(url).netloc.split(":")[0]


def _candidate_roots(url: str, html: str, bundles: list[str]) -> list[str]:
    host = _host(url)
    roots = []
    for bundle in bundles:
        for match in re.findall(r'["\']((?:https?://[^"\']+)?/api/[a-zA-Z0-9/_{}.-]*)["\']', bundle):
            roots.append(match)
    roots.extend(
        [
            f"https://api.{host}/api/v1",
            f"https://api.{host}/api",
            f"{_origin(url)}/api/v1",
            f"{_origin(url)}/api",
        ]
    )
    cleaned = []
    for root in roots:
        if root.startswith("/"):
            root = _origin(url) + root
        match = re.match(r"(https?://[^/]+/api(?:/[a-z0-9]+)?)", root, re.I)
        if not match:
            continue
        value = match.group(1)
        if value in cleaned:
            continue
        cleaned.append(value)

    def score(root: str) -> int:
        host = _host(root)
        value = 0
        if host.startswith("api."):
            value -= 2
        if "/v1" in root or "/v2" in root:
            value -= 1
        if "proxy" in root:
            value += 2
        return value

    return sorted(cleaned, key=lambda item: (score(item), len(item)))


def _bundle_urls(url: str, html: str) -> list[str]:
    sources = re.findall(r'<script[^>]+src="([^"]+\.js[^"]*)"', html)
    result = []
    for source in sources[:8]:
        if source.startswith("http"):
            result.append(source)
        elif source.startswith("/"):
            result.append(_origin(url) + source)
        else:
            result.append(_origin(url) + "/" + source)
    return result


def profile(url: str, key_header: str = "", key: str = "") -> dict:
    """Возвращает черновик декларации + список того, что не удалось найти."""
    url = url.rstrip("/")
    status, html = _get(url)
    if status != 200:
        return {"error": f"HTTP {status} для {url}", "url": url}

    host = _host(url)
    name = site_name(html, host)
    bundles = []
    for bundle_url in _bundle_urls(url, html):
        code, body = _get(bundle_url)
        if code == 200:
            bundles.append(body)

    headers = {key_header: key} if key_header and key else {}
    result = {
        "url": url,
        "host": host,
        "name": name,
        "bundles": len(bundles),
        "kind": detect_kind(html, url),
        "nsfw": detect_nsfw(html, url),
        "rating": detect_content_rating(html, url),
        "base": "",
        "list_path": "",
        "items": "",
        "fields": {},
        "pagination": {},
        "gaps": [],
    }

    for root in _candidate_roots(url, html, bundles):
        for hint in LIST_HINTS:
            for path in (f"/{hint}", f"/manga/{hint}", f"/titles/{hint}"):
                probe = f"{root}{path}"
                separator = "&" if "?" in probe else "?"
                payload = _get_json(f"{probe}{separator}page=1&per_page=5&limit=5", headers)
                if not isinstance(payload, dict):
                    continue
                detected = detect_list(payload)
                if not detected:
                    continue
                result.update(
                    {
                        "base": root,
                        "list_path": path,
                        "items": detected["items"],
                        "fields": detected["fields"],
                        "pagination": find_pagination(payload),
                    }
                )
                break
            if result["list_path"]:
                break
        if result["list_path"]:
            break

    if not result["list_path"]:
        try:
            import smartparse
            learned = smartparse.learn_list(html, url)
        except Exception:
            learned = {}
        if learned:
            result["html"] = learned
            result["gaps"].append("html-режим: детали/главы/страницы")
        else:
            result["gaps"].append("список тайтлов (попробуйте указать api.base и api.list.path вручную)")
        return result

    sample_id = _sample_id(result, headers)
    for label, suffixes in (
        ("details", ("",)),
        ("chapters", ("/chapters", "/issues")),
        ("pages", ("/pages",)),
    ):
        if label == "details":
            found = _probe_details(result, sample_id, headers)
        else:
            found = _probe_child(result, label, sample_id, headers, suffixes)
        if not found:
            result["gaps"].append(label)
    return result


def _sample_id(result: dict, headers) -> str:
    separator = "&" if "?" in result["base"] + result["list_path"] else "?"
    payload = _get_json(
        f'{result["base"]}{result["list_path"]}{separator}page=1&per_page=1&limit=1', headers
    )
    try:
        node = payload
        for segment in result["items"].split("."):
            node = node[segment]
        item = node[0]
        return str(item[result["fields"]["id"]])
    except Exception:
        return ""


def _probe_details(result: dict, sample_id: str, headers) -> bool:
    base = result["base"]
    for pattern in (f"/titles/{sample_id}", f"/manga/{sample_id}", f"/series/{sample_id}"):
        payload = _get_json(base + pattern, headers)
        if isinstance(payload, dict):
            return True
    return False


def _probe_child(result: dict, label: str, sample_id: str, headers, suffixes) -> bool:
    base = result["base"]
    chapter_id = ""
    for pattern in (f"/titles/{sample_id}", f"/manga/{sample_id}", f"/series/{sample_id}"):
        chapter_id = chapter_id or _find_first_id(_get_json(base + pattern, headers), ITEM_ID_KEYS)
    if not chapter_id:
        chapter_id = sample_id
    for suffix in suffixes:
        payload = _get_json(f"{base}/{label}/{chapter_id}{suffix}" if label == "pages" else f"{base}/{label}{suffix}", headers)
        if payload is not None:
            return True
    return False


def _find_first_id(node, keys) -> str:
    def walk(value):
        if isinstance(value, dict):
            for key in keys:
                if key in value and isinstance(value[key], (str, int)):
                    return str(value[key])
            for child in value.values():
                found = walk(child)
                if found:
                    return found
        if isinstance(value, list):
            for child in value[:3]:
                found = walk(child)
                if found:
                    return found
        return ""

    return walk(node)


def _yaml_str(value: str) -> str:
    escaped = (value or "").replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _build_html_yaml(result: dict, site_id: str, lang: str) -> str:
    """Декларация для HTML-сайта (без JSON API) из подсказок smartparse.

    smartparse даёт только список карточек; детали/главы/страницы требуют
    отдельного разбора страницы тайтла — они остаются TODO с примером.
    """
    html = result.get("html") or {}
    lines = [
        f"# {result['name']} — {result['host']} (разведка {result['url']})",
        f"id: {site_id}",
        f"kind: {result.get('kind') or 'mihon'}",
        f"name: {_yaml_str(result['name'])}",
        f"base_url: {result['url']}",
        f"lang: {lang}",
        "api_style: html",
        f"nsfw: {str(bool(result.get('nsfw'))).lower()}",
        f"rating: {result.get('rating') or 'safe'}",
        "",
        "html:",
        "  rate: 3",
        "  list:",
        '    path: "/"            # TODO: страница каталога/популярного, если не "/"',
        '    latest_path: "/"      # TODO: страница последних обновлений',
        '    search_path: ""       # TODO: например "/search?q={query}"',
        f"    items: {_yaml_str(html.get('item_selector') or '.card a')}",
        f"    link: {_yaml_str(html.get('item_selector') or 'a')}",
        f"    cover: {_yaml_str('img')}",
        '    has_next: ""          # TODO: селектор кнопки "далее", если список постраничный',
        "  details:",
        '    title: { selector: "h1" }   # TODO: проверить на странице тайтла',
        '    cover: ".cover img, .poster img"',
        '    description: { selector: ".description" }',
        '    genres: { selector: ".genres" }',
        "  chapters:",
        '    items: ".chapter-list a"   # TODO: селектор строки главы',
        '    link: "a"',
        "  pages:",
        '    images: ".reader img"      # TODO: селектор картинок читалки',
    ]
    if html.get("sample"):
        lines.append("")
        lines.append("# найдено карточек: " + str(html.get("items_found", "?")))
        for item in html["sample"][:3]:
            lines.append(f"#   - {item.get('title', '')!r} -> {item.get('url', '')}")
    lines.append("")
    lines.append(
        "# TODO: это каркас, не готовое расширение. smartparse нашёл только карточки\n"
        "# списка (см. образцы выше) — селекторы деталей/глав/страниц надо\n"
        "# проверить на реальной странице тайтла и поправить руками, затем:\n"
        "#   python3 forge.py generate sites/" + site_id + ".yaml"
    )
    return "\n".join(lines) + "\n"


def build_yaml(result: dict, site_id: str, lang: str = "ru") -> str:
    """Черновик sites/<id>.yaml из результатов разведки."""
    if not result.get("list_path") and result.get("html"):
        return _build_html_yaml(result, site_id, lang)

    lines = [
        f"# {result['name']} — {result['host']} (разведка {result['url']})",
        f"id: {site_id}",
        f"kind: {result.get('kind') or 'mihon'}",
        f"name: {_yaml_str(result['name'])}",
        f"base_url: {result['url']}",
        f"lang: {lang}",
        "api_style: json_api_v2",
        f"nsfw: {str(bool(result.get('nsfw'))).lower()}",
        f"rating: {result.get('rating') or 'safe'}",
        "",
        "api:",
        f'  base: "{result["base"]}"',
        "  list:",
        f'    path: "{result["list_path"]}"',
        f'    items: "{result["items"]}"',
        "    page_param: page",
        '    size_param: "per_page"',
        "    size: 20",
        '    query_param: "q"',
    ]
    fields = result.get("fields") or {}
    if fields:
        lines.append("    fields:")
        for key, value in fields.items():
            lines.append(f"      {key}: {value}")
    pagination = result.get("pagination") or {}
    if pagination.get("total_pages"):
        lines.append(f'    total_pages: "{pagination["total_pages"]}"')
    if pagination.get("current_page"):
        lines.append(f'    current_page: "{pagination["current_page"]}"')
    if result.get("gaps"):
        lines.append("")
        lines.append("# TODO, не найдено автоматически: " + ", ".join(result["gaps"]))
    return "\n".join(lines) + "\n"
