package @@PKG@@

import android.content.SharedPreferences
import android.net.Uri
import android.util.Base64
import android.util.Log
import eu.kanade.tachiyomi.animesource.model.Video
import eu.kanade.tachiyomi.network.GET
import eu.kanade.tachiyomi.network.awaitSuccess
import keiyoushi.utils.bodyString
import okhttp3.FormBody
import okhttp3.Headers
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONObject

/**
 * Kodik: страница плеера подписана (`urlParams`), озвучки и серии лежат в разметке,
 * потоки отдаёт POST /ftor, ссылки закодированы сдвигом букв на 18 + base64.
 * Разметку Kodik разбираем регулярками: Jsoup теряет часть вложенных списков.
 */
class KodikExtractor(
    private val client: OkHttpClient,
    private val headers: Headers,
    private val baseUrl: String,
    private val preferences: SharedPreferences,
) {
    private data class Translation(
        val id: String,
        val title: String,
        val mediaType: String,
        val mediaId: String,
        val mediaHash: String,
        val kind: String,
    )

    suspend fun videos(playerUrl: String, season: Int, episode: Int): List<Video> {
        val page = fetch(playerUrl)
        if (page.isBlank()) {
            Log.e(TAG, "страница плеера не загрузилась")
            return emptyList()
        }

        val params = URL_PARAMS_REGEX.find(page)?.groupValues?.get(1)
            ?.let { runCatching { JSONObject(it) }.getOrNull() }
        if (params == null) {
            Log.e(TAG, "urlParams не найдены")
            return emptyList()
        }

        val episodeAttrs = episodeAttrs(page, episode)
        val parsed = parseTranslations(page)
        Log.e(TAG, "page=" + page.length + " translations=" + parsed.size)

        // Часть сайтов отдаёт ссылку сразу на серию: списка озвучек в разметке нет,
        // а ссылки запрашиваются по id/hash самой серии. Тогда собираем одну
        // «озвучку» из атрибутов серии, иначе плеер молча отдаёт пустой список.
        val translations = parsed.ifEmpty {
            val attrs = episodeAttrs ?: return emptyList()
            val seriesId = attrs["data-id"].orEmpty()
            val seriesHash = attrs["data-hash"].orEmpty()
            if (seriesId.isBlank() || seriesHash.isBlank()) return emptyList()
            listOf(
                Translation(
                    id = seriesId,
                    title = "Озвучка",
                    mediaType = "seria",
                    mediaId = seriesId,
                    mediaHash = seriesHash,
                    kind = "voice",
                ),
            )
        }
        val ignoreSubs = preferences.getBoolean(PREF_IGNORE_SUBS, true)

        val wanted = translations
            .filter { !ignoreSubs || !it.kind.contains("sub", ignoreCase = true) }
            .filter { matchesPreference(it.title) }
            .ifEmpty { translations }

        val qualities = preferredQualities()
        val videos = mutableListOf<Video>()

        for (translation in wanted) {
            val isSerial = translation.mediaType != "video" && episodeAttrs != null
            val id = if (isSerial) episodeAttrs?.get("data-id").orEmpty() else translation.mediaId
            val hash = if (isSerial) episodeAttrs?.get("data-hash").orEmpty() else translation.mediaHash
            if (id.isBlank() || hash.isBlank()) continue

            val links = requestLinks(params, translation, id, hash, isSerial) ?: continue
            Log.e(TAG, "ftor " + translation.title + " -> " + links.keys().asSequence().toList())

            val available = qualities.filter { links.has(it) }
            val ordered = available.ifEmpty {
                listOfNotNull(links.keys().asSequence().maxOrNull())
            }

            for (quality in ordered) {
                val src = links.optJSONArray(quality)?.optJSONObject(0)?.optString("src").orEmpty()
                if (src.isBlank()) continue
                val url = decode(src) ?: continue
                val label = translation.title + " " + quality + "p"
                videos += Video(url, label, url, headers = kodikHeaders())
            }
        }

        Log.e(TAG, "videos=" + videos.size)
        return videos.distinctBy { it.videoUrl }
    }

    private suspend fun fetch(url: String): String = runCatching {
        client.newCall(GET(url, kodikHeaders())).awaitSuccess().bodyString()
    }.getOrDefault("")

    private fun parseTranslations(page: String): List<Translation> = TRANSLATION_OPTION_REGEX.findAll(page).mapNotNull { match ->
        val attrs = tagAttrs(match.value)
        val id = attrs["data-id"].orEmpty().ifBlank { attrs["value"].orEmpty() }
        val mediaId = attrs["data-media-id"].orEmpty()
        if (id.isBlank() || mediaId.isBlank()) return@mapNotNull null
        Translation(
            id = id,
            title = attrs["data-title"].orEmpty().ifBlank { "Озвучка " + id },
            mediaType = attrs["data-media-type"].orEmpty(),
            mediaId = mediaId,
            mediaHash = attrs["data-media-hash"].orEmpty(),
            kind = attrs["data-translation-type"].orEmpty(),
        )
    }.toList()

    private fun episodeAttrs(page: String, episode: Int): Map<String, String>? = EPISODE_OPTION_REGEX.findAll(page)
        .map { tagAttrs(it.value) }
        .firstOrNull { it["value"] == episode.toString() && !it["data-hash"].isNullOrBlank() }

    private fun tagAttrs(tag: String): Map<String, String> = ATTR_REGEX.findAll(tag).associate { it.groupValues[1] to it.groupValues[2] }

    private suspend fun requestLinks(
        params: JSONObject,
        translation: Translation,
        id: String,
        hash: String,
        isSerial: Boolean,
    ): JSONObject? {
        val pd = params.optString("pd").ifBlank { return null }
        val body = FormBody.Builder()
            .add("d", params.optString("d"))
            .add("d_sign", Uri.decode(params.optString("d_sign")))
            .add("pd", pd)
            .add("pd_sign", Uri.decode(params.optString("pd_sign")))
            .add("ref", Uri.decode(params.optString("ref")))
            .add("ref_sign", Uri.decode(params.optString("ref_sign")))
            .add("type", if (isSerial) "seria" else "video")
            .add("id", id)
            .add("hash", hash)
            .add("translation_id", translation.id)
            .build()

        val request = Request.Builder()
            .url("https://" + pd + "/ftor")
            .post(body)
            .headers(kodikHeaders())
            .build()

        val data = runCatching {
            JSONObject(client.newCall(request).awaitSuccess().bodyString())
        }.getOrNull() ?: return null
        return data.optJSONObject("links")
    }

    private fun preferredQualities(): List<String> {
        val available = listOf("1080", "720", "480", "360", "240")
        val useMax = preferences.getBoolean(PREF_USE_MAX_QUALITY, true)
        if (useMax) return available
        val chosen = preferences.getStringSet(PREF_QUALITY, emptySet()).orEmpty()
        return available.filter { it in chosen } + available.filter { it !in chosen }
    }

    private fun matchesPreference(title: String): Boolean {
        val raw = preferences.getString(PREF_DUB_TEAM, "").orEmpty()
        if (raw.isBlank()) return true
        val keywords = raw.split(',').map { it.trim().lowercase() }.filter { it.isNotEmpty() }
        if (keywords.isEmpty()) return true
        val lower = title.lowercase()
        return keywords.any { lower.contains(it) }
    }

    /** Сдвиг букв на 18 внутри регистра + base64 → прямой адрес плейлиста. */
    private fun decode(src: String): String? = runCatching {
        val shifted = buildString(src.length) {
            src.forEach { ch ->
                if (ch in 'a'..'z' || ch in 'A'..'Z') {
                    val max = if (ch <= 'Z') 'Z'.code else 'z'.code
                    val code = ch.code + 18
                    append((if (max >= code) code else code - 26).toChar())
                } else {
                    append(ch)
                }
            }
        }
        val padded = shifted + "=".repeat((4 - shifted.length % 4) % 4)
        val decoded = String(Base64.decode(padded, Base64.DEFAULT), Charsets.UTF_8)
        if (decoded.startsWith("//")) "https:$decoded" else decoded
    }.getOrNull()

    private fun kodikHeaders(): Headers = headers.newBuilder()
        .set("Referer", "$baseUrl/")
        .set("Origin", "https://kodikplayer.com")
        .build()

    companion object {
        private const val TAG = "Animka"
        const val PREF_USE_MAX_QUALITY = "pref_use_max_quality"
        const val PREF_QUALITY = "pref_quality"
        const val PREF_IGNORE_SUBS = "pref_ignore_subs"
        const val PREF_DUB_TEAM = "pref_dub_team"

        private val URL_PARAMS_REGEX = Regex("""urlParams\s*=\s*'([^']+)'""")
        private val TRANSLATION_OPTION_REGEX = Regex("""<option\b[^>]*data-media-id="[^"]*"[^>]*>""")
        private val EPISODE_OPTION_REGEX = Regex("""<option\b[^>]*data-hash="[^"]*"[^>]*>""")
        private val ATTR_REGEX = Regex("""([a-zA-Z-]+)="([^"]*)"""")
    }
}
