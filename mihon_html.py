"""HTML-шаблон mihon-расширений (Jsoup) для сайтов без JSON API.

Поведение описывается декларацией `html:` в sites/<id>.yaml: шаблоны URL,
CSS-селекторы и правила извлечения текста (after/before/split/pick/map).
"""

from __future__ import annotations

HEADER = '''package {pkg}

import eu.kanade.tachiyomi.network.GET
import eu.kanade.tachiyomi.source.model.FilterList
import eu.kanade.tachiyomi.source.model.MangasPage
import eu.kanade.tachiyomi.source.model.Page
import eu.kanade.tachiyomi.source.model.SChapter
import eu.kanade.tachiyomi.source.model.SManga
import eu.kanade.tachiyomi.source.online.HttpSource
import keiyoushi.annotation.Source
import keiyoushi.network.rateLimit
import keiyoushi.utils.asJsoup
import okhttp3.Headers
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import org.jsoup.nodes.Document
import org.jsoup.nodes.Element
import java.text.SimpleDateFormat
import java.util.Locale
import kotlin.time.Duration.Companion.seconds

/**
 * {name} ({host}) — сгенерировано extension-forge (HTML).
 *
 * {notes}
 */
@Source
class {cls}(
    override val name: String,
    override val baseUrl: String,
    override val lang: String,
    override val id: Long,
) : HttpSource() {{

    override val supportsLatest = true

    override val client: OkHttpClient = network.client.newBuilder()
        .connectTimeout(30.seconds)
        .readTimeout(60.seconds)
        .rateLimit({rate}, 1.seconds)
        .build()

    override fun headersBuilder() = Headers.Builder().apply {{
        add("User-Agent", UA)
        add("Referer", "$baseUrl/")
        add("Accept-Language", "ru,en;q=0.8")
    }}

    private fun text(
        root: Element,
        selector: String,
        attr: String = "",
        after: String = "",
        before: String = "",
        split: String = "",
        pick: String = "",
    ): String {{
        if (selector.isEmpty()) return ""
        val element = root.selectFirst(selector) ?: return ""
        var value = if (attr.isEmpty()) element.text() else element.attr(attr)
        if (after.isNotEmpty()) value = value.substringAfter(after, value)
        if (before.isNotEmpty()) value = value.substringBefore(before, value)
        if (split.isNotEmpty()) {{
            val parts = value.split(split).map {{ it.trim() }}.filter {{ it.isNotEmpty() }}
            value = when (pick) {{
                "min" -> parts.minOrNull() ?: ""
                "max" -> parts.maxOrNull() ?: ""
                "last" -> parts.lastOrNull() ?: ""
                else -> parts.firstOrNull() ?: ""
            }}
        }}
        return value.trim()
    }}

    // =============================== Каталог ===============================

    private fun listRequest(page: Int, path: String): Request =
        GET(baseUrl + path.replace("{{page}}", page.toString()), headers)

    override fun popularMangaRequest(page: Int): Request = listRequest(page, LIST_PATH)

    override fun popularMangaParse(response: Response): MangasPage = parseList(response)

    override fun latestUpdatesRequest(page: Int): Request = listRequest(page, LATEST_PATH)

    override fun latestUpdatesParse(response: Response): MangasPage = parseList(response)

    override fun searchMangaRequest(page: Int, query: String, filters: FilterList): Request {{
        if (query.isNotBlank()) {{
            val path = SEARCH_PATH
                .replace("{{query}}", query.trim())
                .replace("{{page}}", page.toString())
            return GET(baseUrl + path, headers)
        }}
        return popularMangaRequest(page)
    }}

    override fun searchMangaParse(response: Response): MangasPage = parseList(response)

    private fun parseList(response: Response): MangasPage {{
        val document = response.asJsoup()
        val mangas = document.select(ITEM_SELECTOR).mapNotNull {{ card ->
            val link = card.selectFirst(LINK_SELECTOR) ?: return@mapNotNull null
            SManga.create().apply {{
                setUrlWithoutDomain(link.absUrl("href"))
                title = link.attr("title").ifBlank {{ link.text() }}.trim()
                thumbnail_url = card.selectFirst(COVER_SELECTOR)
                    ?.attr("abs:src")
                    ?.replace(COVER_REPLACE_FROM, COVER_REPLACE_TO)
                    .orEmpty()
                initialized = false
            }}
        }}
        val hasMore = HAS_NEXT_SELECTOR.isEmpty() || document.selectFirst(HAS_NEXT_SELECTOR) != null
        return MangasPage(mangas, hasMore)
    }}

    // =============================== Детали ===============================

    override fun mangaDetailsParse(response: Response): SManga {{
        val document = response.asJsoup()
        return SManga.create().apply {{
            title = text(document, TITLE_SELECTOR, after = TITLE_AFTER, before = TITLE_BEFORE, split = TITLE_SPLIT, pick = TITLE_PICK)
                .ifBlank {{ document.title() }}
            thumbnail_url = document.selectFirst(COVER_DETAIL_SELECTOR)?.attr("abs:src").orEmpty()
            author = text(document, AUTHOR_SELECTOR, after = AUTHOR_AFTER, split = AUTHOR_SPLIT, pick = AUTHOR_PICK)
                .takeIf {{ it.isNotEmpty() }}
            artist = text(document, ARTIST_SELECTOR, after = ARTIST_AFTER, split = ARTIST_SPLIT, pick = ARTIST_PICK)
                .takeIf {{ it.isNotEmpty() }}
            genre = text(document, GENRES_SELECTOR, after = GENRES_AFTER).takeIf {{ it.isNotEmpty() }}
            description = buildString {{
                val alt = text(document, ALT_SELECTOR, after = ALT_AFTER)
                if (alt.isNotEmpty()) append("Альтернативные названия: ").append(alt).append("\\n\\n")
                append(text(document, DESC_SELECTOR, after = DESC_AFTER))
            }}
            status = when (text(document, STATUS_SELECTOR, after = STATUS_AFTER)) {{
{status_map}
                else -> SManga.UNKNOWN
            }}
            initialized = false
        }}
    }}

    // ================================ Главы =================================

    override fun chapterListParse(response: Response): List<SChapter> {{
        val document = response.asJsoup()
        return document.select(CHAPTER_ITEM_SELECTOR).mapNotNull {{ element ->
            val link = element.selectFirst(CHAPTER_LINK_SELECTOR) ?: return@mapNotNull null
            SChapter.create().apply {{
                setUrlWithoutDomain(link.absUrl("href"))
                name = link.attr("title").ifBlank {{ link.text() }}.trim()
                chapter_number = parseChapterNumber(name, link.absUrl("href"))
                date_upload = parseDate(text(element, CHAPTER_DATE_SELECTOR, after = CHAPTER_DATE_AFTER))
                scanlator = element.select(CHAPTER_SCANLATOR_SELECTOR)
                    .joinToString(" / ") {{ it.text() }}
                    .takeIf {{ it.isNotEmpty() }}
            }}
        }}
    }}

    private fun parseChapterNumber(name: String, href: String): Float {{
        val fromName = Regex("""(\\d+(?:[.,]\\d+)?)""").find(name)?.groupValues?.get(1)
        val fromHref = Regex("""(\\d+(?:[.,]\\d+)?)/?$""").find(href)?.groupValues?.get(1)
        return (fromName ?: fromHref)?.replace(',', '.')?.toFloatOrNull() ?: -1f
    }}

    private fun parseDate(value: String): Long = when (value) {{
        "Сегодня" -> System.currentTimeMillis()
        "Вчера" -> System.currentTimeMillis() - 24 * 60 * 60 * 1000
        else -> runCatching { DATE_FORMAT.parse(value)?.time ?: 0L }.getOrDefault(0L)
    }}

    // =============================== Страницы ===============================

    override fun pageListParse(response: Response): List<Page> {{
        val document = response.asJsoup()
        val images = document.select(PAGE_IMAGE_SELECTOR)
        if (images.isNotEmpty()) {{
            return images.mapIndexed {{ index, image -> Page(index, imageUrl = image.attr("abs:src")) }}
        }}
        if (PAGE_LINK_SELECTOR.isNotEmpty()) {{
            return document.select(PAGE_LINK_SELECTOR).mapIndexed {{ index, link ->
                Page(index, imageUrl = link.absUrl("href"))
            }}
        }}
        return emptyList()
    }}

    override fun imageUrlParse(response: Response): String = throw UnsupportedOperationException()

    override fun getFilterList() = FilterList()

    companion object {{
        private const val UA = "{ua}"
        private val DATE_FORMAT = SimpleDateFormat("dd.MM.yyyy", Locale.US)

        private const val LIST_PATH = "{list_path}"
        private const val LATEST_PATH = "{latest_path}"
        private const val SEARCH_PATH = "{search_path}"
        private const val ITEM_SELECTOR = "{item_selector}"
        private const val LINK_SELECTOR = "{link_selector}"
        private const val COVER_SELECTOR = "{cover_selector}"
        private const val COVER_REPLACE_FROM = "{cover_from}"
        private const val COVER_REPLACE_TO = "{cover_to}"
        private const val HAS_NEXT_SELECTOR = "{has_next}"

        private const val TITLE_SELECTOR = "{title_selector}"
        private const val TITLE_AFTER = "{title_after}"
        private const val TITLE_BEFORE = "{title_before}"
        private const val TITLE_SPLIT = "{title_split}"
        private const val TITLE_PICK = "{title_pick}"
        private const val COVER_DETAIL_SELECTOR = "{cover_detail}"
        private const val AUTHOR_SELECTOR = "{author_selector}"
        private const val AUTHOR_AFTER = "{author_after}"
        private const val AUTHOR_SPLIT = "{author_split}"
        private const val AUTHOR_PICK = "{author_pick}"
        private const val ARTIST_SELECTOR = "{artist_selector}"
        private const val ARTIST_AFTER = "{artist_after}"
        private const val ARTIST_SPLIT = "{artist_split}"
        private const val ARTIST_PICK = "{artist_pick}"
        private const val GENRES_SELECTOR = "{genres_selector}"
        private const val GENRES_AFTER = "{genres_after}"
        private const val ALT_SELECTOR = "{alt_selector}"
        private const val ALT_AFTER = "{alt_after}"
        private const val DESC_SELECTOR = "{desc_selector}"
        private const val DESC_AFTER = "{desc_after}"
        private const val STATUS_SELECTOR = "{status_selector}"
        private const val STATUS_AFTER = "{status_after}"

        private const val CHAPTER_ITEM_SELECTOR = "{chapter_item}"
        private const val CHAPTER_LINK_SELECTOR = "{chapter_link}"
        private const val CHAPTER_DATE_SELECTOR = "{chapter_date}"
        private const val CHAPTER_DATE_AFTER = "{chapter_date_after}"
        private const val CHAPTER_SCANLATOR_SELECTOR = "{chapter_scanlator}"

        private const val PAGE_IMAGE_SELECTOR = "{page_image}"
        private const val PAGE_LINK_SELECTOR = "{page_link}"
    }}
}}
'''


def render(site, pkg: str, cls: str, host: str) -> str:
    cfg = site.html or {}
    listing = cfg.get("list") or {}
    details = cfg.get("details") or {}
    chapters = cfg.get("chapters") or {}
    pages = cfg.get("pages") or {}

    status_map = "".join(
        f'                "{key}" -> SManga.{value}\n'
        for key, value in (details.get("status_map") or {}).items()
    )

    def fld(name: str, default: str = "") -> dict:
        value = details.get(name)
        return value if isinstance(value, dict) else {"selector": value or default}

    title = fld("title")
    author = fld("author")
    artist = fld("artist")
    genres = fld("genres")
    alt = fld("alt")
    desc = fld("description")
    status = fld("status")

    values = {
        "pkg": pkg,
        "cls": cls,
        "name": site.name,
        "host": host,
        "notes": (site.notes or "").replace("*/", "* /"),
        "rate": cfg.get("rate", 2),
        "ua": cfg.get("user_agent") or "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/120.0 Safari/537.36",
        "list_path": listing.get("path", "/"),
        "latest_path": listing.get("latest_path") or listing.get("path", "/"),
        "search_path": listing.get("search_path", ""),
        "item_selector": listing.get("items", ""),
        "link_selector": listing.get("link", "a"),
        "cover_selector": listing.get("cover", "img"),
        "cover_from": listing.get("cover_replace_from", ""),
        "cover_to": listing.get("cover_replace_to", ""),
        "has_next": listing.get("has_next", ""),
        "title_selector": title.get("selector", "h1"),
        "title_after": title.get("after", ""),
        "title_before": title.get("before", ""),
        "title_split": title.get("split", ""),
        "title_pick": title.get("pick", ""),
        "cover_detail": details.get("cover", ".cover img") if not isinstance(details.get("cover"), dict) else details["cover"].get("selector", ".cover img"),
        "author_selector": author.get("selector", ""),
        "author_after": author.get("after", ""),
        "author_split": author.get("split", ""),
        "author_pick": author.get("pick", ""),
        "artist_selector": artist.get("selector", ""),
        "artist_after": artist.get("after", ""),
        "artist_split": artist.get("split", ""),
        "artist_pick": artist.get("pick", ""),
        "genres_selector": genres.get("selector", ""),
        "genres_after": genres.get("after", ""),
        "alt_selector": alt.get("selector", ""),
        "alt_after": alt.get("after", ""),
        "desc_selector": desc.get("selector", ""),
        "desc_after": desc.get("after", ""),
        "status_selector": status.get("selector", ""),
        "status_after": status.get("after", ""),
        "status_map": status_map,
        "chapter_item": chapters.get("items", ""),
        "chapter_link": chapters.get("link", "a"),
        "chapter_date": chapters.get("date_selector", ""),
        "chapter_date_after": chapters.get("date_after", ""),
        "chapter_scanlator": chapters.get("scanlator_selector", ""),
        "page_image": pages.get("images", ""),
        "page_link": pages.get("fallback_links", ""),
    }

    text = HEADER.replace("{{", "{").replace("}}", "}")
    for key, value in values.items():
        text = text.replace("{" + key + "}", str(value))
    return text
