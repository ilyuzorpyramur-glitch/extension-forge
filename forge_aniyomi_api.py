"""Генерация aniyomi-расширения по карте JSON API (v2).

Учтены ловушки реальных сайтов:
  * сортировки, которых нет в API, молча дают «популярное» — поэтому список
    допустимых сортировок фиксируется в декларации;
  * карточки каталога могут повторяться — дубликаты отсекаются по url;
  * фильтр по жанрам собирается из карточек (отдельного эндпоинта нет);
  * API отдаёт 0–1 серию, а полный список лежит в плеере Kodik — серии
    добираются из страницы плеера.
"""

from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path

from forge import clean_kotlin

SOURCE_TEMPLATE = '''package @@PKG@@

import android.app.Application
import android.content.SharedPreferences
import androidx.preference.ListPreference
import androidx.preference.PreferenceScreen
import eu.kanade.tachiyomi.animesource.ConfigurableAnimeSource
import eu.kanade.tachiyomi.animesource.model.AnimeFilter
import eu.kanade.tachiyomi.animesource.model.AnimeFilterList
import eu.kanade.tachiyomi.animesource.model.AnimesPage
import eu.kanade.tachiyomi.animesource.model.SAnime
import eu.kanade.tachiyomi.animesource.model.SEpisode
import eu.kanade.tachiyomi.animesource.model.Video
import eu.kanade.tachiyomi.network.GET
import keiyoushi.utils.AnimeHttpLegacySource
import keiyoushi.utils.bodyString
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.Headers
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.Request
import okhttp3.Response
import org.json.JSONArray
import org.json.JSONObject
import uy.kohesive.injekt.Injekt
import uy.kohesive.injekt.api.get
import java.text.SimpleDateFormat
import java.util.Locale

/**
 * @@NAME@@ (@@HOST@@) — сгенерировано extension-forge по карте JSON API.
 *
 * Каталог, детали и серии берутся из JSON, плеер — @@PLAYER@@.
 */
class @@CLASS@@ :
    AnimeHttpLegacySource(),
    ConfigurableAnimeSource {

    override val name = "@@NAME@@"

    override val baseUrl = "@@BASE@@"

    override val lang = "ru"

    override val supportsLatest = true

    private val apiUrl = "$baseUrl@@API_PREFIX@@"

    private val preferences: SharedPreferences by lazy {
        Injekt.get<Application>().getSharedPreferences("source_$id", 0)
    }

    private val kodik by lazy { KodikExtractor(client, headers, baseUrl, preferences) }

    private val dateFormat by lazy { SimpleDateFormat("yyyy-MM-dd", Locale.US) }

    /** Связка «адрес серии → (id аниме, номер)». */
    private val episodeMeta = mutableMapOf<String, Pair<String, Float>>()

    /** id аниме → адрес плеера: нужен, когда API отдал не все серии. */
    private val playerByAnime = mutableMapOf<String, String>()

    override fun headersBuilder(): Headers.Builder = super.headersBuilder()
        .set("User-Agent", USER_AGENT)
        .set("Referer", "$baseUrl/")
        .set("Accept", "application/json, text/plain, */*")
        .set("Accept-Language", "ru-RU,ru;q=0.9,en;q=0.8")

    // =============================== Каталог ===============================

    private fun catalogUrl(page: Int, sort: String, query: String?, genre: String?): String {
        val builder = "$apiUrl@@CATALOG@@".toHttpUrl().newBuilder()
            .addQueryParameter("@@PAGE_PARAM@@", page.toString())
            .addQueryParameter("@@LIMIT_PARAM@@", PER_PAGE.toString())
            .addQueryParameter("@@SORT_PARAM@@", sort)
        if (!query.isNullOrBlank()) builder.addQueryParameter("@@SEARCH_PARAM@@", query)
        if (!genre.isNullOrBlank()) builder.addQueryParameter("@@GENRE_PARAM@@", genre)
        return builder.build().toString()
    }

    override fun popularAnimeRequest(page: Int): Request = GET(catalogUrl(page, DEFAULT_SORT, null, null), headers)

    override fun popularAnimeParse(response: Response): AnimesPage = parseCatalog(response)

    override fun latestUpdatesRequest(page: Int): Request = GET(catalogUrl(page, LATEST_SORT, null, null), headers)

    override fun latestUpdatesParse(response: Response): AnimesPage = parseCatalog(response)

    override fun searchAnimeRequest(page: Int, query: String, filters: AnimeFilterList): Request {
        var sort = DEFAULT_SORT
        var genre = ""
        filters.forEach { filter ->
            when (filter) {
                is SortFilter -> sort = filter.value
@@GENRE_BRANCH@@
                else -> {}
            }
        }
        return GET(catalogUrl(page, sort, query, genre), headers)
    }

    override fun searchAnimeParse(response: Response): AnimesPage = parseCatalog(response)

    private fun parseCatalog(response: Response): AnimesPage {
        val root = runCatching { JSONObject(response.bodyString()) }.getOrNull()
            ?: return AnimesPage(emptyList(), false)
        val items = dig(root, ITEMS_PATH) as? JSONArray
            ?: return AnimesPage(emptyList(), false)

        val animes = (0 until items.length())
            .mapNotNull { index -> (items.opt(index) as? JSONObject)?.toSAnime() }
            .distinctBy { it.url }

        val pagination = dig(root, PAGINATION_PATH) as? JSONObject
        val currentPage = pagination?.optInt(PAGE_FIELD, 1) ?: 1
        val totalPages = pagination?.optInt(PAGES_FIELD, 1) ?: 1
        return AnimesPage(animes, currentPage < totalPages)
    }

    private fun JSONObject.toSAnime(): SAnime = SAnime.create().apply {
        url = opt(ID_FIELD)?.toString().orEmpty()
        title = optString(TITLE_FIELD).ifBlank { optString(TITLE_ALT_FIELD) }
        thumbnail_url = optString(POSTER_FIELD).takeIf { it.isNotBlank() }?.let(::absoluteUrl)
        initialized = false
    }

    private fun absoluteUrl(raw: String): String = when {
        raw.startsWith("http://") || raw.startsWith("https://") -> raw
        raw.startsWith("//") -> "https:$raw"
        raw.startsWith("/") -> "$baseUrl$raw"
        else -> "$baseUrl@@POSTER_PREFIX@@$raw"
    }

    /** Kodik живёт на многих доменах; недоступный подменяем на рабочий. */
    private fun playerUrl(raw: String): String {
        val absolute = absoluteUrl(raw)
        if (!absolute.contains("kodik", ignoreCase = true)) return absolute
        return absolute.replace(KODIK_HOST_REGEX, "//$PLAYER_HOST/")
    }

    private fun dig(node: JSONObject, path: String): Any? {
        var current: Any? = node
        for (part in path.split(".")) {
            current = (current as? JSONObject)?.opt(part) ?: return null
        }
        return current
    }

    // =============================== Детали ===============================

    override fun animeDetailsRequest(anime: SAnime): Request = GET("$apiUrl@@DETAILS@@".replace("{id}", anime.url), headers)

    override fun animeDetailsParse(response: Response): SAnime {
        val root = runCatching { JSONObject(response.bodyString()) }.getOrNull()
            ?: return SAnime.create()
        val data = dig(root, DETAILS_DATA) as? JSONObject ?: return SAnime.create()

        data.optString(KODIK_FIELD).takeIf { it.isNotBlank() }?.let {
            playerByAnime[data.opt(ID_FIELD)?.toString().orEmpty()] = playerUrl(it)
        }

        return data.toSAnime().apply {
            description = data.optString(DESC_FIELD).takeIf { it.isNotBlank() }
            genre = genresOf(data)
            status = statusOf(data.optString(STATUS_FIELD))
            initialized = true
        }
    }

    private fun genresOf(data: JSONObject): String? {
        val array = data.optJSONArray(GENRES_FIELD) ?: return null
        val names = (0 until array.length()).mapNotNull { index ->
            when (val item = array.opt(index)) {
                is JSONObject -> item.optString("name").takeIf { it.isNotBlank() }
                is String -> item
                else -> null
            }
        }
        return names.takeIf { it.isNotEmpty() }?.joinToString(", ")
    }

    private fun statusOf(raw: String): Int = when (raw.lowercase()) {
        "ongoing", "онгоинг" -> SAnime.ONGOING
        "finished", "released", "completed" -> SAnime.COMPLETED
        else -> SAnime.UNKNOWN
    }

    // =============================== Серии ================================

    override fun episodeListRequest(anime: SAnime): Request = GET("$apiUrl@@EPISODES@@".replace("{id}", anime.url), headers)

    override fun episodeListParse(response: Response): List<SEpisode> {
        val segments = response.request.url.pathSegments
        val animeId = segments.getOrNull(segments.indexOf("anime") + 1).orEmpty()

        val root = runCatching { JSONObject(response.bodyString()) }.getOrNull()
        val items = root?.let { dig(it, EPISODES_ITEMS) as? JSONArray }

        val fromApi = items?.let { array ->
            (0 until array.length()).mapNotNull { index ->
                val item = array.opt(index) as? JSONObject ?: return@mapNotNull null
                val number = item.opt(EP_NUMBER)?.toString()?.toFloatOrNull() ?: 0f
                val player = item.optString(EP_PLAYER).takeIf { it.isNotBlank() } ?: return@mapNotNull null
                val url = playerUrl(player)
                episodeMeta[url] = animeId to number
                SEpisode.create().apply {
                    this.url = url
                    name = item.optString(EP_TITLE).ifBlank { "Серия " + number.toInt() }
                    episode_number = number
                    date_upload = parseDate(item.optString(EP_DATE))
                }
            }
        }.orEmpty()

        // Сайт часто отдаёт в API лишь часть серий: полный список лежит в плеере.
        // В приложении свежие серии должны быть сверху — как в остальных расширениях.
        val apiSorted = fromApi.distinctBy { it.episode_number }.sortedByDescending { it.episode_number }
        val expected = expectedEpisodes(animeId)
        if (apiSorted.size >= expected && apiSorted.isNotEmpty()) return apiSorted

        val fromPlayer = episodesFromPlayer(animeId)
        if (fromPlayer.isEmpty()) return apiSorted
        return fromPlayer
    }

    private fun expectedEpisodes(animeId: String): Int {
        val root = runCatching {
            client.newCall(GET("$apiUrl@@DETAILS@@".replace("{id}", animeId), headers)).execute()
                .use { JSONObject(it.bodyString()) }
        }.getOrNull() ?: return 0
        val data = dig(root, DETAILS_DATA) as? JSONObject ?: return 0
        return data.optInt("episodes_count", 0)
    }

    /** Разбирает список серий из страницы плеера Kodik. */
    private fun episodesFromPlayer(animeId: String): List<SEpisode> {
        val player = playerByAnime[animeId] ?: run {
            expectedEpisodes(animeId)
            playerByAnime[animeId]
        } ?: return emptyList()

        val page = runCatching {
            client.newCall(GET(player, kodikHeaders())).execute().use { it.bodyString() }
        }.getOrNull() ?: return emptyList()

        val episodes = mutableListOf<SEpisode>()
        for (tag in EPISODE_TAG_REGEX.findAll(page)) {
            val value = VALUE_REGEX.find(tag.value)?.groupValues?.get(1)?.toIntOrNull() ?: continue
            val title = TITLE_REGEX.find(tag.value)?.groupValues?.get(1).orEmpty()
            val number = value.toFloat()
            val url = "$player#e=$value"
            episodeMeta[url] = animeId to number
            episodes += SEpisode.create().apply {
                this.url = url
                name = title.ifBlank { "Серия $value" }
                episode_number = number
            }
        }
        // В разметке плеера список серий продублирован (десктоп и мобильный),
        // поэтому одинаковые номера схлопываем.
        return episodes
            .distinctBy { it.episode_number }
            .sortedByDescending { it.episode_number }
    }

    private fun kodikHeaders(): Headers = headers.newBuilder()
        .set("Referer", "$baseUrl/")
        .set("Origin", "https://$PLAYER_HOST")
        .build()

    private fun parseDate(raw: String): Long = runCatching {
        dateFormat.parse(raw.take(10))?.time
    }.getOrNull() ?: 0L

    // =============================== Видео ================================

    override suspend fun getVideoList(episode: SEpisode): List<Video> = withContext(Dispatchers.IO) {
        val number = episodeMeta[episode.url]?.second
            ?: EPISODE_HASH_REGEX.find(episode.url)?.groupValues?.get(1)?.toFloatOrNull()
            ?: episode.episode_number
        val target = episode.url.substringBefore("#")
        val videos = kodik.videos(target, 1, number.toInt())
        if (videos.isEmpty()) throw Exception("Плеер не вернул видео")
        videos
    }

    // ============================== Настройки =============================

    override fun setupPreferenceScreen(screen: PreferenceScreen) {
        ListPreference(screen.context).apply {
            key = KodikExtractor.PREF_QUALITY
            title = "Качество видео"
            entries = arrayOf("Максимальное", "1080p", "720p", "480p", "360p")
            entryValues = arrayOf("max", "1080", "720", "480", "360")
            summary = "%s"
            setDefaultValue("max")
        }.let(screen::addPreference)
    }

    override fun getFilterList(): AnimeFilterList = AnimeFilterList(
        SortFilter(),
@@GENRE_ENTRY@@
    )

    private class SortFilter :
        AnimeFilter.Select<String>(
            "Сортировка",
            arrayOf("По популярности", "По рейтингу", "По дате", "По просмотрам"),
        ) {
        val value: String
            get() = arrayOf("popular", "rating", "date", "views")[state]
    }
@@GENRE_FILTER@@
    companion object {
        private const val USER_AGENT =
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"

        private const val PLAYER_HOST = "@@PLAYER_HOST@@"

        private val KODIK_HOST_REGEX = Regex("""//kodik[a-z0-9.-]*\\.[a-z]{2,}/""")

        private val EPISODE_TAG_REGEX = Regex("""<option[^>]*data-hash="[^"]+"[^>]*>""", RegexOption.IGNORE_CASE)
        private val VALUE_REGEX = Regex("""value="(\\d+)"""")
        private val TITLE_REGEX = Regex("""data-title="([^"]*)"""")
        private val EPISODE_HASH_REGEX = Regex("""#e=(\\d+)""")

        private const val PER_PAGE = @@PER_PAGE@@
        private const val DEFAULT_SORT = "@@DEFAULT_SORT@@"
        private const val LATEST_SORT = "@@LATEST_SORT@@"

        private const val ITEMS_PATH = "@@ITEMS@@"
        private const val PAGINATION_PATH = "@@PAGINATION@@"
        private const val PAGE_FIELD = "@@PAGE_FIELD@@"
        private const val PAGES_FIELD = "@@PAGES_FIELD@@"

        private const val ID_FIELD = "@@ID_FIELD@@"
        private const val TITLE_FIELD = "@@TITLE_FIELD@@"
        private const val TITLE_ALT_FIELD = "@@TITLE_ALT_FIELD@@"
        private const val POSTER_FIELD = "@@POSTER_FIELD@@"

        private const val DETAILS_DATA = "@@DETAILS_DATA@@"
        private const val DESC_FIELD = "@@DESC_FIELD@@"
        private const val GENRES_FIELD = "@@GENRES_FIELD@@"
        private const val STATUS_FIELD = "@@STATUS_FIELD@@"
        private const val KODIK_FIELD = "@@KODIK_FIELD@@"

        private const val EPISODES_ITEMS = "@@EPISODES_ITEMS@@"
        private const val EP_NUMBER = "@@EP_NUMBER@@"
        private const val EP_TITLE = "@@EP_TITLE@@"
        private const val EP_PLAYER = "@@EP_PLAYER@@"
        private const val EP_DATE = "@@EP_DATE@@"
    }
}
'''

GRADLE_TEMPLATE = '''ext {{
    extName = '{name}'
    extClass = '.{cls}'
    extVersionCode = {version}
    isNsfw = {nsfw}
}}

apply plugin: "kei.plugins.extension.legacy"

dependencies {{
    implementation(project(':lib:playlistutils'))
}}
'''

DEFAULTS = {
    "prefix": "/api",
    "catalog": "/anime",
    "page_param": "page",
    "limit_param": "limit",
    "sort_param": "sort",
    "search_param": "search",
    "genre_param": "genre",
    "per_page": 24,
    "default_sort": "popular",
    "latest_sort": "date",
    "items": "data.items",
    "pagination": "data.pagination",
    "page_field": "current_page",
    "pages_field": "total_pages",
    "details": "/anime/{id}",
    "details_data": "data",
    "desc_field": "description",
    "genres_field": "genres",
    "status_field": "status",
    "kodik_field": "kodik_url",
    "episodes": "/anime/{id}/episodes",
    "episodes_items": "data.episodes",
    "ep_number": "number",
    "ep_title": "title",
    "ep_player": "player_url",
    "ep_date": "release_date",
    "id_field": "id",
    "title_field": "title_ru",
    "title_alt_field": "title_original",
    "poster_field": "poster",
    "poster_prefix": "/uploads/",
    "player": "kodik",
    "player_host": "kodikplayer.com",
    "genre_pages": 5,
}


def _display_name(raw: str, site_id: str) -> str:
    """Имя без домена и посторонних символов: сборка требует романизации."""
    base = str(raw or "").split(".")[0].strip()
    first = base.split(" ")[0] if base else ""
    if not first or not re.match(r"^[A-Za-z][A-Za-z0-9-]*$", first):
        return site_id.capitalize()

    # Разведка иногда берёт заголовок рекламы или чужого бренда:
    # если имя не связано с доменом, показываем имя сайта.
    norm_raw = re.sub(r"[^a-z0-9]", "", base.lower())
    norm_id = re.sub(r"[^a-z0-9]", "", site_id.lower())
    if norm_id and norm_id not in norm_raw and norm_raw not in norm_id:
        return site_id.capitalize()

    cleaned = re.sub(r"[^A-Za-z0-9 ]+", " ", base).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned or site_id.capitalize()


def _class_name(name: str) -> str:
    parts = re.split(r"[^A-Za-z0-9]+", name)
    return "".join(p[:1].upper() + p[1:] for p in parts if p)


def collect_genres(site, api: dict) -> list[dict]:
    """Жанры для фильтра: отдельного эндпоинта нет, поэтому собираем по карточкам.

    В каталоге жанры приходят названиями («Драма, Музыка»), а фильтр сайта
    принимает латинские slug'и (comedy, drama). Поэтому недостающие slug'и
    добираем со страниц тайтлов и кэшируем результат.
    """
    cache = Path(__file__).resolve().parent / "sites" / f"{site.site_id}.genres.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))

    header_key = str(api["genres_field"])
    paths = str(api["items"]).split(".")
    found: dict[str, str] = {}
    ids: list[str] = []

    for page in range(1, int(api["genre_pages"]) + 1):
        url = site.base_url.rstrip("/") + str(api["prefix"]) + str(api["catalog"]) + f"?page={page}&limit=50"
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=20) as resp:
                payload = json.load(resp)
        except Exception:
            break

        node = payload
        for part in paths:
            node = node.get(part) if isinstance(node, dict) else None
            if node is None:
                break
        if not isinstance(node, list):
            break

        for item in node:
            raw = item.get(header_key)
            if isinstance(raw, str):
                values = [part.strip() for part in raw.split(",")]
            elif isinstance(raw, list):
                values = raw
            else:
                values = []

            for genre in values:
                if isinstance(genre, dict):
                    slug = str(genre.get("slug") or "").strip()
                    name = str(genre.get("name") or "").strip()
                    if slug and name:
                        found[slug] = name
                elif isinstance(genre, str) and genre.strip():
                    found.setdefault(genre.strip(), "")

            item_id = item.get(str(api["id_field"]))
            if item_id is not None:
                ids.append(str(item_id))

    # Названия без slug'ов: добираем со страниц тайтлов.
    missing = [name for name, slug in found.items() if not slug]
    if missing:
        for item_id in ids[:120]:
            url = site.base_url.rstrip("/") + str(api["prefix"]) + str(api["details"]).replace("{id}", item_id)
            request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
            try:
                with urllib.request.urlopen(request, timeout=15) as resp:
                    payload = json.load(resp)
            except Exception:
                continue

            node = payload
            for part in str(api["details_data"]).split("."):
                node = node.get(part) if isinstance(node, dict) else None
            if not isinstance(node, dict):
                continue

            for genre in node.get(header_key) or []:
                if isinstance(genre, dict):
                    slug = str(genre.get("slug") or "").strip()
                    name = str(genre.get("name") or "").strip()
                    if slug and name:
                        found[slug] = name

            if not any(not slug for slug in found.values()):
                break

    result = [
        {"id": slug, "name": name or slug.capitalize()}
        for slug, name in sorted(found.items(), key=lambda kv: kv[0])
        # в фильтр идут только латинские slug-и: русские названия сайт не принимает
        if slug and re.fullmatch(r"[a-z0-9-]+", slug)
    ]
    cache.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def _genre_fragments(genres: list[dict]) -> dict:
    if not genres:
        return {
            "@@GENRE_BRANCH@@": "",
            "@@GENRE_ENTRY@@": "",
            "@@GENRE_FILTER@@": "",
        }

    entries = "".join(
        '        Genre("%s", "%s"),\n' % (g["name"].replace('"', '\\"'), g["id"]) for g in genres
    )
    branch = "                is GenreFilter -> genre = filter.toUri()\n"
    # Список жанров выносим в функцию: ktlint требует по одному аргументу на строку,
    # а внутри конструктора Group это условие выполнить неудобно.
    filt = (
        "\n    private fun genreList(): List<Genre> = listOf(\n"
        + entries
        + "    )\n"
        "\n"
        "    private inner class GenreFilter :\n"
        "        AnimeFilter.Group<Genre>(\n"
        '            "Жанры",\n'
        "            genreList(),\n"
        "        ) {\n"
        "        fun toUri(): String = state\n"
        "            .filter { it.state == AnimeFilter.TriState.STATE_INCLUDE }\n"
        '            .joinToString(",") { it.id }\n'
        "    }\n"
        "\n"
        '    private class Genre(name: String, val id: String) : AnimeFilter.TriState(name)\n'
    )
    return {
        "@@GENRE_BRANCH@@": branch,
        "@@GENRE_ENTRY@@": "        GenreFilter(),",
        "@@GENRE_FILTER@@": filt,
    }


def normalize_api(site) -> dict:
    """Приводит вложенную карту разведки к плоским ключам рендерера.

    recon пишет api.base / api.list.path / api.list.fields, а шаблон ждёт
    prefix / catalog / items / id_field. Без приведения генерировались
    модули с путями по умолчанию: собирались, но каталог всегда пустой.
    """
    raw = dict(site.api or {})
    out: dict = {}

    base = str(raw.get("base") or "").strip()
    if base:
        match = re.match(r"^https?://[^/]+(/.*)?$", base)
        prefix = (match.group(1) or "") if match else ""
        out["prefix"] = prefix.rstrip("/")

    listing = raw.get("list")
    if isinstance(listing, dict):
        mapping = (
            ("path", "catalog"),
            ("items", "items"),
            ("page_param", "page_param"),
            ("size_param", "limit_param"),
            ("size", "per_page"),
            ("query_param", "search_param"),
        )
        for src, dst in mapping:
            value = listing.get(src)
            if value not in (None, ""):
                out[dst] = value

        fields = listing.get("fields")
        if isinstance(fields, dict):
            if fields.get("id"):
                out["id_field"] = fields["id"]
            if fields.get("title"):
                out["title_field"] = fields["title"]
            if fields.get("genres"):
                out["genres_field"] = fields["genres"]

    for key in DEFAULTS:
        value = raw.get(key)
        if value not in (None, ""):
            out[key] = value
    return out


def preflight_catalog(site, api: dict) -> str:
    """Дёргает каталог по объявленной карте. Возвращает причину брака или ""."""
    url = (
        site.base_url.rstrip("/")
        + str(api["prefix"])
        + str(api["catalog"])
        + f"?{api['page_param']}=1&{api['limit_param']}={api['per_page']}"
    )
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=25) as resp:
            payload = json.load(resp)
    except Exception as exc:
        return f"каталог недоступен: {str(exc)[:60]}"

    node = payload
    for part in str(api["items"]).split("."):
        node = node.get(part) if isinstance(node, dict) else None
        if node is None:
            return f"в ответе нет пути items={api['items']}"
    if not isinstance(node, list) or not node:
        return "каталог вернул пустой список"
    return ""


def render(site, repo: Path) -> None:
    api = dict(DEFAULTS)
    api.update(normalize_api(site))

    # Каталог проверяем до записи файлов: иначе в репозиторий уходит модуль,
    # который собирается, но никогда ничего не показывает.
    reason = preflight_catalog(site, api)
    if reason:
        print(f"⛔ СКЕЛЕТ: {reason}")
        raise SystemExit(4)

    module = repo / "src" / site.lang / site.site_id
    pkg = f"eu.kanade.tachiyomi.animeextension.{site.lang}.{site.site_id}"
    pkg_dir = module / "src" / "eu" / "kanade" / "tachiyomi" / "animeextension" / site.lang / site.site_id
    pkg_dir.mkdir(parents=True, exist_ok=True)

    display_name = _display_name(site.name, site.site_id)
    cls = _class_name(display_name)
    host = site.base_url.split("//", 1)[-1].split("/")[0]

    version = site.ext_version_code
    if not version:
        gradle = module / "build.gradle"
        match = re.search(r"extVersionCode\s*=\s*(\d+)", gradle.read_text()) if gradle.exists() else None
        version = int(match.group(1)) + 1 if match else 1

    (module / "build.gradle").write_text(
        GRADLE_TEMPLATE.format(name=display_name, cls=cls, version=version, nsfw=str(site.nsfw).lower()),
        encoding="utf-8",
    )

    genres = collect_genres(site, api)

    code = SOURCE_TEMPLATE
    replacements = {
        "@@PKG@@": pkg,
        "@@CLASS@@": cls,
        "@@NAME@@": display_name,
        "@@HOST@@": host,
        "@@BASE@@": site.base_url,
        "@@API_PREFIX@@": str(api["prefix"]),
        "@@CATALOG@@": str(api["catalog"]),
        "@@PAGE_PARAM@@": str(api["page_param"]),
        "@@LIMIT_PARAM@@": str(api["limit_param"]),
        "@@SORT_PARAM@@": str(api["sort_param"]),
        "@@SEARCH_PARAM@@": str(api["search_param"]),
        "@@GENRE_PARAM@@": str(api["genre_param"]),
        "@@PER_PAGE@@": str(api["per_page"]),
        "@@DEFAULT_SORT@@": str(api["default_sort"]),
        "@@LATEST_SORT@@": str(api["latest_sort"]),
        "@@ITEMS@@": str(api["items"]),
        "@@PAGINATION@@": str(api["pagination"]),
        "@@PAGE_FIELD@@": str(api["page_field"]),
        "@@PAGES_FIELD@@": str(api["pages_field"]),
        "@@DETAILS@@": str(api["details"]),
        "@@DETAILS_DATA@@": str(api["details_data"]),
        "@@DESC_FIELD@@": str(api["desc_field"]),
        "@@GENRES_FIELD@@": str(api["genres_field"]),
        "@@STATUS_FIELD@@": str(api["status_field"]),
        "@@KODIK_FIELD@@": str(api["kodik_field"]),
        "@@EPISODES@@": str(api["episodes"]),
        "@@EPISODES_ITEMS@@": str(api["episodes_items"]),
        "@@EP_NUMBER@@": str(api["ep_number"]),
        "@@EP_TITLE@@": str(api["ep_title"]),
        "@@EP_PLAYER@@": str(api["ep_player"]),
        "@@EP_DATE@@": str(api["ep_date"]),
        "@@ID_FIELD@@": str(api["id_field"]),
        "@@TITLE_FIELD@@": str(api["title_field"]),
        "@@TITLE_ALT_FIELD@@": str(api["title_alt_field"]),
        "@@POSTER_FIELD@@": str(api["poster_field"]),
        "@@POSTER_PREFIX@@": str(api["poster_prefix"]),
        "@@PLAYER@@": str(api["player"]),
        "@@PLAYER_HOST@@": str(api["player_host"]),
    }
    replacements.update(_genre_fragments(genres))
    for key, value in replacements.items():
        code = code.replace(key, value)

    (pkg_dir / f"{cls}.kt").write_text(clean_kotlin(code), encoding="utf-8")

    if str(api["player"]).lower() == "kodik":
        template = Path(__file__).resolve().parent / "templates" / "aniyomi_kodik.kt"
        if not template.exists():
            raise FileNotFoundError(f"нет шаблона плеера: {template}")
        extractor = template.read_text(encoding="utf-8").replace("@@PKG@@", pkg)
        (pkg_dir / "KodikExtractor.kt").write_text(extractor.rstrip() + "\n", encoding="utf-8")

    print(f"  ✔ модуль: {module}")
    print(f"  ✔ класс: {cls}, версия {version}, жанров: {len(genres)}")
