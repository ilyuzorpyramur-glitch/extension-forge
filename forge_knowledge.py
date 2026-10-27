"""База знаний генератора: шаблон статического парсинга и предполётная проверка.

Знания собраны при разработке плагинов LNReader для litnet.com и libcat.ru —
подробности в knowledge/lnreader.md. Здесь они превращены в код:

  render_static_plugin(site, repo)  — собирает готовый плагин по декларации
                                      (api_style: html), без TODO по селекторам;
  lint_lnreader(site)               — предупреждения о типовых ошибках;
  doctor(repo, site_id)             — чек-лист «перед пушем».

Ключевые уроки, зашитые в шаблон:
  * UA сайта может включать другой фронтенд (Litnet: без UA приложения нет глав);
  * рейтинг выводим только если сайт даёт настоящее число (ld+json Litnet даёт 5 всем);
  * showLatestNovels обязателен — иначе «Популярное» и «Последние» одинаковы;
  * у сайта без глав страницы нарезаются на части по N страниц (path `base::1-5`);
  * SEO-вода вместо аннотации отбрасывается по regex, а не публикуется как описание.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

# ---------------------------------------------------------------------------
# Вспомогательное
# ---------------------------------------------------------------------------

def _ts(value: str) -> str:
    """Экранирование строки для одинарных кавычек TypeScript."""
    return value.replace("\\", "\\\\").replace("'", "\\'").replace("\n", " ")


def _class_name(site) -> str:
    raw = re.sub(r"[^A-Za-z0-9]+", " ", site.name).title().replace(" ", "")
    return raw or ("Site" + re.sub(r"\W", "", site.site_id).title())


def _filters_ts(site) -> str:
    cfg = (site.html or {}).get("filters") or {}
    if not cfg:
        return "  filters = {} as Filters;\n"
    key, spec = next(iter(cfg.items()))
    options = ",\n".join(
        f"        {{ label: '{_ts(str(label))}', value: '{_ts(str(value))}' }}"
        for label, value in spec.get("options", [])
    )
    return f"""  filters = {{
    {key}: {{
      type: FilterTypes.Picker,
      label: '{_ts(spec.get("label", key))}',
      value: '{_ts(str(spec.get("default", "")))}',
      options: [
{options},
      ],
    }},
  }} as Filters;
"""


# ---------------------------------------------------------------------------
# Генерация плагина со статическим парсингом
# ---------------------------------------------------------------------------

TEMPLATE = r"""import { Plugin } from '@/types/plugin';
import { FilterTypes, Filters } from '@libs/filterInputs';
import { defaultCover } from '@libs/defaultCover';
import { fetchApi } from '@libs/fetch';
import { NovelStatus } from '@libs/novelStatus';
import { load } from 'cheerio';

/**
 * Сгенерировано extension-forge по декларации sites/@@ID@@.yaml.
 * Источник: @@SITE@@
 * @@NOTES@@
 */
const CARD = {
  item: '@@ITEM@@',
  link: '@@LINK@@',
  linkMode: '@@LINK_MODE@@' as 'closest' | 'find',
  titleAttr: '@@TITLE_ATTR@@',
  titleRegex: '@@TITLE_REGEX@@',
  titleSelect: '@@TITLE_SELECT@@',
  coverSelect: '@@COVER_SELECT@@',
  coverAttr: '@@COVER_ATTR@@',
};

const DETAIL = {
  title: '@@D_TITLE@@',
  author: '@@D_AUTHOR@@',
  authorSeparator: '@@AUTHOR_SEP@@',
  cover: '@@D_COVER@@',
  coverAttr: '@@D_COVER_ATTR@@',
  summary: '@@D_SUMMARY@@',
  summaryFromLdjson: @@SUMMARY_LDJSON@@,
  summaryReject: '@@SUMMARY_REJECT@@',
  genres: '@@D_GENRES@@',
  genresFromLdjson: @@GENRES_LDJSON@@,
  ratingText: '@@RATING_TEXT@@',
  ongoingText: '@@ONGOING@@',
  completedText: '@@COMPLETED@@',
};

const LATEST = {
  url: '@@LATEST_URL@@',
  sort: '@@LATEST_SORT@@' as '' | 'id',
};

const PAGES = {
  mode: '@@CHAPTER_MODE@@' as 'list' | 'pages_range',
  perChapter: @@PAGES_PER_CHAPTER@@,
  totalSelector: '@@PAGES_TOTAL_SELECTOR@@',
  totalAttr: '@@PAGES_TOTAL_ATTR@@',
  pageUrl: '@@PAGE_URL@@',
  chapterSelector: '@@CHAPTER_SELECTOR@@',
  chapterNameStrip: '@@CHAPTER_STRIP@@',
  textSelector: '@@TEXT_SELECTOR@@',
  textRemove: '@@TEXT_REMOVE@@',
};

class @@CLASS@@ implements Plugin.PluginBase {
  id = '@@ID@@';
  name = '@@NAME@@';
  icon = 'src/ru/@@ID@@/icon.png';
  site = '@@SITE@@';
  version = '@@VERSION@@';

  private headers = {
    'User-Agent':
      '@@UA@@',
    'Accept-Language': 'ru-RU,ru;q=0.9,en;q=0.8',
  };

@@FILTERS@@
  private async fetchHtml(path: string): Promise<string> {
    const res = await fetchApi(this.site + path, { headers: this.headers });
    return res.text();
  }

  private absolute(src?: string): string {
    if (!src) return '';
    if (/^https?:\/\//.test(src)) return src;
    return this.site + (src.startsWith('/') ? '' : '/') + src;
  }

  private parseBookList(html: string): Plugin.NovelItem[] {
    const $ = load(html);
    const novels: Plugin.NovelItem[] = [];
    const seen = new Set<string>();

    $(CARD.item).each((_, element) => {
      const card = $(element);
      const link =
        CARD.linkMode === 'closest'
          ? card.closest(CARD.link)
          : card.find(CARD.link).first();
      const href = link.attr('href') || (CARD.linkMode === 'closest' ? card.attr('href') : '');
      if (!href) return;

      const image = CARD.coverSelect
        ? card.find(CARD.coverSelect).first()
        : card.find('img').first();

      const rawTitle = CARD.titleAttr
        ? image.attr(CARD.titleAttr) || ''
        : CARD.titleSelect
          ? card.find(CARD.titleSelect).first().text()
          : link.text();
      const title = (CARD.titleRegex
        ? rawTitle.match(new RegExp(CARD.titleRegex))?.[1] || rawTitle
        : rawTitle
      ).trim();

      if (!title) return;
      const path = href.replace(/^https?:\/\/[^/]+/, '').replace(/^\/+/, '');
      if (seen.has(path)) return;
      seen.add(path);

      const cover = image.attr(CARD.coverAttr) || image.attr('src');
      novels.push({
        name: title,
        path,
        cover: cover ? this.absolute(cover) : defaultCover,
      });
    });

    return novels;
  }

  private static ldBook($: ReturnType<typeof load>): Record<string, any> {
    for (const element of $('script[type="application/ld+json"]').toArray()) {
      try {
        const data = JSON.parse($(element).text());
        const list = Array.isArray(data) ? data : [data];
        const book = list.find((item: any) => item?.['@type'] === 'Book');
        if (book) return book;
      } catch {
        continue;
      }
    }
    return {};
  }

  async popularNovels(
    pageNo: number,
    { showLatestNovels, filters }: Plugin.PopularNovelsOptions<typeof this.filters>,
  ): Promise<Plugin.NovelItem[]> {
    // Отдельная лента свежего: без этого «Популярное» и «Последние» совпадают.
    if (showLatestNovels && LATEST.url) {
      return this.parseBookList(
        await this.fetchHtml(this.fill(LATEST.url, '', pageNo)),
      );
    }

    // Сайт не умеет сортировать по дате — берём новые id из первых страниц.
    if (showLatestNovels && LATEST.sort === 'id') {
      const collected: Plugin.NovelItem[] = [];
      const seen = new Set<string>();
      for (let page = 1; page <= 3; page += 1) {
        const items = this.parseBookList(
          await this.fetchHtml(this.fill('@@LIST_URL@@', this.genre(filters), page)),
        );
        for (const item of items) {
          if (seen.has(item.path)) continue;
          seen.add(item.path);
          collected.push(item);
        }
      }
      collected.sort((a, b) => this.bookId(b.path) - this.bookId(a.path));
      const start = (pageNo - 1) * 20;
      return collected.slice(start, start + 20);
    }

    const genre = this.genre(filters);
    return this.parseBookList(await this.fetchHtml(this.fill('@@LIST_URL@@', genre, pageNo)));
  }

  private genre(filters: unknown): string {
    return (filters as any)?.genre?.value || '@@DEFAULT_GENRE@@';
  }

  private bookId(path: string): number {
    return Number(path.match(/(\d+)-[^/]*\.html$/)?.[1] || 0);
  }

  private fill(template: string, genre: string, pageNo: number): string {
    let path = template
      .replace('{genre}', genre)
      .replace('{page}', String(pageNo));
    if (@@FIRST_PAGE_BARE@@ && pageNo <= 1) {
      path = path.replace(/page\/1\/$/, '');
    }
    return path;
  }

@@PARSE_NOVEL@@
@@PARSE_CHAPTER@@
  async searchNovels(searchTerm: string, pageNo: number): Promise<Plugin.NovelItem[]> {
    const url =
      pageNo > 1
        ? `/search/page/${pageNo}/?q=${encodeURIComponent(searchTerm)}`
        : `/search/?q=${encodeURIComponent(searchTerm)}`;
    return this.parseBookList(await this.fetchHtml(url));
  }

  resolveUrl(path: string, isNovel?: boolean): string {
    void isNovel;
    return this.site + '/' + path.replace(/^\/+/, '');
  }
}

export default new @@CLASS@@();
"""

PARSE_NOVEL_LIST_MODE = r"""  async parseNovel(novelPath: string): Promise<Plugin.SourceNovel> {
    const html = await this.fetchHtml('/' + novelPath.replace(/^\/+/, ''));
    const $ = load(html);
    const book = @@CLASS@@.ldBook($);
    const pageText = $.root().text().replace(/\s+/g, ' ');

    const titleNode = $(DETAIL.title).first().text().trim();
    const [head, tail] = DETAIL.authorSeparator && titleNode.includes(DETAIL.authorSeparator)
      ? [
          titleNode.split(DETAIL.authorSeparator)[0],
          titleNode.split(DETAIL.authorSeparator).slice(1).join(DETAIL.authorSeparator),
        ]
      : ['', titleNode];

    const novel: Plugin.SourceNovel = {
      path: novelPath,
      name: (DETAIL.authorSeparator ? tail : titleNode).trim() || book.name || '',
      author:
        (DETAIL.author ? $(DETAIL.author).first().text().trim() : head.trim()) ||
        book.author?.name ||
        undefined,
      cover:
        DETAIL.cover && $(DETAIL.cover).first().attr(DETAIL.coverAttr || 'src')
          ? this.absolute($(DETAIL.cover).first().attr(DETAIL.coverAttr || 'src'))
          : book.image || undefined,
      chapters: [],
    };

    const ratingMatch = DETAIL.ratingText ? pageText.match(new RegExp(DETAIL.ratingText)) : null;
    if (ratingMatch) novel.rating = Number(ratingMatch[1].replace(',', '.'));

    const summary = DETAIL.summaryFromLdjson
      ? String(book.description || '')
      : DETAIL.summary
        ? $(DETAIL.summary).first().text()
        : '';
    const cleaned = summary.replace(/<br\s*\/?>/gi, '\n').replace(/<[^>]+>/g, '').trim();
    if (cleaned && !(DETAIL.summaryReject && new RegExp(DETAIL.summaryReject).test(cleaned))) {
      novel.summary = cleaned;
    }

    const genres = DETAIL.genresFromLdjson
      ? Array.isArray(book.genre)
        ? book.genre
        : book.genre
          ? [book.genre]
          : []
      : DETAIL.genres
        ? $(DETAIL.genres)
            .toArray()
            .map(node => $(node).text().trim())
            .filter(Boolean)
        : [];
    const cleanGenres = genres.filter(
      (name: string) => name && !/^(LibCat|Главная|Книги|Каталог)$/i.test(name) && name !== novel.name,
    );
    if (cleanGenres.length) novel.genres = cleanGenres.join(', ');

    if (DETAIL.ongoingText && pageText.includes(DETAIL.ongoingText)) {
      novel.status = NovelStatus.Ongoing;
    } else if (DETAIL.completedText && pageText.includes(DETAIL.completedText)) {
      novel.status = NovelStatus.Completed;
    }

    const chapters: Plugin.ChapterItem[] = [];
    $(PAGES.chapterSelector).each((_, element) => {
      const link = $(element);
      const href = link.attr('href');
      if (!href) return;
      const name = link
        .clone()
        .find(PAGES.chapterNameStrip)
        .remove()
        .end()
        .text()
        .replace(/\s+/g, ' ')
        .trim();
      chapters.push({
        name: name || `Глава ${chapters.length + 1}`,
        path: href.replace(/^https?:\/\/[^/]+/, '').replace(/^\/+/, ''),
      });
    });

    novel.chapters = chapters;
    return novel;
  }
"""

PARSE_NOVEL_PAGES_MODE = r"""  async parseNovel(novelPath: string): Promise<Plugin.SourceNovel> {
    const html = await this.fetchHtml('/' + novelPath.replace(/^\/+/, ''));
    const $ = load(html);
    const pageText = $.root().text().replace(/\s+/g, ' ');

    const titleNode = $(DETAIL.title).first().text().trim();
    const separator = DETAIL.authorSeparator;
    const [head, tail] = separator && titleNode.includes(separator)
      ? [titleNode.split(separator)[0], titleNode.split(separator).slice(1).join(separator)]
      : ['', titleNode];

    const novel: Plugin.SourceNovel = {
      path: novelPath,
      name: (separator ? tail : titleNode).trim(),
      author:
        (DETAIL.author ? $(DETAIL.author).first().text().trim() : head.trim()) || undefined,
      cover: DETAIL.cover
        ? this.absolute($(DETAIL.cover).first().attr(DETAIL.coverAttr || 'src'))
        : undefined,
      chapters: [],
    };

    const ratingMatch = DETAIL.ratingText ? pageText.match(new RegExp(DETAIL.ratingText)) : null;
    if (ratingMatch) novel.rating = Number(ratingMatch[1].replace(',', '.'));

    const summaryNode = DETAIL.summary ? $(DETAIL.summary).first() : null;
    const summary = summaryNode ? summaryNode.text().replace(/\s+/g, ' ').trim() : '';
    if (summary && !(DETAIL.summaryReject && new RegExp(DETAIL.summaryReject).test(summary))) {
      novel.summary = summary;
    }

    const genres = DETAIL.genres
      ? $(DETAIL.genres)
          .toArray()
          .map(node => $(node).text().trim())
          .filter(
            (name: string) =>
              name && !/^(LibCat|Главная|Книги|Каталог)$/i.test(name) && name !== novel.name,
          )
      : [];
    if (genres.length) novel.genres = genres.join(', ');

    const totalAttr = $(PAGES.totalSelector).attr(PAGES.totalAttr) || '';
    const totalPages = Number(totalAttr) || 1;
    const perChapter = PAGES.perChapter || 5;

    const chapters: Plugin.ChapterItem[] = [];
    for (let from = 1; from <= totalPages; from += perChapter) {
      const to = Math.min(from + perChapter - 1, totalPages);
      chapters.push({
        name:
          totalPages <= perChapter
            ? 'Полный текст'
            : `Часть ${chapters.length + 1} (стр. ${from}–${to})`,
        path: `${novelPath}::${from}-${to}`,
      });
    }

    novel.chapters = chapters;
    return novel;
  }
"""

PARSE_CHAPTER_LIST_MODE = r"""  async parseChapter(chapterPath: string): Promise<string> {
    const html = await this.fetchHtml('/' + chapterPath.replace(/^\/+/, ''));
    const $ = load(html);
    const content = $(PAGES.textSelector).clone();
    content.find(PAGES.textRemove).remove();
    content.find('img').each((_, image) => {
      const src = $(image).attr('src');
      if (src?.startsWith('/')) $(image).attr('src', this.site + src);
    });
    return content.html() || '';
  }
"""

PARSE_CHAPTER_PAGES_MODE = r"""  async parseChapter(chapterPath: string): Promise<string> {
    const [base, range] = chapterPath.split('::');
    const [from, to] = (range || '').split('-').map(Number);
    if (!base || !from || !to) return '';

    const match = base.match(/(\d+)-(.+)\.html$/);
    const id = match?.[1] || '';
    const slug = match?.[2] || '';
    const dir = base.slice(0, base.lastIndexOf('/') + 1);

    const parts: string[] = [];
    for (let pageNo = from; pageNo <= to; pageNo += 1) {
      const pagePath =
        pageNo <= 1
          ? '/' + base
          : '/' + dir + PAGES.pageUrl.replace('{id}', id).replace('{page}', String(pageNo)).replace('{slug}', slug);
      const $ = load(await this.fetchHtml(pagePath));
      const content = $(PAGES.textSelector).clone();
      content.find(PAGES.textRemove).remove();
      content.find('img').each((_, image) => {
        const src = $(image).attr('src');
        if (src?.startsWith('/')) $(image).attr('src', this.site + src);
      });
      parts.push(content.html() || '');
    }

    return parts.filter(Boolean).join('\n');
  }
"""


def render_static_plugin(site, repo: Path) -> Path:
    """Собирает плагин LNReader по декларации (api_style: html)."""
    html = site.html or {}
    detail = html.get("detail", {}) or {}
    pages_mode = html.get("chapter_mode") == "pages_range"

    latest = html.get("list_latest_url", "")
    list_url = html.get("list_url", "/?page={page}")

    cls = _class_name(site)
    plugin = TEMPLATE
    replacements = {
        "@@CLASS@@": cls,
        "@@ID@@": site.site_id,
        "@@NAME@@": _ts(site.name),
        "@@SITE@@": site.base_url,
        "@@VERSION@@": site.version,
        "@@NOTES@@": _ts(site.notes or ""),
        "@@UA@@": _ts(html.get("ua") or "Mozilla/5.0 (Linux; Android 14) Chrome/120.0"),
        "@@FILTERS@@": _filters_ts(site),
        "@@ITEM@@": _ts(html.get("item", "a[href]")),
        "@@LINK@@": _ts(html.get("item_link", "a[href]")),
        "@@LINK_MODE@@": html.get("link_mode") or ("closest" if " img" in html.get("item", "") else "find"),
        "@@TITLE_ATTR@@": _ts(html.get("title_attr", "")),
        "@@TITLE_REGEX@@": _ts(html.get("title_regex", "")),
        "@@TITLE_SELECT@@": _ts(html.get("title_select", "")),
        "@@COVER_SELECT@@": _ts(html.get("cover_select", "")),
        "@@COVER_ATTR@@": _ts(html.get("cover_attr", "src")),
        "@@D_TITLE@@": _ts(detail.get("title", "h1")),
        "@@D_AUTHOR@@": _ts(detail.get("author", "")),
        "@@AUTHOR_SEP@@": _ts(detail.get("author_from_title", "")),
        "@@D_COVER@@": _ts(detail.get("cover", "")),
        "@@D_COVER_ATTR@@": _ts(detail.get("cover_attr", "src")),
        "@@D_SUMMARY@@": _ts(detail.get("summary", "")),
        "@@SUMMARY_LDJSON@@": "true" if detail.get("summary_from_ldjson") else "false",
        "@@SUMMARY_REJECT@@": _ts(html.get("summary_reject", "")),
        "@@D_GENRES@@": _ts(detail.get("genres", "")),
        "@@GENRES_LDJSON@@": "true" if detail.get("genres_from_ldjson") else "false",
        "@@RATING_TEXT@@": _ts(detail.get("rating_text", "")),
        "@@ONGOING@@": _ts(detail.get("ongoing_text", "")),
        "@@COMPLETED@@": _ts(detail.get("completed_text", "")),
        "@@CHAPTER_MODE@@": "pages_range" if pages_mode else "list",
        "@@PAGES_PER_CHAPTER@@": str(int(html.get("pages_per_chapter", 5))),
        "@@PAGES_TOTAL_SELECTOR@@": _ts(html.get("pages_total", "")),
        "@@PAGES_TOTAL_ATTR@@": _ts(html.get("pages_total_attr", "")),
        "@@PAGE_URL@@": _ts(html.get("page_url", "")),
        "@@CHAPTER_SELECTOR@@": _ts(html.get("chapter", "")),
        "@@CHAPTER_STRIP@@": _ts(html.get("chapter_name_strip", "span")),
        "@@TEXT_SELECTOR@@": _ts(html.get("text", "")),
        "@@TEXT_REMOVE@@": _ts(html.get("text_remove", "script, style")),
        "@@LATEST_URL@@": _ts(latest),
        "@@LATEST_SORT@@": _ts(html.get("latest_sort", "")),
        "@@LIST_URL@@": _ts(list_url),
        "@@DEFAULT_GENRE@@": _ts(
            str((html.get("filters") or {}).get("genre", {}).get("default", "all"))
        ),
        "@@FIRST_PAGE_BARE@@": "true" if html.get("list_first_page_bare") else "false",
        "@@PARSE_NOVEL@@": PARSE_NOVEL_PAGES_MODE if pages_mode else PARSE_NOVEL_LIST_MODE,
        "@@PARSE_CHAPTER@@": PARSE_CHAPTER_PAGES_MODE if pages_mode else PARSE_CHAPTER_LIST_MODE,
    }
    for token, value in replacements.items():
        plugin = plugin.replace(token, value)
    # блоки parseNovel/parseChapter подставляются последними и тоже содержат @@CLASS@@
    plugin = plugin.replace("@@CLASS@@", cls)

    target = repo / "plugins" / "russian" / f"{site.site_id}.ts"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(plugin, encoding="utf-8")
    return target


# ---------------------------------------------------------------------------
# Предупреждения и чек-лист
# ---------------------------------------------------------------------------

RULES = (
    ("диапазон глав", "chapter_mode: pages_range", "сайт без глав — страницы режутся на части"),
    ("лента свежего", "list_latest_url", "иначе «Популярное» и «Последние» одинаковы"),
    ("UA", "ua", "многие сайты меняют вёрстку по User-Agent"),
)


def lint_lnreader(site) -> list[str]:
    """Предупреждения по декларации — до генерации."""
    html = site.html or {}
    warnings: list[str] = []

    if site.api_style != "html":
        warnings.append(
            "api_style != html: будет создан заглушечный плагин с TODO — "
            "для статического сайта укажите api_style: html и блок html:"
        )
        return warnings

    if not html.get("list_latest_url") and html.get("latest_sort") != "id":
        warnings.append(
            "нет list_latest_url/latest_sort → вкладка «Последние» будет повторять «Популярное»"
        )
    if not html.get("ua") and not html.get("ua_free"):
        warnings.append("нет ua → если сайт отдаёт разную вёрстку по UA, глав не будет")
    if not html.get("chapter") and html.get("chapter_mode") != "pages_range":
        warnings.append("нет chapter → parseNovel вернёт пустой список глав")
    if not html.get("text"):
        warnings.append("нет text → глава будет пустой")

    detail = html.get("detail", {}) or {}
    if "rating" not in detail and not detail.get("rating_text"):
        warnings.append(
            "detail.rating не задан: если сайт публикует фиктивную оценку "
            "(ld+json ratingValue), укажите rating: false"
        )
    if (
        not detail.get("summary_from_ldjson")
        and not detail.get("summary")
        and not detail.get("summary_absent")
    ):
        warnings.append("описание не настроено — novel.summary будет пустым")
    return warnings


def doctor(repo: Path, site_id: str) -> list[tuple[bool, str]]:
    """Чек-лист «перед пушем» для уже сгенерированного плагина."""
    checks: list[tuple[bool, str]] = []
    plugin = repo / "plugins" / "russian" / f"{site_id}.ts"
    icon = repo / "public" / "static" / "src" / "ru" / site_id / "icon.png"
    icons_script = repo / "scripts" / "make-icons.py"

    checks.append((plugin.exists(), f"плагин на месте: {plugin.name}"))
    if plugin.exists():
        text = plugin.read_text(encoding="utf-8")
        checks.append((bool(re.search(r"version\s*=\s*['\"][\d.]+['\"]", text)), "поле version задано"))
        checks.append(("/icon.png" in text, "поле icon задано"))
        checks.append(("TODO" not in text, "в плагине нет TODO"))
        checks.append(
            ("rating" not in text or "ratingText" in text or "novel.rating" in text,
             "рейтинг только из реального источника"),
        )
    checks.append((icon.exists(), "иконка в public/static/src/ru/<id>/icon.png"))
    if icons_script.exists():
        checks.append(
            (f"src/ru/{site_id}/icon.png" in icons_script.read_text(encoding="utf-8"),
             "иконка заведена в scripts/make-icons.py"),
        )
    tsconfig = repo / "tsconfig.production.json"
    if tsconfig.exists() and (repo / "node_modules" / "typescript").exists():
        result = subprocess.run(
            ["npx", "tsc", "-p", str(tsconfig)],
            cwd=repo, capture_output=True, text=True,
        )
        checks.append((result.returncode == 0, "tsc -p tsconfig.production.json без ошибок"))
    checks.append((True, "version поднят относительно публикации (проверьте .dist/plugins.min.json)"))
    return checks
