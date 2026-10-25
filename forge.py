#!/usr/bin/env python3
"""extension-forge — генератор расширений и плагинов для Tadami-экосистемы.

Целевые репозитории:
  - mihon-ru-manga-repo   (манга,   Kotlin,  gradle, тема multichan/...)
  - aniyomi-ru-anime-repo (аниме,   Kotlin,  gradle, legacy-плагин)
  - lnreader-ru-plugins   (ранобэ,  TypeScript, LNReader Plugin API)

Команды:
  forge generate <site.yaml>   — сгенерировать расширение из декларации
  forge bump <target> <name>   — поднять версию расширения
  forge list                   — список известных сайтов
  forge watch                  — прогнать сторож по зарегистрированным расширениям
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

FORGE_ROOT = Path(__file__).resolve().parent
SITES_DIR = FORGE_ROOT / "sites"
TEMPLATES_DIR = FORGE_ROOT / "templates"
REGISTRY = FORGE_ROOT / "registry.json"

REPOS = {
    "mihon": Path("/home/work/repos/mihon-ru-manga-repo"),
    "aniyomi": Path("/home/work/repos/aniyomi-ru-anime-repo"),
    "lnreader": Path("/home/work/repos/lnreader-ru-plugins"),
}

# -----------------------------------------------------------------------------
# Декларация сайта
# -----------------------------------------------------------------------------


@dataclass
class SiteSpec:
    """Декларация сайта — всё, что нужно для генерации расширения."""

    site_id: str              # например "my_manga_site"
    kind: str                 # "mihon" | "aniyomi" | "lnreader"
    name: str                 # человекочитаемое имя ("My Manga Site")
    base_url: str             # https://example.com
    lang: str = "ru"
    theme: str = ""           # multisrc-тема для mihon (multichan, grouple...)
    version: str = "1.0.0"
    # селекторы для парсинга
    selectors: dict = field(default_factory=dict)
    # качества изображений/видео: [{"id": "720", "label": "720p"}, ...]
    qualities: list = field(default_factory=list)
    default_quality: str = ""
    nsfw: bool = False
    api_style: str = ""       # "html" | "json_api"
    notes: str = ""

    @classmethod
    def from_yaml(cls, path: Path) -> "SiteSpec":
        import yaml
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return cls(
            site_id=data["id"],
            kind=data["kind"],
            name=data["name"],
            base_url=data["base_url"].rstrip("/"),
            lang=data.get("lang", "ru"),
            theme=data.get("theme", ""),
            version=data.get("version", "1.0.0"),
            selectors=data.get("selectors", {}),
            qualities=data.get("qualities", []),
            default_quality=data.get("default_quality", ""),
            nsfw=data.get("nsfw", False),
            api_style=data.get("api_style", "html"),
            notes=data.get("notes", ""),
        )


# -----------------------------------------------------------------------------
# Генерация имён/пакетов
# -----------------------------------------------------------------------------


def package_name(site: SiteSpec) -> str:
    """ru.mymanagasite из 'My Manga Site'."""
    slug = re.sub(r"[^a-z0-9]", "", site.name.lower().replace(" ", ""))
    return f"{site.lang}.{slug}"


def class_name(site: SiteSpec) -> str:
    """MyMangaSite из 'My Manga Site'."""
    return re.sub(r"[^A-Za-z0-9]", "", site.name.title().replace(" ", ""))


def monogram(name: str) -> str:
    words = name.split()
    if len(words) >= 2:
        return (words[0][0] + words[1][0]).upper()
    return name[:2].upper()


# ---------------------------------------------------------------------------
# Ключевая механика: подключение модуля и bump версии
# ---------------------------------------------------------------------------


def next_version(version: str, bump: str = "patch") -> str:
    parts = [int(p) for p in version.split(".")]
    while len(parts) < 3:
        parts.append(0)
    if bump == "major":
        parts = [parts[0] + 1, 0, 0]
    elif bump == "minor":
        parts = [parts[0], parts[1] + 1, 0]
    else:
        parts[2] += 1
    return ".".join(map(str, parts))


def bump_mihon(repo: Path, site: SiteSpec) -> str:
    """versionCode = X → X+1, versionName из registry."""
    gradle_file = repo / "src" / site.lang / site.site_id / "build.gradle.kts"
    text = gradle_file.read_text(encoding="utf-8")
    m = re.search(r"versionCode\s*=\s*(\d+)", text)
    code = int(m.group(1)) + 1
    text = re.sub(r"versionCode\s*=\s*\d+", f"versionCode = {code}", text)
    gradle_file.write_text(text, encoding="utf-8")
    return str(code)


def bump_aniyomi(repo: Path, site: SiteSpec) -> str:
    gradle_file = repo / "src" / site.lang / site.site_id / "build.gradle"
    text = gradle_file.read_text(encoding="utf-8")
    m = re.search(r"extVersionCode\s*=\s*(\d+)", text)
    code = int(m.group(1)) + 1
    text = re.sub(r"extVersionCode\s*=\s*\d+", f"extVersionCode = {code}", text)
    gradle_file.write_text(text, encoding="utf-8")
    return str(code)


def bump_lnreader(repo: Path, site: SiteSpec) -> str:
    plugin_file = repo / "plugins" / "russian" / f"{site.site_id}.ts"
    text = plugin_file.read_text(encoding="utf-8")
    m = re.search(r"version\s*=\s*['\"]([\d.]+)['\"]", text)
    new_version = next_version(m.group(1))
    text = re.sub(
        r"version\s*=\s*['\"][\d.]+['\"]",
        f"version = '{new_version}'",
        text,
    )
    plugin_file.write_text(text, encoding="utf-8")
    return new_version


def bump_extension(site: SiteSpec) -> str:
    repo = REPOS[site.kind]
    if site.kind == "mihon":
        return bump_mihon(repo, site)
    if site.kind == "aniyomi":
        return bump_aniyomi(repo, site)
    return bump_lnreader(repo, site)


# ---------------------------------------------------------------------------
# Иконки
# ---------------------------------------------------------------------------


def generate_icons(site: SiteSpec) -> list[Path]:
    """Использует проверенный make-icons.py целевого репозитория."""
    repo = REPOS[site.kind]
    script = repo / ".github" / "scripts" / "make-icons.py"
    if not script.exists():
        raise FileNotFoundError(f"make-icons.py не найден в {repo}")
    ext_arg = site.site_id if site.kind != "lnreader" else site.site_id
    subprocess.run(
        [sys.executable, str(script), ext_arg],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    if site.kind == "lnreader":
        return [repo / "plugins" / "russian" / f"{site.site_id}.ts"]
    return sorted((repo / "src" / site.lang / site.site_id / "res").rglob("ic_launcher.png"))


# ---------------------------------------------------------------------------
# Генерация: mihon
# ---------------------------------------------------------------------------


def render_mihon(site: SiteSpec) -> None:
    repo = REPOS["mihon"]
    module = repo / "src" / site.lang / site.site_id
    pkg_dir = module / "src" / "eu" / "kanade" / "tachiyomi" / "extension" / site.lang / site.site_id
    pkg_dir.mkdir(parents=True, exist_ok=True)

    cls = class_name(site)
    pkg = package_name(site)

    # build.gradle.kts
    theme_block = f'\n    theme = "{site.theme}"' if site.theme else ""
    gradle = f"""plugins {{
    alias(kei.plugins.extension)
}}

keiyoushi {{
    name = "{site.name}"
    versionCode = 1
    libVersion = "1.4"{theme_block}

    source {{
        baseUrl {{
            custom("{site.base_url}")
        }}
        lang = "{site.lang}"
        id = {abs(hash(site.site_id)) % 900_000_000 + 1_000_000_000}L
    }}
}}
"""
    (module / "build.gradle.kts").write_text(gradle, encoding="utf-8")

    # Основной класс — расширяет multisrc-тему или HttpSource
    if site.theme:
        base_import = f"import eu.kanade.tachiyomi.multisrc.{site.theme}.{site.theme}"
        base_class = f"{site.theme}()"
    else:
        base_import = ""
        base_class = "HttpSource()"
    quality_prefs = _render_quality_prefs(site, "mihon")

    main_kt = f"""package {pkg}

{base_import}
import eu.kanade.tachiyomi.source.ConfigurableSource
import eu.kanade.tachiyomi.source.model.FilterList
import eu.kanade.tachiyomi.source.model.MangasPage
import eu.kanade.tachiyomi.source.model.SChapter
import eu.kanade.tachiyomi.source.model.SManga
import keiyoushi.utils.getPreferencesLazy{quality_prefs[0]}
import keiyoushi.utils.parseAs
import kotlinx.serialization.json.Json{quality_prefs[1]}

/**
 * Сгенерировано extension-forge.
 * Источник: {site.base_url}
 * {site.notes}
 */
@Source
abstract class {cls} :
    {base_class},
    ConfigurableSource() {{

    private val preferences by getPreferencesLazy()

    // ==== Качество по умолчанию (Настройки → Расширения → {site.name}) ====
{quality_prefs[2]}

    // ==== Популярное ====
    override fun popularMangaRequest(page: Int) =
        GET("$baseUrl/популярное?page=$page", headers)

    override fun popularMangaParse(response: Response): MangasPage {{
        // {site.selectors.get('popular', 'заполните селекторы в sites/' + site.site_id + '.yaml')}
        TODO("Реализуйте парсинг по декларации sites/{site.site_id}.yaml")
    }}

    // ==== Поиск ====
    override fun searchMangaRequest(page: Int, query: String, filters: FilterList) =
        GET("$baseUrl/search?q=$query&page=$page", headers)

    override fun searchMangaParse(response: Response): MangasPage =
        popularMangaParse(response)

    // ==== Детали ====
    override fun mangaDetailsParse(response: Response): SManga {{
        TODO("Реализуйте по селекторам details из sites/{site.site_id}.yaml")
    }}

    // ==== Главы ====
    override fun chapterListParse(response: Response): List<SChapter> {{
        TODO("Реализуйте по селекторам chapters из sites/{site.site_id}.yaml")
    }}

    // ==== Страницы (качество выбирается в настройках) ====
    override fun pageListParse(response: Response): List<Page> {{
        TODO("Реализуйте по селекторам pages из sites/{site.site_id}.yaml; качество: $defaultQuality")
    }}

    companion object {{
        const val baseUrl = "{site.base_url}"
    }}
}}
"""
    (pkg_dir / f"{cls}.kt").write_text(main_kt, encoding="utf-8")

    # Иконки
    generate_icons(site)


def _render_quality_prefs(site: SiteSpec, kind: str) -> tuple[str, str, str]:
    """Возвращает (import_suffix, import_json, тело настроек качества)."""
    if not site.qualities:
        return ("", "", "    // качества не заданы в декларации")
    ids = [q["id"] for q in site.qualities]
    default = site.default_quality or ids[0]
    body = f"""    private val defaultQuality get() = preferences.getString(
        "preferred_quality", "{default}"
    )"""
    return ("", "", body)


# ---------------------------------------------------------------------------
# Генерация: aniyomi
# ---------------------------------------------------------------------------


def render_aniyomi(site: SiteSpec) -> None:
    repo = REPOS["aniyomi"]
    module = repo / "src" / site.lang / site.site_id
    pkg_dir = module / "src" / "eu" / "kanade" / "tachiyomi" / "animeextension" / site.lang / site.site_id
    pkg_dir.mkdir(parents=True, exist_ok=True)

    cls = class_name(site)

    groovy = f"""ext {{
    extName = '{site.name}'
    extClass = '.{cls}'
    extVersionCode = 1
    isNsfw = {str(site.nsfw).lower()}
}}

apply plugin: "kei.plugins.extension.legacy"

dependencies {{
    implementation(project(':lib:playlistutils'))
}}
"""
    (module / "build.gradle").write_text(groovy, encoding="utf-8")

    main_kt = f"""package eu.kanade.tachiyomi.animeextension.{site.lang}.{site.site_id}

import eu.kanade.tachiyomi.animeextension.{site.lang}.{site.site_id}.dto.*
import eu.kanade.tachiyomi.animesource.ConfigurableAnimeSource
import eu.kanade.tachiyomi.animesource.model.*
import eu.kanade.tachiyomi.network.GET
import eu.kanade.tachiyomi.util.asJsoup
import keiyoushi.utils.getPreferencesLazy
import kotlinx.serialization.json.Json

/**
 * Сгенерировано extension-forge.
 * Источник: {site.base_url}
 * {site.notes}
 */
class {cls} : ConfigurableAnimeSource, AnimeHttpSource() {{

    private val preferences by getPreferencesLazy()

    // ==== Качество видео по умолчанию ====
    private val preferredQuality get() = preferences.getString("preferred_quality", "{site.default_quality or site.qualities[0]['id'] if site.qualities else '720'}")

    override fun popularAnimeRequest(page: Int): AnimeRequest =
        GET("$baseUrl/anime?page=$page", headers)

    override fun popularAnimeParse(response: AnimeResponse): AnimesPage {{
        TODO("Реализуйте парсинг по декларации sites/{site.site_id}.yaml")
    }}

    override fun searchAnimeRequest(page: Int, query: String, filters: AnimeFilterList): AnimeRequest =
        GET("$baseUrl/search?q=$query&page=$page", headers)

    override fun searchAnimeParse(response: AnimeResponse): AnimesPage =
        popularAnimeParse(response)

    override fun animeDetailsParse(response: AnimeResponse): SAnime {{
        TODO("Реализуйте по селекторам details из sites/{site.site_id}.yaml")
    }}

    override fun episodeListParse(response: AnimeResponse): List<SEpisode> {{
        TODO("Реализуйте по селекторам episodes из sites/{site.site_id}.yaml")
    }}

    override fun videoListParse(response: AnimeResponse): List<SVideo> {{
        TODO("Реализуйте; качества: {[q['id'] for q in site.qualities]}; выбор по умолчанию: $preferredQuality")
    }}

    companion object {{
        const val baseUrl = "{site.base_url}"
    }}
}}
"""
    (pkg_dir / f"{cls}.kt").write_text(main_kt, encoding="utf-8")

    generate_icons(site)


# ---------------------------------------------------------------------------
# Генерация: lnreader
# ---------------------------------------------------------------------------


def render_lnreader(site: SiteSpec) -> None:
    repo = REPOS["lnreader"]
    plugin_file = repo / "plugins" / "russian" / f"{site.site_id}.ts"

    cls = class_name(site)
    base = site.base_url

    plugin = f"""import {{ Plugin }} from '@/types/plugin';
import {{ FilterTypes, Filters }} from '@libs/filterInputs';
import {{ fetchApi }} from '@libs/fetch';
import {{ NovelStatus }} from '@libs/novelStatus';
import dayjs from 'dayjs';

/**
 * Сгенерировано extension-forge.
 * Источник: {base}
 * {site.notes}
 */
class {cls} implements Plugin.PluginBase {{
  id = '{site.site_id}';
  name = '{site.name}';
  icon = 'src/ru/{site.site_id}/icon.png';
  site = '{base}';
  version = '{site.version}';

  async popularNovels(
    pageNo: number,
    {{
      showLatestNovels,
      filters,
    }}: Plugin.PopularNovelsOptions<typeof this.filters>,
  ): Promise<Plugin.NovelItem[]> {{
    // {site.selectors.get('popular', 'TODO: заполните селекторы в sites/' + site.site_id + '.yaml')}
    const url = `${{this.site}}/api/novels?page=${{pageNo}}`;
    return fetchApi(url).then(res => res.json());
  }}

  async parseNovel(novelID: string): Promise<Plugin.SourceNovel> {{
    const url = `${{this.site}}/api/novels/${{novelID}}`;
    const data = await fetchApi(url).then(res => res.json());
    return {{
      path: novelID,
      name: data.name,
      cover: data.cover,
      summary: data.description,
      status: data.status === 'Ongoing' ? NovelStatus.Ongoing : NovelStatus.Completed,
    }};
  }}

  async parseChapter(chapterID: string): Promise<string> {{
    const url = `${{this.site}}/api/chapters/${{chapterID}}`;
    return fetchApi(url).then(res => res.text());
  }}

  async searchNovels(searchTerm: string, pageNo: number): Promise<Plugin.NovelItem[]> {{
    const url = `${{this.site}}/api/search?q=${{searchTerm}}&page=${{pageNo}}`;
    return fetchApi(url).then(res => res.json());
  }}

  filters = {{
    // заполните фильтры по документации API сайта
  }} as Filters<typeof this.filters>;
}}

export default new {cls}();
"""
    plugin_file.write_text(plugin, encoding="utf-8")


# ---------------------------------------------------------------------------
# Registry — чтобы сторож знал, что проверять
# ---------------------------------------------------------------------------


def load_registry() -> dict:
    if REGISTRY.exists():
        return json.loads(REGISTRY.read_text(encoding="utf-8"))
    return {"extensions": []}


def save_registry(reg: dict) -> None:
    REGISTRY.write_text(
        json.dumps(reg, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def register_extension(site: SiteSpec, version_code: str = "1") -> None:
    reg = load_registry()
    entry = {
        "id": site.site_id,
        "kind": site.kind,
        "name": site.name,
        "base_url": site.base_url,
        "version": site.version,
        "version_code": version_code,
        "nsfw": site.nsfw,
        "watch": True,       # сторож раз в месяц
        "selectors": site.selectors,
        "qualities": site.qualities,
        "default_quality": site.default_quality,
    }
    for ext in reg["extensions"]:
        if ext["id"] == site.site_id and ext["kind"] == site.kind:
            ext.update(entry)
            save_registry(reg)
            return
    reg["extensions"].append(entry)
    save_registry(reg)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def cmd_generate(args: list[str]) -> None:
    if not args:
        sys.exit("Использование: forge.py generate <sites/example.yaml>")
    spec_path = Path(args[0])
    if not spec_path.exists():
        sys.exit(f"Декларация не найдена: {spec_path}")
    site = SiteSpec.from_yaml(spec_path)
    print(f"→ Генерация {site.kind}/{site.site_id} ({site.name})")

    if site.kind == "mihon":
        render_mihon(site)
    elif site.kind == "aniyomi":
        render_aniyomi(site)
    elif site.kind == "lnreader":
        render_lnreader(site)
    else:
        sys.exit(f"Неизвестный kind: {site.kind}")

    register_extension(site)
    print(f"✔ Готово: {site.site_id}")
    print(f"  Дальше: заполните TODO по селекторам из {spec_path},")
    print(f"  затем git commit + push — CI соберёт APK и опубликует индекс.")


def cmd_bump(args: list[str]) -> None:
    if len(args) < 2:
        sys.exit("Использование: forge.py bump <mihon|aniyomi|lnreader> <site_id>")
    kind, site_id = args[0], args[1]
    reg = load_registry()
    entry = next((e for e in reg["extensions"] if e["id"] == site_id and e["kind"] == kind), None)
    if not entry:
        sys.exit(f"{kind}/{site_id} не найден в registry.json")

    site = SiteSpec(
        site_id=site_id, kind=kind, name=entry["name"],
        base_url=entry["base_url"], lang="ru", version=entry["version"],
    )
    new_v = bump_extension(site)
    entry["version_code" if kind != "lnreader" else "version"] = new_v
    save_registry(reg)
    print(f"✔ {kind}/{site_id}: версия → {new_v}")


def cmd_list(args: list[str]) -> None:
    reg = load_registry()
    for ext in reg["extensions"]:
        watch = "👁" if ext.get("watch") else "  "
        print(f"{watch} {ext['kind']:8} {ext['id']:20} v{ext['version']}  {ext['base_url']}")


def cmd_watch(args: list[str]) -> None:
    """Сторож: проверка живости источников. Подробности в watchdog/watchdog.py."""
    subprocess.run([sys.executable, str(FORGE_ROOT / "watchdog" / "watchdog.py")] + args)


COMMANDS = {
    "generate": cmd_generate,
    "bump": cmd_bump,
    "list": cmd_list,
    "watch": cmd_watch,
}


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        sys.exit(1)
    COMMANDS[sys.argv[1]](sys.argv[2:])
