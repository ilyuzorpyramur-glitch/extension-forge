#!/usr/bin/env python3
"""extension-forge — генератор расширений и плагинов для Tadami-экосистемы.

Целевые репозитории:
  - mihon-ru-manga-repo   (манга,   Kotlin,  gradle, тема multichan/...)
  - aniyomi-ru-anime-repo (аниме,   Kotlin,  gradle, legacy-плагин)
  - lnreader-ru-plugins   (ранобэ,  TypeScript, LNReader Plugin API)

Команды:
  forge add <url>              — разведка сайта и генерация по одной ссылке
  forge generate <site.yaml>   — сгенерировать расширение из декларации
  forge bump <target> <name>   — поднять версию расширения
  forge list                   — список известных сайтов
  forge watch                  — прогнать сторож по зарегистрированным расширениям
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import urllib.request
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
    # JSON API: эндпоинты, ключ, сортировки, статусы, возрастные рейтинги
    api: dict = field(default_factory=dict)
    # явный id источника для KSP (иначе считается стабильно из site_id)
    source_id: int = 0
    # versionCode модуля (если 0 — версия существующего модуля + 1)
    ext_version_code: int = 0
    # переопределения имён (если имя сайта не годится для класса/пакета)
    source_class: str = ""
    source_package: str = ""
    # ContentWarning: SAFE | MIXED | NSFW
    content_warning: str = ""
    # HTML-режим: селекторы и правила извлечения
    html: dict = field(default_factory=dict)
    # safe | mixed | nsfw — если пусто, считается из nsfw
    rating: str = ""

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
            api=data.get("api", {}),
            source_id=data.get("source_id", 0),
            ext_version_code=data.get("ext_version_code", 0),
            source_class=data.get("class_name", ""),
            source_package=data.get("package", ""),
            content_warning=data.get("content_warning", ""),
            html=data.get("html", {}),
            rating=str(data.get("rating", "") or "").lower(),
        )


# -----------------------------------------------------------------------------
# Генерация имён/пакетов
# -----------------------------------------------------------------------------


def package_name(site: SiteSpec) -> str:
    """ru.mymanagasite из 'My Manga Site'."""
    slug = re.sub(r"[^a-z0-9]", "", site.name.lower().replace(" ", ""))
    return f"{site.lang}.{slug}"


def class_name(site: SiteSpec) -> str:
    """MyMangaSite из 'My Manga Site'; MangaLove остаётся MangaLove."""
    parts = re.split(r"[^A-Za-z0-9]+", site.name)
    return "".join(part[:1].upper() + part[1:] for part in parts if part)


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


def format_kotlin(path: Path) -> None:
    """Best-effort ktlint-форматирование сгенерированного Kotlin (если есть кэш)."""
    import glob
    import shutil

    java = shutil.which("java")
    if not java:
        return
    jars = glob.glob(
        str(Path.home() / ".gradle/caches/modules-2/files-2.1/**/*.jar"), recursive=True
    )
    if not jars:
        return
    try:
        subprocess.run(
            [java, "-cp", ":".join(jars), "com.pinterest.ktlint.Main", "-F", str(path)],
            capture_output=True,
            timeout=300,
            check=False,
        )
    except Exception:
        pass


def render_mihon(site: SiteSpec) -> None:
    """Генерирует mihon-расширение: API-клиент или HTML-заготовку."""
    if site.api_style == "html" and site.html:
        render_mihon_html(site)
    elif site.api_style == "json_api_v2" and site.api:
        render_mihon_api_v2(site)
    elif site.api:
        render_mihon_api(site)
    else:
        _render_mihon_skeleton(site)


def _write_gradle(module: Path, site: SiteSpec, version_code: int, source_id: int) -> None:
    content_warning = site.content_warning or (
        site.rating.upper() if site.rating in ("safe", "mixed", "nsfw") else ("NSFW" if site.nsfw else "SAFE")
    )
    gradle = f"""import io.github.keiyoushi.gradle.api.ContentWarning

plugins {{
    alias(kei.plugins.extension)
}}

keiyoushi {{
    name = "{site.name}"
    versionCode = {version_code}
    contentWarning = ContentWarning.{content_warning}
    libVersion = "1.4"

    source {{
        baseUrl = "{site.base_url}"
        lang = "{site.lang}"
        id = {source_id}L
    }}
}}
"""
    (module / "build.gradle.kts").write_text(gradle, encoding="utf-8")


def render_mihon_html(site: SiteSpec) -> None:
    """HTML-генератор: Jsoup + селекторы из блока html декларации."""
    import mihon_html

    repo = REPOS["mihon"]
    module = repo / "src" / site.lang / site.site_id
    pkg_dir = module / "src" / "eu" / "kanade" / "tachiyomi" / "extension" / site.lang / site.site_id
    pkg_dir.mkdir(parents=True, exist_ok=True)

    cls = site.source_class or class_name(site)
    pkg = site.source_package or package_name(site)
    host = site.base_url.split("//", 1)[-1].split("/")[0]
    source_id = _stable_source_id(site)
    version_code = site.ext_version_code or _next_version_code(module)

    _write_gradle(module, site, version_code, source_id)
    code = mihon_html.render(site, pkg, cls, host)
    target = pkg_dir / f"{cls}.kt"
    target.write_text(code, encoding="utf-8")
    format_kotlin(target)
    generate_icons(site)


def render_mihon_api_v2(site: SiteSpec) -> None:
    """Универсальный API-генератор: всё поведение из блока api декларации."""
    import mihon_api_v2

    repo = REPOS["mihon"]
    module = repo / "src" / site.lang / site.site_id
    pkg_dir = module / "src" / "eu" / "kanade" / "tachiyomi" / "extension" / site.lang / site.site_id
    pkg_dir.mkdir(parents=True, exist_ok=True)

    cls = site.source_class or class_name(site)
    pkg = site.source_package or package_name(site)
    host = site.base_url.split("//", 1)[-1].split("/")[0]
    source_id = _stable_source_id(site)
    version_code = site.ext_version_code or _next_version_code(module)
    genres = fetch_genres(site)

    _write_gradle(module, site, version_code, source_id)
    code = mihon_api_v2.render(site, pkg, cls, host, version_code, source_id, genres)
    target = pkg_dir / f"{cls}.kt"
    target.write_text(code, encoding="utf-8")
    format_kotlin(target)
    generate_icons(site)


def _render_mihon_skeleton(site: SiteSpec) -> None:
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
        TODO("Реализуйте по селекторам pages из sites/{site.site_id}.yaml")
    }}

    companion object {{
        const val baseUrl = "{site.base_url}"
    }}
}}
"""
    (pkg_dir / f"{cls}.kt").write_text(main_kt, encoding="utf-8")

    # Иконки
    generate_icons(site)


# ---------------------------------------------------------------------------
# Генерация: mihon + JSON API (полностью рабочий клиент)
# ---------------------------------------------------------------------------

_KT_SERIALIZERS = '''package @@PKG@@

import kotlinx.serialization.KSerializer
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.descriptors.PrimitiveKind
import kotlinx.serialization.descriptors.PrimitiveSerialDescriptor
import kotlinx.serialization.descriptors.SerialDescriptor
import kotlinx.serialization.encoding.Decoder
import kotlinx.serialization.encoding.Encoder
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonDecoder
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonPrimitive

// Сайт отдаёт числа то строкой, то числом, а null — там, где поле объявлено
// обязательным. Эти сериализаторы терпят всю эту неоднородность.
// Сгенерировано extension-forge: правьте шаблон, а не этот файл.

object FlexibleString : KSerializer<String> {
    override val descriptor: SerialDescriptor =
        PrimitiveSerialDescriptor("FlexibleString", PrimitiveKind.STRING)

    override fun deserialize(decoder: Decoder): String {
        val element = (decoder as? JsonDecoder)?.decodeJsonElement() ?: return decoder.decodeString()
        return when (element) {
            is JsonNull -> ""
            is JsonPrimitive -> element.content
            else -> element.toString()
        }
    }

    override fun serialize(encoder: Encoder, value: String) = encoder.encodeString(value)
}

object FlexibleInt : KSerializer<Int> {
    override val descriptor: SerialDescriptor =
        PrimitiveSerialDescriptor("FlexibleInt", PrimitiveKind.INT)

    override fun deserialize(decoder: Decoder): Int {
        val element = (decoder as? JsonDecoder)?.decodeJsonElement() ?: return decoder.decodeInt()
        if (element is JsonNull) return 0
        return (element as? JsonPrimitive)?.content?.toDoubleOrNull()?.toInt() ?: 0
    }

    override fun serialize(encoder: Encoder, value: Int) = encoder.encodeInt(value)
}

object FlexibleDouble : KSerializer<Double> {
    override val descriptor: SerialDescriptor =
        PrimitiveSerialDescriptor("FlexibleDouble", PrimitiveKind.DOUBLE)

    override fun deserialize(decoder: Decoder): Double {
        val element = (decoder as? JsonDecoder)?.decodeJsonElement() ?: return decoder.decodeDouble()
        if (element is JsonNull) return 0.0
        return (element as? JsonPrimitive)?.content?.toDoubleOrNull() ?: 0.0
    }

    override fun serialize(encoder: Encoder, value: Double) = encoder.encodeDouble(value)
}

object FlexibleBool : KSerializer<Boolean> {
    override val descriptor: SerialDescriptor =
        PrimitiveSerialDescriptor("FlexibleBool", PrimitiveKind.BOOLEAN)

    override fun deserialize(decoder: Decoder): Boolean {
        val element = (decoder as? JsonDecoder)?.decodeJsonElement() ?: return decoder.decodeBoolean()
        if (element is JsonNull) return false
        val raw = (element as? JsonPrimitive)?.content ?: return false
        return raw == "true" || raw == "1"
    }

    override fun serialize(encoder: Encoder, value: Boolean) = encoder.encodeBoolean(value)
}

object FlexibleStringList : KSerializer<List<String>> {
    override val descriptor: SerialDescriptor = ListSerializer(FlexibleString).descriptor

    override fun deserialize(decoder: Decoder): List<String> {
        val element = (decoder as? JsonDecoder)?.decodeJsonElement() ?: return emptyList()
        if (element !is JsonArray) return emptyList()
        return element.mapNotNull { item ->
            when (item) {
                is JsonNull -> null
                is JsonPrimitive -> item.content
                else -> null
            }
        }
    }

    override fun serialize(encoder: Encoder, value: List<String>) {
        ListSerializer(FlexibleString).serialize(encoder, value)
    }
}

private class FlexibleList<T>(private val serializer: KSerializer<T>) : KSerializer<List<T>> {
    override val descriptor: SerialDescriptor = ListSerializer(serializer).descriptor

    override fun deserialize(decoder: Decoder): List<T> {
        val jsonDecoder = decoder as? JsonDecoder
            ?: return decoder.decodeSerializableValue(ListSerializer(serializer))
        val element = jsonDecoder.decodeJsonElement()
        if (element !is JsonArray) return emptyList()
        return element.map { jsonDecoder.json.decodeFromJsonElement(serializer, it) }
    }

    override fun serialize(encoder: Encoder, value: List<T>) {
        ListSerializer(serializer).serialize(encoder, value)
    }
}

@Serializable
data class MangaDto(
    @Serializable(with = FlexibleString::class) val id: String = "",
    @Serializable(with = FlexibleString::class) val title: String = "",
    @SerialName("title_ru")
    @Serializable(with = FlexibleString::class)
    val titleRu: String = "",
    @SerialName("description_ru")
    @Serializable(with = FlexibleString::class)
    val descriptionRu: String = "",
    @Serializable(with = FlexibleString::class) val description: String = "",
    @SerialName("cover_url")
    @Serializable(with = FlexibleString::class)
    val coverUrl: String = "",
    @Serializable(with = FlexibleStringList::class) val genres: List<String> = emptyList(),
    @Serializable(with = FlexibleStringList::class) val themes: List<String> = emptyList(),
    @Serializable(with = FlexibleString::class) val author: String = "",
    @Serializable(with = FlexibleString::class) val artist: String = "",
    @Serializable(with = FlexibleString::class) val status: String = "",
    @Serializable(with = FlexibleInt::class) val year: Int = 0,
    @SerialName("chapters_count")
    @Serializable(with = FlexibleInt::class)
    val chaptersCount: Int = 0,
    @SerialName("rating_avg")
    @Serializable(with = FlexibleDouble::class)
    val ratingAvg: Double = 0.0,
)

object MangaList : KSerializer<List<MangaDto>> by FlexibleList(MangaDto.serializer())

@Serializable
data class CatalogDto(
    @Serializable(with = MangaList::class) val manga: List<MangaDto> = emptyList(),
    @SerialName("has_more")
    @Serializable(with = FlexibleBool::class)
    val hasMore: Boolean = false,
    @Serializable(with = FlexibleInt::class) val total: Int = 0,
)

@Serializable
data class ChapterDto(
    @Serializable(with = FlexibleString::class) val id: String = "",
    @SerialName("chapter_num")
    @Serializable(with = FlexibleString::class)
    val chapterNum: String = "",
    @Serializable(with = FlexibleString::class) val volume: String = "",
    @Serializable(with = FlexibleString::class) val title: String = "",
    @SerialName("published_at")
    @Serializable(with = FlexibleString::class)
    val publishedAt: String = "",
    @SerialName("pages_count")
    @Serializable(with = FlexibleInt::class)
    val pagesCount: Int = 0,
    @SerialName("scanlation_group")
    @Serializable(with = FlexibleString::class)
    val scanlationGroup: String = "",
)

object ChapterList : KSerializer<List<ChapterDto>> by FlexibleList(ChapterDto.serializer())

@Serializable
data class ChaptersDto(
    @Serializable(with = ChapterList::class) val chapters: List<ChapterDto> = emptyList(),
    @Serializable(with = FlexibleInt::class) val total: Int = 0,
)

@Serializable
data class PagesDto(
    @Serializable(with = FlexibleStringList::class) val pages: List<String> = emptyList(),
)
'''

_KT_SOURCE = '''package @@PKG@@

import android.widget.Toast
import androidx.preference.ListPreference
import androidx.preference.PreferenceScreen
import eu.kanade.tachiyomi.network.GET
import eu.kanade.tachiyomi.source.ConfigurableSource
import eu.kanade.tachiyomi.source.model.Filter
import eu.kanade.tachiyomi.source.model.FilterList
import eu.kanade.tachiyomi.source.model.MangasPage
import eu.kanade.tachiyomi.source.model.Page
import eu.kanade.tachiyomi.source.model.SChapter
import eu.kanade.tachiyomi.source.model.SManga
import eu.kanade.tachiyomi.source.online.HttpSource
import keiyoushi.annotation.Source
import keiyoushi.network.rateLimit
import keiyoushi.utils.getPreferencesLazy
import keiyoushi.utils.parseAs
import kotlinx.serialization.json.Json
import okhttp3.Headers
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import java.time.OffsetDateTime
import kotlin.time.Duration.Companion.seconds

/**
 * @@NAME@@ (@@HOST@@) — сгенерировано extension-forge.
 *
 * @@NOTES@@
 */
@Source
class @@CLASS@@(
    override val name: String,
    override val baseUrl: String,
    override val lang: String,
    override val id: Long,
) : HttpSource(),
    ConfigurableSource {

    override val supportsLatest = true

    private val json = Json {
        ignoreUnknownKeys = true
        isLenient = true
    }

    private val preferences by getPreferencesLazy()

    private val baseUrlHost = baseUrl.toHttpUrl().host

    override val client: OkHttpClient = network.client.newBuilder()
        .connectTimeout(30.seconds)
        .readTimeout(60.seconds)
        .rateLimit(4, 1.seconds) { it.host == baseUrlHost }
        .addInterceptor { chain ->
            chain.proceed(
                chain.request().newBuilder()
                    .header(API_KEY_HEADER, API_KEY)
                    .build(),
            )
        }
        .build()

    override fun headersBuilder() = Headers.Builder().apply {
        add("Referer", "$baseUrl/")
        add("User-Agent", "Mozilla/5.0 (Linux; Android 14) @@CLASS@@/1.0")
    }

    // ================================ Качество ================================

    private val preferredQuality
        get() = preferences.getString(QUALITY_PREF, DEFAULT_QUALITY) ?: DEFAULT_QUALITY

    override fun setupPreferenceScreen(screen: PreferenceScreen) {
        ListPreference(screen.context).apply {
            key = QUALITY_PREF
            title = "Качество изображений"
            entries = QUALITY_LABELS
            entryValues = QUALITY_IDS
            summary = "%s"
            setDefaultValue(DEFAULT_QUALITY)
            setOnPreferenceChangeListener { _, _ ->
                Toast.makeText(
                    screen.context,
                    "Для применения очистите кэш глав (Настройки → Дополнительно)",
                    Toast.LENGTH_LONG,
                ).show()
                true
            }
        }.let(screen::addPreference)
    }

    // ============================== Каталог/поиск ==============================

    private fun catalogUrl(page: Int, sort: String, query: String? = null): HttpUrl =
        "$baseUrl$CATALOG_PATH".toHttpUrl().newBuilder()
            .addQueryParameter("lang", lang)
            .addQueryParameter("sort", sort)
            .addQueryParameter("page", page.toString())
            .addQueryParameter("per_page", PER_PAGE.toString())
            .apply {
                query?.takeIf { it.isNotBlank() }?.let { addQueryParameter("q", it) }
            }
            .build()

    override fun popularMangaRequest(page: Int): Request =
        GET(catalogUrl(page, DEFAULT_SORT), headers)

    override fun popularMangaParse(response: Response): MangasPage {
        val data = response.parseAs<CatalogDto>(json)
        return MangasPage(data.manga.map { it.toSManga() }, data.hasMore)
    }

    override fun latestUpdatesRequest(page: Int): Request =
        GET(catalogUrl(page, LATEST_SORT), headers)

    override fun latestUpdatesParse(response: Response): MangasPage = popularMangaParse(response)

    override fun searchMangaRequest(page: Int, query: String, filters: FilterList): Request {
        var sort = DEFAULT_SORT
        var status = ""
        var ageRating = ""
@@GENRE_VARS@@
        filters.forEach { filter ->
            when (filter) {
                is SortFilter -> sort = filter.selected
                is StatusFilter -> status = filter.selected
                is AgeRatingFilter -> ageRating = filter.selected
@@GENRE_BRANCH@@
                else -> {}
            }
        }

        val url = catalogUrl(page, sort, query).newBuilder()
        if (status.isNotEmpty()) url.addQueryParameter("status", status)
        if (ageRating.isNotEmpty()) url.addQueryParameter("age_rating", ageRating)
@@GENRE_PARAMS@@
        return GET(url.build(), headers)
    }

    override fun searchMangaParse(response: Response): MangasPage = popularMangaParse(response)

    // ================================ Детали ================================

    override fun mangaDetailsRequest(manga: SManga): Request =
        GET(baseUrl + DETAILS_PATH.replace("{id}", manga.url), headers)

    override fun mangaDetailsParse(response: Response): SManga =
        response.parseAs<MangaDto>(json).toSManga()

    // ================================= Главы =================================

    override fun chapterListRequest(manga: SManga): Request = chapterPageRequest(manga.url, 1)

    private fun chapterPageRequest(mangaId: String, page: Int): Request =
        GET(
            baseUrl + CHAPTERS_PATH.replace("{id}", mangaId) +
                "?lang=" + lang + "&page=" + page,
            headers,
        )

    override fun chapterListParse(response: Response): List<SChapter> {
        val segments = response.request.url.pathSegments
        val mangaId = segments.getOrNull(segments.indexOf("manga") + 1) ?: return emptyList()

        var data = response.parseAs<ChaptersDto>(json)
        val chapters = data.chapters.toMutableList()
        var page = 1

        while (chapters.isNotEmpty() && chapters.size < data.total && page < MAX_CHAPTER_PAGES) {
            page++
            data = client.newCall(chapterPageRequest(mangaId, page)).execute().parseAs(json)
            if (data.chapters.isEmpty()) break
            chapters += data.chapters
        }

        return chapters.distinctBy { it.id }.map { it.toSChapter(mangaId) }
    }

    private fun ChapterDto.toSChapter(mangaId: String): SChapter = SChapter.create().apply {
        url = PAGES_PATH
            .replace("{id}", mangaId)
            .replace("{chapter}", id)
        name = buildString {
            if (volume.isNotEmpty() && volume != "0") append("Том $volume ")
            append("Глава $chapterNum")
            if (title.isNotEmpty()) append(" — $title")
        }
        chapter_number = chapterNum.toFloatOrNull() ?: -1f
        scanlator = scanlationGroup.takeIf { it.isNotEmpty() }
        date_upload = publishedAt.toEpochMillis()
    }

    private fun String.toEpochMillis(): Long = runCatching {
        OffsetDateTime.parse(this).toInstant().toEpochMilli()
    }.getOrDefault(0L)

    // ================================ Страницы ================================

    override fun pageListRequest(chapter: SChapter): Request =
        GET("$baseUrl${chapter.url}?quality=$preferredQuality", headers)

    override fun pageListParse(response: Response): List<Page> =
        response.parseAs<PagesDto>(json).pages.mapIndexed { index, url ->
            Page(index, imageUrl = url)
        }

    override fun imageUrlParse(response: Response): String = throw UnsupportedOperationException()

    override fun imageRequest(page: Page): Request =
        GET(page.imageUrl!!, headers)

    // ================================ Маппинг ================================

    private fun MangaDto.toSManga(): SManga = SManga.create().apply {
        url = id
        title = titleRu.ifBlank { title }
        thumbnail_url = coverUrl.takeIf { it.isNotEmpty() }?.let {
            if (it.startsWith("http")) it else baseUrl + it
        }
        author = this@toSManga.author.takeIf { it.isNotEmpty() }
        artist = this@toSManga.artist.takeIf { it.isNotEmpty() }
        description = this@toSManga.descriptionRu.ifBlank { this@toSManga.description }
        genre = (genres + themes).distinct().joinToString(", ").takeIf { it.isNotEmpty() }
        status = this@toSManga.status.toSMangaStatus()
        initialized = false
    }

    private fun String.toSMangaStatus(): Int = when (this) {
        "ongoing" -> SManga.ONGOING
        "completed" -> SManga.COMPLETED
        "hiatus" -> SManga.ON_HIATUS
        "dropped", "cancelled" -> SManga.CANCELLED
        else -> SManga.UNKNOWN
    }

    // ================================ Фильтры ================================

@@FILTERS@@
    override fun getFilterList() = FilterList(
        SortFilter(),
        StatusFilter(),
        AgeRatingFilter(),
@@GENRE_ENTRY@@
    )

    companion object {
        private const val API_KEY_HEADER = "@@API_KEY_HEADER@@"
        private const val API_KEY = "@@API_KEY@@"
        private const val QUALITY_PREF = "preferred_quality"
        private const val DEFAULT_QUALITY = "@@DEFAULT_QUALITY@@"
        private const val CATALOG_PATH = "@@CATALOG_PATH@@"
        private const val DETAILS_PATH = "@@DETAILS_PATH@@"
        private const val CHAPTERS_PATH = "@@CHAPTERS_PATH@@"
        private const val PAGES_PATH = "@@PAGES_PATH@@"
        private const val PER_PAGE = @@PER_PAGE@@
        private const val MAX_CHAPTER_PAGES = 20
        private const val DEFAULT_SORT = "@@DEFAULT_SORT@@"
        private const val LATEST_SORT = "@@LATEST_SORT@@"
        private val QUALITY_IDS = arrayOf(@@QUALITY_IDS@@)
        private val QUALITY_LABELS = arrayOf(@@QUALITY_LABELS@@)
    }
}
'''


def _kotlin_array(values: list[str]) -> str:
    return ", ".join(f'"{v}"' for v in values)


def _render_filters(api: dict) -> str:
    sorts = api.get("sorts") or [{"id": "popular", "label": "По популярности"}]
    statuses = api.get("statuses") or []
    age_ratings = api.get("age_ratings") or []

    blocks = [
        "    private class SortFilter :\n"
        '        Filter.Select<String>(\n'
        '            "Сортировка",\n'
        f"            arrayOf({_kotlin_array([s['label'] for s in sorts])}),\n"
        "        ) {\n"
        f"        val selected get() = arrayOf({_kotlin_array([s['id'] for s in sorts])})[state]\n"
        "    }\n"
    ]
    if statuses:
        blocks.append(
            "    private class StatusFilter :\n"
            '        Filter.Select<String>(\n'
            '            "Статус",\n'
            f"            arrayOf({_kotlin_array([s['label'] for s in statuses])}),\n"
            "        ) {\n"
            f"        val selected get() = arrayOf({_kotlin_array([s['id'] for s in statuses])})[state]\n"
            "    }\n"
        )
    if age_ratings:
        blocks.append(
            "    private class AgeRatingFilter :\n"
            '        Filter.Select<String>(\n'
            '            "Возрастной рейтинг",\n'
            f"            arrayOf({_kotlin_array([s['label'] for s in age_ratings])}),\n"
            "        ) {\n"
            f"        val selected get() = arrayOf({_kotlin_array([s['id'] for s in age_ratings])})[state]\n"
            "    }\n"
        )
    return "\n".join(blocks)


def fetch_genres(site: SiteSpec) -> list[dict]:
    """Жанры с UUID: MangaDex-теги, закэшированные в sites/<id>.genres.json."""
    cfg = site.api.get("genres") or {}
    if not cfg:
        return []
    cache = SITES_DIR / f"{site.site_id}.genres.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))

    source = cfg.get("source", "mangadex")
    if source == "catalog":
        headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
        headers.update(cfg.get("headers") or {})
        values = {}
        page = 1
        while page <= int(cfg.get("max_pages", 40)):
            url = (
                cfg["base"].rstrip("/")
                + cfg.get("path", "/titles")
                + f"?page={page}&limit=50"
                + (f"&{cfg['extra']}" if cfg.get("extra") else "")
            )
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.load(response)
            items = payload
            for segment in (cfg.get("items") or "").split("."):
                if segment:
                    items = items[segment]
            if not items:
                break
            for item in items:
                for value in item.get(cfg.get("field", "genres")) or []:
                    if isinstance(value, str) and value.strip():
                        values[value.strip()] = True
            page += 1
        exact = {name.lower(): name for name in values}
        only = cfg.get("list") or []
        if only:
            wanted = [
                (item["name"] if isinstance(item, dict) else item) for item in only
            ]
            names = [exact[name.lower()] for name in wanted if name.lower() in exact]
        else:
            names = list(values)
        result = [{"id": name, "name": name} for name in sorted(set(names), key=str.lower)]
        cache.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return result
    if source == "api":
        headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
        headers.update(cfg.get("headers") or {})
        request = urllib.request.Request(
            cfg["base"].rstrip("/") + cfg.get("path", "/genres"),
            headers=headers,
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.load(response)
        items = data
        for segment in (cfg.get("items") or "").split("."):
            if segment:
                items = items[segment]
        result = [
            {"id": str(item[cfg.get("id", "id")]), "name": str(item.get(cfg.get("name", "name")) or "")}
            for item in items
        ]
        cache.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return result
    if source != "mangadex":
        return cfg.get("list") or []

    url = "https://api.mangadex.org/manga/tag?limit=100"
    request = urllib.request.Request(url, headers={"User-Agent": "extension-forge/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        data = json.load(response)

    groups = set(cfg.get("groups") or ["genre"])
    only = set(cfg.get("only") or [])
    exclude = set(cfg.get("exclude") or [])
    items = []
    for tag in data.get("data", []):
        attributes = tag.get("attributes") or {}
        name = (attributes.get("name") or {}).get("en")
        if not name or attributes.get("group") not in groups:
            continue
        if only and name not in only:
            continue
        if name in exclude:
            continue
        items.append({"id": tag["id"], "name": name})

    items.sort(key=lambda item: item["name"].lower())
    cache.write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return items


def _render_genre_filter(genres: list[dict]) -> str:
    if not genres:
        return ""
    lines = [
        "    private class GenreFilter :",
        "        Filter.Group<Genre>(",
        '            "Жанры",',
        "            listOf(",
    ]
    lines.extend(
        f'                Genre("{g["name"].replace(chr(34), chr(92) + chr(34))}", "{g["id"]}"),'
        for g in genres
    )
    lines.extend(
        [
            "            ),",
            "        )",
            "",
            "    private class Genre(name: String, val id: String) : Filter.TriState(name)",
        ]
    )
    return "\n".join(lines) + "\n"


def _genre_placeholders(genres: list[dict]) -> dict:
    if not genres:
        return {
            "@@GENRE_VARS@@": "",
            "@@GENRE_BRANCH@@": "",
            "@@GENRE_PARAMS@@": "",
            "@@GENRE_ENTRY@@": "",
        }
    vars_ = '        var includedTags = ""\n        var excludedTags = ""'
    branch = (
        "                is GenreFilter -> {\n"
        "                    includedTags = filter.state\n"
        "                        .filter { it.state == Filter.TriState.STATE_INCLUDE }\n"
        '                        .joinToString(",") { it.id }\n'
        "                    excludedTags = filter.state\n"
        "                        .filter { it.state == Filter.TriState.STATE_EXCLUDE }\n"
        '                        .joinToString(",") { it.id }\n'
        "                }"
    )
    params = (
        '        if (includedTags.isNotEmpty()) url.addQueryParameter("included_tags", includedTags)\n'
        '        if (excludedTags.isNotEmpty()) url.addQueryParameter("excluded_tags", excludedTags)'
    )
    return {
        "@@GENRE_VARS@@": vars_,
        "@@GENRE_BRANCH@@": branch,
        "@@GENRE_PARAMS@@": params,
        "@@GENRE_ENTRY@@": "        GenreFilter(),",
    }


def render_mihon_api(site: SiteSpec) -> None:
    """Полноценный клиент JSON API: каталог, поиск, детали, главы, страницы, качество."""
    repo = REPOS["mihon"]
    module = repo / "src" / site.lang / site.site_id
    pkg_dir = module / "src" / "eu" / "kanade" / "tachiyomi" / "extension" / site.lang / site.site_id
    pkg_dir.mkdir(parents=True, exist_ok=True)

    api = site.api
    cls = class_name(site)
    pkg = package_name(site)
    host = site.base_url.split("//", 1)[-1].split("/")[0]

    source_id = _stable_source_id(site)
    version_code = site.ext_version_code or _next_version_code(module)
    content_warning = "NSFW" if site.nsfw else "SAFE"
    qualities = site.qualities or [{"id": "default", "label": "По умолчанию"}]
    default_quality = site.default_quality or qualities[0]["id"]
    sorts = api.get("sorts") or [{"id": "popular", "label": "По популярности"}]
    default_sort = api.get("default_sort", sorts[0]["id"])
    latest_sort = api.get("latest_sort", next((s["id"] for s in sorts if "latest" in s["id"].lower()), default_sort))

    gradle = f"""import io.github.keiyoushi.gradle.api.ContentWarning

plugins {{
    alias(kei.plugins.extension)
}}

keiyoushi {{
    name = "{site.name}"
    versionCode = {version_code}
    contentWarning = ContentWarning.{content_warning}
    libVersion = "1.4"

    source {{
        baseUrl = "{site.base_url}"
        lang = "{site.lang}"
        id = {source_id}L
    }}
}}
"""
    (module / "build.gradle.kts").write_text(gradle, encoding="utf-8")

    replacements = {
        "@@PKG@@": pkg,
        "@@CLASS@@": cls,
        "@@NAME@@": site.name,
        "@@HOST@@": host,
        "@@NOTES@@": site.notes.replace("*/", "* /"),
    }
    dto_kt = _KT_SERIALIZERS
    for key, value in replacements.items():
        dto_kt = dto_kt.replace(key, value)
    (pkg_dir / "Dto.kt").write_text(dto_kt, encoding="utf-8")

    source_kt = _KT_SOURCE
    genres = fetch_genres(site)
    source_replacements = dict(replacements)
    source_replacements.update(
        {
            "@@FILTERS@@": _render_filters(api) + _render_genre_filter(genres),
            "@@API_KEY_HEADER@@": api.get("key_header", "X-API-Key"),
            "@@API_KEY@@": api.get("api_key", ""),
            "@@DEFAULT_QUALITY@@": default_quality,
            "@@CATALOG_PATH@@": api.get("catalog", "/api/v1/manga/catalog"),
            "@@DETAILS_PATH@@": api.get("details", "/api/v1/manga/{id}"),
            "@@CHAPTERS_PATH@@": api.get("chapters", "/api/v1/manga/{id}/chapters"),
            "@@PAGES_PATH@@": api.get("pages", "/api/v1/manga/{id}/chapters/{chapter}/pages"),
            "@@PER_PAGE@@": str(api.get("per_page", 50)),
            "@@DEFAULT_SORT@@": default_sort,
            "@@LATEST_SORT@@": latest_sort,
            "@@QUALITY_IDS@@": _kotlin_array([q["id"] for q in qualities]),
            "@@QUALITY_LABELS@@": _kotlin_array([q["label"] for q in qualities]),
        }
    )
    source_replacements.update(_genre_placeholders(genres))
    for key, value in source_replacements.items():
        source_kt = source_kt.replace(key, value)
    (pkg_dir / f"{cls}.kt").write_text(source_kt, encoding="utf-8")

    generate_icons(site)


def _stable_source_id(site: SiteSpec) -> int:
    """id источника: явный из декларации или KSP-совместимый (MD5 name/lang/1)."""
    if site.source_id:
        return int(site.source_id)
    key = f"{site.name.lower()}/{site.lang}/1"
    digest = hashlib.md5(key.encode("utf-8")).digest()
    value = 0
    for byte in digest[:8]:
        value = (value << 8) | byte
    return value & 0x7FFFFFFFFFFFFFFF


def _next_version_code(module: Path) -> int:
    """versionCode существующего модуля + 1 (иначе 1)."""
    gradle = module / "build.gradle.kts"
    if not gradle.exists():
        return 1
    match = re.search(r"versionCode\s*=\s*(\d+)", gradle.read_text(encoding="utf-8"))
    return int(match.group(1)) + 1 if match else 1


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


def cmd_add(args: list[str]) -> None:
    """Разведка сайта по одной ссылке: forge add https://site.ru"""
    if not args:
        sys.exit("Использование: forge.py add https://site.ru [--force] [--dry-run]")
    import recon

    url = args[0]
    flags = set(args[1:])
    if not url.startswith("http"):
        url = "https://" + url
    result = recon.profile(url)
    if result.get("error"):
        sys.exit(f"✖ {result['error']}")

    host = result["host"]
    parts = host.split(".")
    site_id = re.sub(r"[^a-z0-9]", "", parts[-2] if len(parts) >= 2 else parts[0])
    spec_path = SITES_DIR / f"{site_id}.yaml"
    yaml_text = recon.build_yaml(result, site_id)

    print(f"→ Разведка {url}")
    print(f"  сайт: {result['name']}, API: {result['base'] or '—'}")
    print(f"  список: {result['list_path'] or '—'} (items: {result['items'] or '—'})")
    print(f"  поля: {', '.join(result['fields']) or '—'}")
    if result.get("pagination"):
        print(f"  пагинация: {result['pagination']}")
    if result.get("gaps"):
        print("  не найдено: " + ", ".join(result["gaps"]))

    if "--dry-run" in flags:
        print(yaml_text)
        return
    if spec_path.exists() and "--force" not in flags:
        print(f"  декларация уже есть: {spec_path} (--force, чтобы перезаписать)")
        return
    spec_path.write_text(yaml_text, encoding="utf-8")
    print(f"✔ Декларация: {spec_path}")
    cmd_generate([str(spec_path)])


def cmd_generate(args: list[str]) -> None:
    if not args:
        sys.exit("Использование: forge.py generate <sites/example.yaml>")
    spec_path = Path(args[0])
    if not spec_path.exists():
        if str(args[0]).startswith("http"):
            cmd_add([str(args[0])])
            return
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
    "add": cmd_add,
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
