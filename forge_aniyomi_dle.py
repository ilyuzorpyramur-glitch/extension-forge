"""Рендерер для DLE-сайтов: карточки регуляркой, плеер через relay → Kodik.

Схема, найденная на amedia.club и animesss.com:
  страница тайтла содержит data-player-anime-id
  GET /engine/ajax/controller.php?mod=player&id=<id>&slot=0 → JSON {data:{src: kodik-url}}
  дальше работает обычный KodikExtractor: список серий и ссылки на видео.
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
import android.util.Log
import androidx.preference.ListPreference
import androidx.preference.PreferenceScreen
import eu.kanade.tachiyomi.animesource.ConfigurableAnimeSource
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
import okhttp3.Request
import okhttp3.Response
import org.json.JSONObject
import org.jsoup.Jsoup
import org.jsoup.nodes.Document
import uy.kohesive.injekt.Injekt
import uy.kohesive.injekt.api.get

/**
 * @@NAME@@ (@@HOST@@) — сгенерировано extension-forge.
 *
 * Каталог и детали разбираются из HTML, плеер берётся через relay сайта
 * и передаётся в KodikExtractor.
 */
class @@CLASS@@ :
    AnimeHttpLegacySource(),
    ConfigurableAnimeSource {

    override val name = "@@NAME@@"

    override val baseUrl = "@@BASE@@"

    override val lang = "ru"

    override val supportsLatest = true

    private val preferences: SharedPreferences by lazy {
        Injekt.get<Application>().getSharedPreferences("source_$id", 0)
    }

    private val kodik by lazy { KodikExtractor(client, headers, baseUrl, preferences) }

    /** id тайтла на сайте, по которому relay отдаёт плеер. */
    private var currentAnimeId: String = ""

    override fun headersBuilder(): Headers.Builder = super.headersBuilder()
        .set("User-Agent", USER_AGENT)
        .set("Referer", "$baseUrl/")
        .set("Accept-Language", "ru-RU,ru;q=0.9,en;q=0.8")

    // =============================== Каталог ===============================

    private fun pageUrl(path: String, page: Int): String {
        if (page <= 1) return "$baseUrl$path"
        return "$baseUrl$path" + PAGE_SUFFIX.replace("{page}", page.toString())
    }

    override fun popularAnimeRequest(page: Int): Request = GET(pageUrl(CATALOG_PATH, page), headers)

    override fun popularAnimeParse(response: Response): AnimesPage = parseList(response)

    override fun latestUpdatesRequest(page: Int): Request = GET(pageUrl(LATEST_PATH, page), headers)

    override fun latestUpdatesParse(response: Response): AnimesPage = parseList(response)

    override fun searchAnimeRequest(page: Int, query: String, filters: eu.kanade.tachiyomi.animesource.model.AnimeFilterList): Request {
        val encoded = java.net.URLEncoder.encode(query, "UTF-8")
        val url = "$baseUrl" + SEARCH_PATH.replace("{query}", encoded)
        return GET(url, headers)
    }

    override fun searchAnimeParse(response: Response): AnimesPage = parseList(response)

    private fun parseList(response: Response): AnimesPage {
        val html = response.bodyString()
        val linkFilter = Regex(LINK_FILTER)
        val animes = ANCHOR_REGEX.findAll(html)
            .mapNotNull { match ->
                val href = match.groupValues[1]
                if (!linkFilter.containsMatchIn(href)) return@mapNotNull null
                val path = href.substringAfter(baseUrl, href)
                val body = match.groupValues[2]
                SAnime.create().apply {
                    url = path
                    title = cleanTitle(textOf(body)).ifBlank { altOf(body) }
                    thumbnail_url = imageOf(body)?.let(::absoluteUrl)
                    initialized = false
                }
            }
            .filter { it.title.isNotBlank() && !it.title.equals("Подробнее", true) }
            .distinctBy { it.url }
            .toList()

        val nextPage = html.contains("${PAGE_MARKER}${response.request.url.toString().let { pageOf(it) + 1 }}")
        return AnimesPage(animes, nextPage)
    }

    private fun pageOf(url: String): Int = Regex("""(?:page/|page=|/)(\\d+)""").find(url)?.groupValues?.get(1)?.toIntOrNull() ?: 1

    private fun textOf(fragment: String): String = Jsoup.parse(fragment).text()

    private fun altOf(fragment: String): String = Regex("""alt="([^"]*)"""").find(fragment)?.groupValues?.get(1).orEmpty()

    private fun imageOf(fragment: String): String? = Regex("""<img[^>]*src="([^"]+)"""").find(fragment)?.groupValues?.get(1)

    private fun cleanTitle(raw: String): String = raw
        .replace(Regex("""\\s*смотреть онлайн.*$""", RegexOption.IGNORE_CASE), "")
        .replace(Regex("""\\s+аниме$""", RegexOption.IGNORE_CASE), "")
        .trim()

    private fun absoluteUrl(raw: String): String = when {
        raw.startsWith("http://") || raw.startsWith("https://") -> raw
        raw.startsWith("//") -> "https:$raw"
        raw.startsWith("/") -> "$baseUrl$raw"
        else -> "$baseUrl/$raw"
    }

    // =============================== Детали ===============================

    override fun animeDetailsRequest(anime: SAnime): Request = GET(baseUrl + anime.url, headers)

    override fun animeDetailsParse(response: Response): SAnime {
        val html = response.bodyString()
        val doc = Jsoup.parse(html, baseUrl)

        currentAnimeId = Regex("""data-player-anime-id="(\\d+)"""").find(html)?.groupValues?.get(1).orEmpty()

        return SAnime.create().apply {
            url = response.request.url.encodedPath
            title = cleanTitle(doc.selectFirst(DETAILS_TITLE)?.text().orEmpty()).ifBlank { "Без названия" }
            thumbnail_url = (metaOf(doc, "og:image") ?: doc.selectFirst(POSTER_SELECTOR)?.absUrl("src"))
                ?.let(::absoluteUrl)
            description = metaOf(doc, "og:description")
                ?: doc.selectFirst(DESC_SELECTOR)?.text()?.takeIf { it.length > 80 }
            genre = doc.select(GENRE_SELECTOR).map { it.text().trim() }
                .filter { it.isNotEmpty() && it.length < 24 }
                .distinct()
                .take(12)
                .joinToString(", ")
                .ifBlank { null }
            initialized = true
        }
    }

    private fun metaOf(doc: Document, property: String): String? {
        val byProperty = doc.selectFirst("""meta[property="$property"]""")?.attr("content")
        if (!byProperty.isNullOrBlank()) return byProperty
        return doc.selectFirst("""meta[name="$property"]""")?.attr("content")?.takeIf { it.isNotBlank() }
    }

    // =============================== Серии ================================

    override fun episodeListRequest(anime: SAnime): Request {
        if (PLAYER_MODE == "iframe") return GET(baseUrl + anime.url, headers)
        val id = currentAnimeId.ifBlank { relayId(anime.url) }
        return GET(relayUrl(id, 0), headers)
    }

    private fun relayUrl(id: String, slot: Int): String =
        baseUrl + RELAY_URL.replace("{id}", id).replace("{slot}", slot.toString())

    private fun relayId(path: String): String = runCatching {
        val html = client.newCall(GET(baseUrl + path, headers)).execute().use { it.bodyString() }
        Regex("""data-player-anime-id="(\\d+)"""").find(html)?.groupValues?.get(1).orEmpty()
    }.getOrDefault("")

    override fun episodeListParse(response: Response): List<SEpisode> {
        if (PLAYER_MODE == "iframe") {
            // Плеер лежит прямо на странице тайтла: серии отдельными ссылками
            // такие сайты не отдают, поэтому показываем одну серию.
            val html = runCatching { response.bodyString() }.getOrNull() ?: return emptyList()
            val iframe = IFRAME_REGEX.find(html)?.groupValues?.get(1) ?: return emptyList()
            return listOf(
                SEpisode.create().apply {
                    url = absoluteUrl(iframe)
                    name = "Серия 1"
                    episode_number = 1f
                },
            )
        }

        // Сайт выдаёт разным сессиям разные плееры и раскладывает их по слотам,
        // поэтому перебираем слоты и берём Kodik, если он вообще предложен.
        val players = mutableListOf<String>()
        for (slot in 0 until MAX_SLOTS) {
            val src = playerAt(currentAnimeId, slot) ?: continue
            players += src
            if (src.contains("kodik", ignoreCase = true)) break
        }

        val kodikPlayer = players.firstOrNull { it.contains("kodik", ignoreCase = true) }
        if (kodikPlayer == null) {
            // Другой плеер (Alloha и подобные): отдаём одну серию, видео достанет getVideoList.
            val other = players.firstOrNull() ?: return emptyList()
            Log.e("Amedia", "не-Kodik плеер: " + other.take(80))
            return listOf(
                SEpisode.create().apply {
                    url = other
                    name = "Серия 1"
                    episode_number = 1f
                },
            )
        }

        val page = runCatching {
            client.newCall(GET(kodikPlayer, playerHeaders())).execute()
                .use { it.bodyString() }
        }.onFailure { Log.e("Amedia", "player page error", it) }.getOrNull() ?: return emptyList()

        Log.e("Amedia", "page=" + page.length + " options=" + EPISODE_OPTION_REGEX.findAll(page).count())

        return EPISODE_OPTION_REGEX.findAll(page).mapNotNull { match ->
            val attrs = attrsOf(match.value)
            val number = attrs["value"]?.toFloatOrNull() ?: return@mapNotNull null
            val title = attrs["data-title"].orEmpty()
            SEpisode.create().apply {
                url = "$kodikPlayer#e=" + number.toEpisodeNumber()
                name = title.ifBlank { "Серия " + number.toEpisodeNumber() }
                episode_number = number
            }
        }
            .distinctBy { it.episode_number }
            .sortedByDescending { it.episode_number }
            .toList()
    }

    private fun Float.toEpisodeNumber(): String = if (this % 1f == 0f) toInt().toString() else toString()

    private fun playerAt(id: String, slot: Int): String? {
        val body = runCatching {
            client.newCall(GET(relayUrl(id, slot), headers)).execute().use { it.bodyString() }
        }.getOrNull() ?: return null
        return runCatching { JSONObject(body).optJSONObject("data")?.optString("src") }
            .getOrNull()
            ?.takeIf { it.isNotBlank() }
    }

    /** Kodik отдаёт разметку только браузерному User-Agent. */
    private fun playerHeaders(): Headers = headers.newBuilder()
        .set("User-Agent", USER_AGENT)
        .set("Referer", "$baseUrl/")
        .set("Origin", "https://kodikplayer.com")
        .build()

    private fun attrsOf(tag: String): Map<String, String> = Regex("""([a-z-]+)="([^"]*)"""").findAll(tag)
        .associate { it.groupValues[1] to it.groupValues[2] }

    // =============================== Видео ================================

    override suspend fun getVideoList(episode: SEpisode): List<Video> = withContext(Dispatchers.IO) {
        val number = Regex("""#e=(\\d+)""").find(episode.url)?.groupValues?.get(1)?.toFloatOrNull()
            ?: episode.episode_number
        val player = episode.url.substringBefore("#")
        val videos = if (player.contains("kodik", ignoreCase = true)) {
            kodik.videos(player, 1, number.toInt())
        } else {
            directVideos(player)
        }
        if (videos.isEmpty()) throw Exception("Плеер не вернул видео")
        videos
    }

    /** Не-Kodik плееры (Alloha и подобные) обычно кладут ссылку прямо в разметку. */
    private fun directVideos(player: String): List<Video> = runCatching {
        val page = client.newCall(GET(player, playerHeaders())).execute().use { it.bodyString() }
        val decoded = ATOB_REGEX.find(page)?.groupValues?.get(1)?.let(::decodePlaylist).orEmpty()
        val plain = Regex("""https?://[^"'\\s]+\.(?:m3u8|mp4)[^"'\\s]*""")
            .findAll(page)
            .map { it.value.replace("""\/""", "/") }
            .toList()
        val links = decoded.toMutableList()
        plain.forEachIndexed { index, url ->
            links += Video(url, "Видео " + (index + 1), url, headers = playerHeaders())
        }
        Log.e("Amedia", "direct links=" + links.size)
        links.distinctBy { it.videoUrl }
    }.getOrElse {
        Log.e("Amedia", "direct error", it)
        emptyList()
    }

    /** intrdb и подобные прячут список качеств в base64: «[480p]url,[720p]url». */
    private fun decodePlaylist(encoded: String): List<Video> = runCatching {
        val text = String(android.util.Base64.decode(encoded, android.util.Base64.DEFAULT))
        text.split(",").mapNotNull { part ->
            val match = PLAYLIST_ITEM_REGEX.find(part.trim()) ?: return@mapNotNull null
            val quality = match.groupValues[1]
            val url = match.groupValues[2].replace("""\/""", "/")
            Video(url, quality, url, headers = playerHeaders())
        }
    }.getOrDefault(emptyList())

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

    companion object {
        private const val USER_AGENT =
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"

        private const val CATALOG_PATH = "@@CATALOG@@"
        private const val LATEST_PATH = "@@LATEST@@"
        private const val SEARCH_PATH = "@@SEARCH@@"
        private const val PAGE_SUFFIX = "@@PAGE_SUFFIX@@"
        private const val PAGE_MARKER = "@@PAGE_MARKER@@"
        private const val LINK_FILTER = """@@LINK_RE@@"""

        private const val DETAILS_TITLE = "@@TITLE_SEL@@"
        private const val POSTER_SELECTOR = "@@POSTER_SEL@@"
        private const val DESC_SELECTOR = "@@DESC_SEL@@"
        private const val GENRE_SELECTOR = "@@GENRE_SEL@@"

        private const val RELAY_URL = "@@RELAY@@"
        private const val MAX_SLOTS = 4
        private const val PLAYER_MODE = "@@PLAYER_MODE@@"
        private val IFRAME_REGEX = Regex("""<iframe[^>]*src="([^"]+)"""", RegexOption.IGNORE_CASE)
        private val ATOB_REGEX = Regex("""file:\s*atob\("([^"]+)"\)""")
        private val PLAYLIST_ITEM_REGEX = Regex("""\[([^\]]+)](https?://\S+)""")

        private val ANCHOR_REGEX = Regex(
            """<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>""",
            setOf(RegexOption.DOT_MATCHES_ALL, RegexOption.IGNORE_CASE),
        )
        private val EPISODE_OPTION_REGEX = Regex("""<option[^>]*data-hash="[^"]+"[^>]*>""", RegexOption.IGNORE_CASE)
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
    "catalog": "/",
    "latest": "/",
    "search": "/search/?q={query}",
    "page_suffix": "page/{page}/",
    "page_marker": "page/",
    "link_re": r"[^\"']+\.html",
    "title_sel": "h1",
    "poster_sel": "img",
    "desc_sel": "div",
    "genre_sel": "a[href*='/genre/']",
    "relay": "/engine/ajax/controller.php?mod=player&id={id}&slot={slot}",
    "player_mode": "relay",
}


def _display_name(raw: str, site_id: str) -> str:
    base = str(raw or "").split(".")[0].strip()
    first = base.split(" ")[0] if base else ""
    if not first or not re.match(r"^[A-Za-z][A-Za-z0-9-]*$", first):
        return site_id.capitalize()
    norm_raw = re.sub(r"[^a-z0-9]", "", base.lower())
    norm_id = re.sub(r"[^a-z0-9]", "", site_id.lower())
    if norm_id and norm_id not in norm_raw and norm_raw not in norm_id:
        return site_id.capitalize()
    return re.sub(r"\s+", " ", re.sub(r"[^A-Za-z0-9 ]+", " ", base)).strip() or site_id.capitalize()


def _class_name(name: str) -> str:
    parts = re.split(r"[^A-Za-z0-9]+", name)
    return "".join(p[:1].upper() + p[1:] for p in parts if p)


def preflight(site, cfg: dict) -> str:
    """Проверяет, что каталог реально отдаёт карточки по нашему шаблону."""
    url = site.base_url.rstrip("/") + str(cfg["catalog"])
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=25) as resp:
            html = resp.read().decode("utf-8", "replace")
    except Exception as exc:
        return f"каталог недоступен: {str(exc)[:60]}"

    try:
        pattern = re.compile(str(cfg["link_re"]))
    except re.error as exc:
        return f"плохой шаблон ссылки: {exc}"

    anchors = re.findall(r'<a\b[^>]*href="([^"]+)"[^>]*>', html, re.I)
    hits = [href for href in anchors if pattern.search(href)]
    if len(hits) < 5:
        return f"шаблон ссылки нашёл {len(hits)} карточек"
    return ""


def render(site, repo: Path) -> None:
    cfg = dict(DEFAULTS)
    html_cfg = (site.api or {}).get("html") or {}
    for key in DEFAULTS:
        value = html_cfg.get(key)
        if value not in (None, ""):
            cfg[key] = value
    for key, value in (site.api or {}).items():
        if key in DEFAULTS and value not in (None, ""):
            cfg[key] = value

    reason = preflight(site, cfg)
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

    version = site.ext_version_code or 1
    (module / "build.gradle").write_text(
        GRADLE_TEMPLATE.format(name=display_name, cls=cls, version=version, nsfw=str(site.nsfw).lower()),
        encoding="utf-8",
    )

    code = SOURCE_TEMPLATE
    replacements = {
        "@@PKG@@": pkg,
        "@@CLASS@@": cls,
        "@@NAME@@": display_name,
        "@@HOST@@": host,
        "@@BASE@@": site.base_url,
        "@@CATALOG@@": str(cfg["catalog"]),
        "@@LATEST@@": str(cfg["latest"]),
        "@@SEARCH@@": str(cfg["search"]),
        "@@PAGE_SUFFIX@@": str(cfg["page_suffix"]),
        "@@PAGE_MARKER@@": str(cfg["page_marker"]),
        "@@LINK_RE@@": str(cfg["link_re"]),
        "@@TITLE_SEL@@": str(cfg["title_sel"]),
        "@@POSTER_SEL@@": str(cfg["poster_sel"]),
        "@@DESC_SEL@@": str(cfg["desc_sel"]),
        "@@GENRE_SEL@@": str(cfg["genre_sel"]),
        "@@RELAY@@": str(cfg["relay"]),
        "@@PLAYER_MODE@@": str(cfg["player_mode"]),
    }
    for key, value in replacements.items():
        code = code.replace(key, value)

    (pkg_dir / f"{cls}.kt").write_text(clean_kotlin(code), encoding="utf-8")

    template = Path(__file__).resolve().parent / "templates" / "aniyomi_kodik.kt"
    extractor = template.read_text(encoding="utf-8").replace("@@PKG@@", pkg)
    (pkg_dir / "KodikExtractor.kt").write_text(extractor.rstrip() + "\n", encoding="utf-8")

    print(f"  ✔ модуль: {module}")
    print(f"  ✔ класс: {cls}, версия {version}")
