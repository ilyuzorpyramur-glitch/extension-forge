"""Генератор mihon-расширений из декларации JSON API (json_api_v2).

Один шаблон на все сайты: эндпоинты, параметры, JSON-пути и фильтры берутся
из декларации sites/<id>.yaml. Никакого ручного Kotlin на сайт.
"""

from __future__ import annotations

from textwrap import indent

HEADER = '''package {pkg}

import android.widget.Toast
import androidx.preference.ListPreference
import androidx.preference.PreferenceScreen
import androidx.preference.SwitchPreferenceCompat
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
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import okhttp3.Headers
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import rx.Observable
import java.time.OffsetDateTime
import kotlin.time.Duration.Companion.seconds

/**
 * {name} ({host}) — сгенерировано extension-forge.
{notes} */
@Source
class {cls}(
    override val name: String,
    override val baseUrl: String,
    override val lang: String,
    override val id: Long,
) : HttpSource(),
    ConfigurableSource {{

    override val supportsLatest = true

    private val json = Json {{
        ignoreUnknownKeys = true
        isLenient = true
    }}

    private val preferences by getPreferencesLazy()

    override val client: OkHttpClient = network.client.newBuilder()
        .connectTimeout(30.seconds)
        .readTimeout(60.seconds)
        .rateLimit({rate}, 1.seconds)
        .build()

    override fun headersBuilder() = Headers.Builder().apply {{
        add("Referer", "$baseUrl/")
        add("User-Agent", "Mozilla/5.0 (Linux; Android 14) {cls}/1.0")
        add("Accept", "application/json")
{header_lines}
    }}

    // ================================ JSON ==================================

    private fun getJson(url: HttpUrl): JsonElement =
        client.newCall(GET(url, headers)).execute().parseAs<JsonElement>(json)

    private fun getJson(url: String): JsonElement = getJson(url.toHttpUrl())

    private fun JsonElement.at(path: String): JsonElement? {{
        if (path.isEmpty()) return this
        var current: JsonElement = this
        for (segment in path.split('.')) {{
            val obj = current as? JsonObject ?: return null
            current = obj[segment] ?: return null
        }}
        return current
    }}

    private fun JsonElement.text(path: String): String {{
        val element = at(path) ?: return ""
        return when (element) {{
            is JsonNull -> ""
            is JsonPrimitive -> element.content
            is JsonArray -> element.joinToString(", ") {{ it.asText() }}
            else -> ""
        }}
    }}

    private fun JsonElement.number(path: String): Int = text(path).toDoubleOrNull()?.toInt() ?: 0

    private fun JsonElement.decimal(path: String): Double = text(path).toDoubleOrNull() ?: 0.0

    private fun JsonElement.flag(path: String): Boolean = text(path) == "true" || text(path) == "1"

    private fun JsonElement.items(path: String): List<JsonElement> =
        (at(path) as? JsonArray)?.toList() ?: emptyList()

    private fun JsonElement.asText(): String = when (this) {{
        is JsonNull -> ""
        is JsonPrimitive -> content
        else -> ""
    }}

    private fun String.absoluteUrl(useProxy: Boolean = true): String {{
        if (isEmpty()) return ""
        var url = if (startsWith("http")) this else MEDIA_BASE + (if (startsWith("/")) this else "/" + this)
        if (MEDIA_STRIP.isNotEmpty() && url.startsWith(MEDIA_BASE + MEDIA_STRIP)) {{
            url = MEDIA_BASE + url.removePrefix(MEDIA_BASE + MEDIA_STRIP)
        }}
        if (useProxy && MEDIA_PROXY.isNotEmpty()) {{
            url = MEDIA_PROXY.replace("{{url}}", java.net.URLEncoder.encode(url, "UTF-8"))
        }}
        return url
    }}
'''


TAIL = '''    companion object {{
{consts}
    }}
}}
'''


def render(site, pkg: str, cls: str, host: str, version_code: int, source_id: int, genres: list[dict]) -> str:
    api = site.api
    lines: list[str] = []

    head = HEADER.format(
        pkg=pkg,
        cls=cls,
        name=site.name,
        host=host,
        notes=(site.notes or "").replace("*/", "* /"),
        rate=api.get("rate", 4),
        header_lines="\n".join(
            f'        add("{k}", "{v}")' for k, v in (api.get("headers") or {}).items()
        ),
    )
    if not site.qualities:
        head = head.replace("import android.widget.Toast\n", "")
        head = head.replace("import androidx.preference.ListPreference\n", "")
    if not (api.get("options") or {}):
        head = head.replace("import androidx.preference.SwitchPreferenceCompat\n", "")
    if not ((api.get("chapters") or {}).get("steps") or []):
        head = head.replace("import rx.Observable\n", "")
    if not (site.qualities or (api.get("options") or {})):
        head = head.replace(") : HttpSource(),\n    ConfigurableSource {", ") : HttpSource() {")
        head = head.replace("import eu.kanade.tachiyomi.source.ConfigurableSource\n", "")
        head = head.replace("import androidx.preference.PreferenceScreen\n", "")
        head = head.replace("import keiyoushi.utils.getPreferencesLazy\n", "")
        head = head.replace("    private val preferences by getPreferencesLazy()\n\n", "")
    lines.append(head)

    lines.append(_prefs(site, api))
    lines.append(_list_section(api))
    lines.append(_details_section(api))
    lines.append(_chapters_section(api))
    lines.append(_pages_section(api))
    lines.append(_mapping_section(api))
    lines.append(_filters_section(site, api, genres))

    consts = _consts(site, api)
    lines.append(TAIL.format(consts=consts))
    return "".join(lines)


# ---------------------------------------------------------------------------
# Настройки
# ---------------------------------------------------------------------------


def _prefs(site, api) -> str:
    options = api.get("options") or {}
    qualities = site.qualities or []
    if not options and not qualities:
        return ""

    prefs = ["    // ============================== Настройки ==============================\n"]
    entries = []

    if qualities:
        ids = ", ".join(f'"{q["id"]}"' for q in qualities)
        labels = ", ".join(f'"{q["label"]}"' for q in qualities)
        default = site.default_quality or qualities[0]["id"]
        prefs.append(
            f'''    private val preferredQuality get() = preferences.getString(QUALITY_PREF, "{default}") ?: "{default}"

'''
        )
        entries.append(
            f'''        ListPreference(screen.context).apply {{
            key = QUALITY_PREF
            title = "Качество изображений"
            entries = arrayOf({labels})
            entryValues = arrayOf({ids})
            summary = "%s"
            setDefaultValue("{default}")
            setOnPreferenceChangeListener {{ _, _ ->
                Toast.makeText(
                    screen.context,
                    "Для применения очистите кэш глав (Настройки → Дополнительно)",
                    Toast.LENGTH_LONG,
                ).show()
                true
            }}
        }}.let(screen::addPreference)

'''
        )

    for key, opt in options.items():
        if key == "show_adult":
            prefs.append(
                f'''    private val showAdult get() = preferences.getBoolean("{opt["pref"]}", {str(opt.get("default", False)).lower()})

'''
            )
            entries.append(
                f'''        SwitchPreferenceCompat(screen.context).apply {{
            key = "{opt["pref"]}"
            title = "{opt.get("title", "Показывать 18+")}"
            setDefaultValue({str(opt.get("default", False)).lower()})
        }}.let(screen::addPreference)

'''
            )
        elif key == "hide_paid":
            prefs.append(
                f'''    private val hidePaid get() = preferences.getBoolean("{opt["pref"]}", {str(opt.get("default", True)).lower()})

'''
            )
            entries.append(
                f'''        SwitchPreferenceCompat(screen.context).apply {{
            key = "{opt["pref"]}"
            title = "{opt.get("title", "Скрывать платные главы")}"
            setDefaultValue({str(opt.get("default", True)).lower()})
        }}.let(screen::addPreference)

'''
            )

    prefs.append(
        "    override fun setupPreferenceScreen(screen: PreferenceScreen) {\n"
        + "".join(entries)
        + "    }\n\n"
    )
    return "".join(prefs)


# ---------------------------------------------------------------------------
# Список: популярное / последние / поиск
# ---------------------------------------------------------------------------


def _list_section(api) -> str:
    cfg = api.get("list") or {}
    path = cfg.get("path", "/")
    page_param = cfg.get("page_param", "page")
    start = cfg.get("page_start", 1)
    size_param = cfg.get("size_param", "")
    size = cfg.get("size", 20)
    query_param = cfg.get("query_param", "q")
    sort_param = cfg.get("sort_param", "sort")
    sorts = cfg.get("sorts") or []
    default_sort = cfg.get("default_sort") or (sorts[0]["value"] if sorts else "")
    latest_sort = cfg.get("latest_sort") or default_sort

    size_line = ""
    if size_param:
        size_line = f'            .addQueryParameter("{size_param}", SIZE.toString())\n'

    page_expr = f"page + {start - 1}" if start != 1 else "page"

    extra_lines = "".join(
        f'            .addQueryParameter("{k}", "{v}")\n'
        for k, v in (cfg.get("extra_params") or {}).items()
    )

    def url_fn(name: str, sort_expr: str) -> str:
        sort_line = ""
        if sorts and sort_param:
            sort_line = f'            .addQueryParameter("{sort_param}", {sort_expr})\n'
        return (
            f"    private fun {name}(page: Int): HttpUrl =\n"
            f'        (API_BASE + LIST_PATH).toHttpUrl().newBuilder()\n'
            f'            .addQueryParameter("{page_param}", {page_expr}.toString())\n'
            f"{size_line}{extra_lines}{sort_line}"
            "            .build()\n\n"
        )

    extra_search = "".join(
        f'            .addQueryParameter("{k}", "{v}")\n'
        for k, v in (cfg.get("extra_params") or {}).items()
    )
    out = ["    // ============================== Каталог ==============================\n\n"]
    out.append(url_fn("catalogUrl", "DEFAULT_SORT"))
    out.append(url_fn("latestUrl", "LATEST_SORT"))

    out.append(
        f'''    override fun popularMangaRequest(page: Int): Request = GET(catalogUrl(page), headers)

    override fun popularMangaParse(response: Response): MangasPage = parseList(response)

    override fun latestUpdatesRequest(page: Int): Request = GET(latestUrl(page), headers)

    override fun latestUpdatesParse(response: Response): MangasPage = parseList(response)

    override fun searchMangaRequest(page: Int, query: String, filters: FilterList): Request {{
        var sort = DEFAULT_SORT
        val builder = (API_BASE + LIST_PATH).toHttpUrl().newBuilder()
            .addQueryParameter("{page_param}", {page_expr}.toString())
{size_line}{extra_search}
        if (query.isNotBlank()) builder.addQueryParameter("{query_param}", query.trim())

        filters.forEach {{ filter ->
            when (filter) {{
'''
    )

    for entry in api.get("filters") or []:
        out.append(
            f'''                is {entry["class"]} -> filter.selected.takeIf {{ it.isNotEmpty() }}
                    ?.let {{ builder.addQueryParameter("{entry["param"]}", it) }}
'''
        )
    sorts_line = ""
    if len(sorts) > 1:
        sorts_line = f'''                is SortFilter -> sort = filter.selected
'''
    out.append(sorts_line)
    if genres_config(api):
        transform = (genres_config(api).get("transform") or "").lower()
        if transform == "lower":
            genre_expr = "it.id.lowercase()"
        elif transform == "trim":
            genre_expr = "it.id.trim()"
        else:
            genre_expr = "it.id"
        out.append(
            f'''                is GenreFilter -> filter.state.filter {{ it.state }}
                    .forEach {{ builder.addQueryParameter(GENRE_PARAM, {genre_expr}) }}
'''
        )
    out.append(
        f'''                else -> {{}}
            }}
        }}
'''
    )
    if len(sorts) > 1:
        out.append(
            f'''        if (sort.isNotEmpty()) builder.addQueryParameter("{sort_param}", sort)
'''
        )
    out.append(
        """        return GET(builder.build(), headers)
    }

    override fun searchMangaParse(response: Response): MangasPage = parseList(response)

"""
    )

    items = cfg.get("items", "data")
    total_pages = cfg.get("total_pages", "")
    current_page = cfg.get("current_page", "")
    has_more = cfg.get("has_more", "")

    if has_more:
        more_expr = f"root.flag({_kotlin_str(has_more)})"
    elif total_pages:
        more_expr = (
            f"root.number({_kotlin_str(current_page or total_pages)}) < "
            f"root.number({_kotlin_str(total_pages)})"
        )
    else:
        more_expr = "items.size >= SIZE"

    guard = ""
    if (api.get("options") or {}).get("show_adult"):
        opt = api["options"]["show_adult"]
        guard = f"            if (!showAdult && item.flag({_kotlin_str(opt.get('field', 'isAdult'))})) return@mapNotNull null\n"

    out.append(
        f'''    private fun parseList(response: Response): MangasPage {{
        val root = response.parseAs<JsonElement>(json)
        val items = root.items({_kotlin_str(items)})
        val mangas = items.mapNotNull {{ item ->
{guard}            item.toSManga()
        }}
        return MangasPage(mangas, {more_expr})
    }}

'''
    )
    return "".join(out)


# ---------------------------------------------------------------------------
# Детали
# ---------------------------------------------------------------------------


def _details_section(api) -> str:
    cfg = api.get("details") or {}
    path = cfg.get("path", "")
    if not path:
        return ""
    root = cfg.get("root", "")
    root_line = f"        val item = root.at({_kotlin_str(root)}) ?: root\n" if root else "        val item = root\n"
    return f'''    // =============================== Детали ===============================

    override fun mangaDetailsRequest(manga: SManga): Request =
        GET(API_BASE + DETAILS_PATH.replace("{{id}}", manga.url), headers)

    override fun mangaDetailsParse(response: Response): SManga {{
        val root = response.parseAs<JsonElement>(json)
{root_line}        return item.toSManga()
    }}

'''


# ---------------------------------------------------------------------------
# Главы
# ---------------------------------------------------------------------------


def _chapters_section(api) -> str:
    cfg = api.get("chapters") or {}
    steps = cfg.get("steps") or []
    if not steps:
        return ""

    body: list[str] = ["    override fun fetchChapterList(manga: SManga): Observable<List<SChapter>> = Observable.fromCallable {\n"]
    vars_: dict[str, str] = {}

    def camel(name: str) -> str:
        parts = name.split("_")
        return parts[0] + "".join(part[:1].upper() + part[1:] for part in parts[1:])

    for idx, step in enumerate(steps[:-1], start=1):
        select = step.get("select") or {}
        pre = f"step{idx}"
        path_expr = f"API_BASE + STEP{idx}_PATH"
        for var, kotlin_name in vars_.items():
            path_expr += f'.replace("{{{var}}}", {kotlin_name})'
        path_expr += '.replace("{id}", manga.url)'
        body.append(f"        val {pre} = getJson({path_expr})\n")
        if step.get("items"):
            body.append(f"        val {pre}Items = {pre}.items({_kotlin_str(step['items'])})\n")
        if select:
            main = select.get("main_field")
            mx = select.get("max_field", "count")
            main_expr = (
                f"{pre}Items.firstOrNull {{ it.flag({_kotlin_str(main)}) }}"
                if main
                else f"{pre}Items.firstOrNull()"
            )
            pick = f"{main_expr} ?: {pre}Items.maxByOrNull {{ it.number({_kotlin_str(mx)}) }}"
            body.append(f"        val picked{idx} = {pick}\n")
            for var_name, field in (select.get("save") or {}).items():
                kotlin_name = camel(var_name)
                body.append(f"        val {kotlin_name} = picked{idx}?.text({_kotlin_str(field)}) ?: \"\"\n")
                vars_[var_name] = kotlin_name
            for var_name, field in (select.get("save_number") or {}).items():
                kotlin_name = camel(var_name)
                body.append(f"        val {kotlin_name} = picked{idx}?.text({_kotlin_str(field)}) ?: \"\"\n")
                vars_[var_name] = kotlin_name
        for var_name, path in (step.get("save") or {}).items():
            kotlin_name = camel(var_name)
            body.append(f"        val {kotlin_name} = {pre}.text({_kotlin_str(path)})\n")
            vars_[var_name] = kotlin_name
        body.append("\n")

    last = steps[-1]
    expr = f"API_BASE + CHAPTERS_PATH"
    for var, kotlin_name in vars_.items():
        expr += f'.replace("{{{var}}}", {kotlin_name})'
    expr += '.replace("{id}", manga.url)'

    paged = last.get("paged")
    extra_params = last.get("params") or {}
    segments = []
    for index, (param, value) in enumerate(extra_params.items()):
        prefix = "?" if index == 0 else "&"
        if isinstance(value, str) and value.startswith("{") and value.endswith("}"):
            var = value[1:-1]
            expression = "manga.url" if var == "id" else vars_.get(var, var)
            segments.append(f'"{prefix}{param}=" + {expression}')
        else:
            segments.append(f'"{prefix}{param}={value}"')
    if segments:
        expr += " + " + " + ".join(segments)

    items_path = _kotlin_str(last.get("items", "data"))

    if paged:
        page_param = paged.get("page_param", "page")
        start = paged.get("start", 1)
        total_pages = paged.get("total_pages", "")
        base_expr = expr
        body.append(
            f'''        val chapterItems = mutableListOf<JsonElement>()
        var page = {start}
        var totalPages = 1
        do {{
            val root = getJson({base_expr} + "&{page_param}=" + page)
            chapterItems += root.items({items_path})
            totalPages = root.number({_kotlin_str(total_pages)})
            page++
        }} while (page <= totalPages)

'''
        )
    else:
        body.append(f"        val chapterItems = getJson({expr}).items({items_path})\n\n")

    hooks = cfg.get("filter") or {}
    if (api.get("options") or {}).get("hide_paid"):
        opt = api["options"]["hide_paid"]
        body.append(
            f'''        val filtered = chapterItems.filterNot {{ item ->
            hidePaid && item.flag({_kotlin_str(opt.get("field", "isPaid"))}) &&
                item.number({_kotlin_str(opt.get("price_field", "unlockPrice"))}) > 0
        }}

        filtered.map {{ it.toSChapter() }}
'''
        )
    else:
        body.append("        chapterItems.map { it.toSChapter() }\n")

    body.append("    }\n\n")
    body.append(
        "    override fun chapterListParse(response: Response): List<SChapter> = throw UnsupportedOperationException()\n\n"
    )
    return "    // ================================ Главы =================================\n\n" + "".join(body)


# ---------------------------------------------------------------------------
# Страницы
# ---------------------------------------------------------------------------


def _pages_section(api) -> str:
    cfg = api.get("pages") or {}
    if not cfg:
        return ""
    image = cfg.get("image", "")
    quality = ""
    if site_has_quality(api):
        quality = ' + "?quality=" + preferredQuality'
    if image:
        url_expr = f'item.text({_kotlin_str(image)})'
    else:
        url_expr = "item.asText()"
    return f'''    // =============================== Страницы ===============================

    override fun pageListRequest(chapter: SChapter): Request =
        GET(API_BASE + PAGES_PATH.replace("{{chapter}}", chapter.url){quality}, headers)

    override fun pageListParse(response: Response): List<Page> =
        response.parseAs<JsonElement>(json).items({_kotlin_str(cfg.get("items", "data"))})
            .mapIndexed {{ index, item ->
                Page(index, imageUrl = {url_expr}.absoluteUrl(useProxy = false))
            }}

    override fun imageUrlParse(response: Response): String = throw UnsupportedOperationException()

'''


def site_has_quality(api) -> bool:
    return bool(api.get("quality_param"))


# ---------------------------------------------------------------------------
# Маппинг SManga / SChapter
# ---------------------------------------------------------------------------


def _mapping_section(api) -> str:
    fields = dict((api.get("list") or {}).get("fields") or {})
    chapter_cfg = api.get("chapters") or {}
    chapter_fields = dict(chapter_cfg.get("fields") or {})
    steps = chapter_cfg.get("steps") or []
    if steps:
        chapter_fields.update(steps[-1].get("fields") or {})
    fields.update(chapter_fields)
    status_map = api.get("status_map") or {}
    f = lambda key, default="": _kotlin_str(fields.get(key, default))  # noqa: E731

    status_lines = "".join(
        f'        "{key}" -> SManga.{value}\n' for key, value in status_map.items()
    )
    if not status_lines:
        status_lines = "        else -> SManga.UNKNOWN\n"
    else:
        status_lines += "        else -> SManga.UNKNOWN\n"

    title_expr = f"item.text({f('title')})"
    alt = fields.get("title_alt")
    if alt:
        title_expr = f"item.text({f('title')}).ifBlank {{ item.text({_kotlin_str(alt)}) }}"

    genres_field = fields.get("genres", "genres")
    genres_name = fields.get("genres_name")
    if genres_name:
        genre_expr = (
            f'item.items({_kotlin_str(genres_field)}).joinToString(", ") '
            f'{{ it.text({_kotlin_str(genres_name)}) }}'
        )
    else:
        genre_expr = f'item.items({_kotlin_str(genres_field)}).joinToString(", ") {{ it.asText() }}'

    description_expr = f"item.text({f('description')})"
    description_alt = fields.get("description_alt")
    if description_alt:
        description_expr = (
            f"item.text({f('description')}).ifBlank {{ item.text({_kotlin_str(description_alt)}) }}"
        )

    return f'''    // ================================ Маппинг ================================

    private fun JsonElement.toSManga(): SManga = SManga.create().apply {{
        val item = this@toSManga
        url = item.text({f("id")})
        title = {title_expr}
        thumbnail_url = item.text({f("cover")}).absoluteUrl()
        author = item.text({f("author")}).takeIf {{ it.isNotEmpty() }}
        artist = item.text({f("artist")}).takeIf {{ it.isNotEmpty() }}
        description = {description_expr}
        genre = {genre_expr}
        status = item.text({f("status")}).toSMangaStatus()
        initialized = false
    }}

    private fun String.toSMangaStatus(): Int = when (this) {{
{status_lines}    }}

    private fun JsonElement.toSChapter(): SChapter = SChapter.create().apply {{
        val item = this@toSChapter
        url = item.text({f("chapter_id", "_id")})
        name = item.text({f("chapter_title", "name")}).ifBlank {{
            "Глава " + item.text({f("chapter_number", "chapterNumber")}).removeSuffix(".0")
        }}
        chapter_number = item.decimal({f("chapter_number", "chapterNumber")}).toFloat()
        date_upload = item.text({f("chapter_date", "releaseDate")}).toEpochMillis()
    }}

    private fun String.toEpochMillis(): Long = runCatching {{
        OffsetDateTime.parse(this).toInstant().toEpochMilli()
    }}.getOrDefault(0L)

'''


# ---------------------------------------------------------------------------
# Фильтры
# ---------------------------------------------------------------------------


def t(key: str) -> str:
    return key


def genre_value_param(api) -> str:
    return ((api.get("genres") or {}).get("param")) or "genres"


def genres_config(api) -> dict:
    return api.get("genres") or {}


def _filters_section(site, api, genres: list[dict]) -> str:
    classes: list[str] = []
    entries: list[str] = []

    sorts = (api.get("list") or {}).get("sorts") or []
    if len(sorts) > 1:
        labels = ", ".join(f'"{s["label"]}"' for s in sorts)
        values = ", ".join(f'"{s["value"]}"' for s in sorts)
        classes.append(
            "    private class SortFilter :\n"
            "        Filter.Select<String>(\n"
            '            "Сортировка",\n'
            f"            arrayOf({labels}),\n"
            "        ) {\n"
            f"        val selected get() = arrayOf({values})[state]\n"
            "    }\n"
        )
        entries.append("        SortFilter(),")

    for entry in api.get("filters") or []:
        labels = ", ".join(f'"{label}"' for _, label in entry["values"])
        values = ", ".join(f'"{value}"' for value, _ in entry["values"])
        classes.append(
            f'    private class {entry["class"]} :\n'
            "        Filter.Select<String>(\n"
            f'            "{entry["name"]}",\n'
            f"            arrayOf({labels}),\n"
            "        ) {\n"
            f"        val selected get() = arrayOf({values})[state]\n"
            "    }\n"
        )
        entries.append(f'        {entry["class"]}(),')

    if genres:
        gf = api.get("genres") or {}
        list_lines = "".join(
            f'                Genre("{g["name"]}", "{g["id"]}"),\n' for g in genres
        )
        classes.append(
            "    private class GenreFilter :\n"
            "        Filter.Group<Genre>(\n"
            f'            "{gf.get("title", "Жанры")}",\n'
            "            listOf(\n"
            f"{list_lines}"
            "            ),\n"
            "        )\n\n"
            "    private class Genre(name: String, val id: String) : Filter.CheckBox(name)\n"
        )
        entries.append("        GenreFilter(),")

    if not classes:
        return ""

    return (
        "    // ================================ Фильтры ================================\n\n"
        + "\n".join(classes)
        + "\n    override fun getFilterList() = FilterList(\n"
        + "\n".join(entries)
        + "\n    )\n\n"
    )


# ---------------------------------------------------------------------------
# Константы
# ---------------------------------------------------------------------------


def _consts(site, api) -> str:
    list_cfg = api.get("list") or {}
    chapters = (api.get("chapters") or {}).get("steps") or []
    consts = [
        f'        private const val API_BASE = "{api.get("base", "")}"',
        f'        private const val MEDIA_BASE = "{api.get("media_base", site.base_url)}"',
        f'        private const val MEDIA_STRIP = "{api.get("media_strip", "")}"',
        f'        private const val MEDIA_PROXY = "{api.get("media_proxy", "")}"',
        f'        private const val LIST_PATH = "{list_cfg.get("path", "/")}"',
        f'        private const val SIZE = {list_cfg.get("size", 20)}',
        f'        private const val DEFAULT_SORT = "{list_cfg.get("default_sort", "")}"',
        f'        private const val LATEST_SORT = "{list_cfg.get("latest_sort", "")}"',
    ]
    if list_cfg.get("total_pages"):
        consts.append(f'        private const val TOTAL_PAGES_PATH = "{list_cfg["total_pages"]}"')
        consts.append(
            f'        private const val CURRENT_PAGE_PATH = "{list_cfg.get("current_page", list_cfg["total_pages"])}"'
        )
    if list_cfg.get("has_more"):
        consts.append(f'        private const val HAS_MORE_PATH = "{list_cfg["has_more"]}"')

    details = (api.get("details") or {}).get("path")
    if details:
        consts.append(f'        private const val DETAILS_PATH = "{details}"')
    if chapters:
        for idx, step in enumerate(chapters[:-1], start=1):
            consts.append(f'        private const val STEP{idx}_PATH = "{step["path"]}"')
        consts.append(f'        private const val CHAPTERS_PATH = "{chapters[-1]["path"]}"')
    pages = (api.get("pages") or {}).get("path")
    if pages:
        consts.append(f'        private const val PAGES_PATH = "{pages}"')
    if genres_config(api):
        consts.append(f'        private const val GENRE_PARAM = "{genres_config(api).get("param", "genres")}"')
    if site.qualities:
        consts.append('        private const val QUALITY_PREF = "preferred_quality"')
    return "\n".join(consts) + "\n"


def _kotlin_str(value) -> str:
    if value is None:
        return '""'
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'
