# KNOWLEDGE — опыт генерации расширений (AnimeStock / animestock.ru)

Выжимка из полной разработки расширения `animestock.ru` (v1.4.1 → v1.4.4, 8 итераций CI).
Использовать при генерации ЛЮБОГО расширения для сайта на конструкторе Craftum
и при работе с Supabase/PostgREST. Референс-реализация: `mihon-ru-manga-repo/src/ru/animestock` (v1.4.4).

---

## 1. Анатомия сайта на Craftum

Сайт-конструктор: сервер отдаёт статический HTML **без контента списков** — каталог
рендерит клиентский `/static/articles.js`. HTML-парсер (Jsoup) по спискам бесполезен.

| Что | Где | Форма |
|---|---|---|
| Список статей рубрики | `GET /user-website-api/blog/rubrics/{id}/articles/?limit=&offset=&tags=&deleted=false&published=true` | JSON-массив статей |
| Теги рубрики | `GET /user-website-api/blog/tags/?rubric_id={id}&limit=100` | JSON-массив `{id, name}` |
| Полный каталог сайта | Supabase `anime_catalog` (`title, url, poster`) | см. §2 |
| Sitemap | `/sitemap.xml` | все пути + `lastmod`, иерархия `подборка/слаг` |
| RSS рубрики | `/{rubric_id}-feed.xml` | с обложками |

Статья: `{id, name, url, preview_url, first_published_at, is_published, service_mode,
rubric{id, name}, seo_title, seo_description}`.

Важные факты:

- **Сортировок нет.** Параметры `ordering/order/sort` молча игнорируются.
  «Популярное» и «Последнее» через API неотличимы — делайте их разными лентами
  (разные рубрики / объединение рубрик по дате), как в AnimeStock v1.4.2+.
- **Поиска нет** в CMS — см. §3.
- **Теги привязаны к рубрикам**: один и тот же жанр («Юри», «БДСМ») имеет РАЗНЫЕ id
  в разных рубриках. Словарь тегов храните как `имя → (id рубрики → id тега)`,
  и при поиске с тегом по нескольким рубрикам подставляйте id для каждой.
- **Пост манги = одна длинная полоса**: единственный `img.cli-article__image`
  (например 1362×57654). Маппинг doujinshi/long-strip: запись = выпуск,
  одна глава («Полоса»), `pageList` = список полос документа.
- Обложка/описание поста: `meta[property=og:image]`, `meta[property=og:description]`.
- Подборки-альбомы (страницы моделей/персонажей) несут **свою рубрику** в HTML:
  `data-heading-id="число"`. Посты этой рубрики = главы альбома. Если id нет —
  соберите посты модели по sitemap-префиксу (см. §4).
- Картинки: CDN `274418.selcdn.ru/...` и `static.craftum.com/...` (проксированные
  превью) — оба отдаются без referer-защиты.

## 2. Supabase / PostgREST — правила (грабли: 400)

Кабинет сайта висит на Supabase, ключ публикуемый (`sb_publishable_…` из фронтенда),
читать таблицы можно напрямую: `GET {SUPABASE}/rest/v1/{таблица}?…` с заголовками
`apikey: K` и `Authorization: Bearer K`.

Таблица `anime_catalog` — **самый полный каталог сайта** (у animestock.ru — 712 записей
с постерами: манга, хентай, все косплей-альбомы, страницы персонажей), в то время как
рубрики CMS покрывают лишь часть.

**Грабли, которые дают HTTP 400:**

- PostgREST принимает ТОЛЬКО свои параметры (`select`, `limit`, `offset`, `order`,
  `or`, `and`, …). Любой посторонний (`page=1`, `q=x`, `rubric=1634`) →
  `400 PGRST100 "failed to parse filter"`. Свою пагинацию/поиск поверх — нельзя;
  фильтруйте на языке PostgREST.
- Правильные формы (проверено живьём):
  - `or=(url.ilike.*kosple*,url.ilike.*stranitsa*,title.ilike.*косплей*)`
  - вложенное: `and=(or(url.ilike.*manga*,title.ilike.*манга*),title.ilike.*запрос*)`
  - `ilike`: `*` — wildcard, кириллица в шаблоне работает.
- Пагинация: `offset`/`limit`; сортировка: `order=created_at.desc`.

## 3. Поиск, когда API поиска нет

Двухслойная схема AnimeStock (переносить как образец):

1. **Без жанра** — по каталогу Supabase (`anime_catalog`): покрытие всего сайта,
   готовые постеры, фильтры рубрик — `or()`-фильтрами по `url`/`title`, текст запроса —
   `title.ilike.*q*` с вычищенными спецсимволами (`* ( ) ,`).
2. **С жанром** — по фидам рубрик CMS (`tags=`), т.к. жанровые теги существуют только
   в CMS. Для «Все рубрики» запрос делается к каждой рубрике с её id тега.
3. Фильтрация по названию в памяти — допустимый fallback для малых каталогов
   (в animestock.ru рубрики < 100 статей): параметры поиска переносятся в query
   самого запроса и разбираются в `Parse`.

Служебные пути каталога (`/feedback`, `/other`, `/Nav`, `/profile`, `/models`,
`/info`, `/admin*`, `/undress`) и записи «Страница без названия» — исключать.

## 4. Главы для альбомов/подборок

1. В HTML страницы-подборки найти `data-heading-id="<id>"` → запросить
   `rubrics/{id}/articles/` → посты = главы (имя = название поста, дата =
   `first_published_at`, сортировка по дате).
2. Fallback (id нет, но путь `/Album/…`): вытащить slug из заголовка, взять из
   sitemap пути из 2 сегментов с этим префиксом (исключив сам альбом и `/Cosplay/`)
   → главы «Фото N»; `lastmod` — дата главы; UUID-слаги называть «Фото N».
3. Одиночный пост — одна глава-«Полоса» (`url` = путь страницы).

## 5. Контракт стиля Kotlin (spotless/ktlint в CI keiyoushi)

CI падает на `spotlessKotlinCheck` ДО компиляции. Правила, за которые бьют
(все — из `.editorconfig` репозитория расширений):

- **function-signature / class-signature**: если параметры не влезают в строку —
  каждый параметр на своей строке, `)` на отдельной строке.
- **chain-method-continuation**: перенос перед КАЖДЫМ `.`/`?.` цепочки, отступ +4.
- **multiline-expression-wrapping**: если выражение после `=` многострочное —
  держите единую форму. Эмпирика CI: `fun f(): X = "строка".методы()` — цепочка
  на строке объявления с отступом +4; а присваивания `val x =` с длинным RHS —
  выражение с новой строки. Локальный ktlint 1.3.1 с CI расходится на этом правиле —
  **при расхождении доверять диффу из лога CI**, он печатает точный patch.
- Raw-строки `"""…"""` не могут заканчиваться кавычкой — regex с `"` в конце
  пишите обычной строкой с экранированием: `Regex("data-x=\"(\\d+)\"")`.
- `okhttp3.Headers` **не имеет** `newBuilder()` — собирайте `Headers.Builder()`.
- Дубль константы в `companion` = `Conflicting declarations` + каскад
  `Overload resolution ambiguity` по всему файлу.
- `max-line-length` = 140.
- DTO — `@Serializable` со значениями по умолчанию, парсинг `response.parseAs<List<T>>(json)`
  (нужен свой `Json { ignoreUnknownKeys = true; isLenient = true }` для чужих API).

## 6. Процедура релиза (манга-репозиторий)

1. `versionCode +1` в `build.gradle.kts` модуля.
2. Commit → push. Если CI уже успел закоммитить публикацию
   (`chore: publish extensions [skip ci]`) — `git fetch && git rebase origin/main`
   перед пушем.
3. CI: `spotlessKotlinCheck` → `assembleRelease` → APK в `apk/` + `index.json`/
   `index.min.json` (коммитом `chore: publish extensions [skip ci]`).
4. Иконка: добавить строку в `ICONS` в `.github/scripts/make-icons.py`
   (монограмма, акцент) и сгенерировать `python3 .github/scripts/make-icons.py <ext>`.
5. `registry.json` форджа: `version`, `version_code`, `last_status` — обновлять при
   каждом релизе (сторож читает их).

Типовые падения CI и лечение:

| Симптом | Причина | Лечение |
|---|---|---|
| `spotlessKotlinCheck FAILED` | стиль (см. §5) | править по диффу из лога CI |
| `compileReleaseKotlin` → `Unresolved reference` | нет константы/импорта, чужой API-метод | смотреть `e:` строки лога |
| push not fast-forward | CI закоммитил публикацию | rebase на `origin/main` |

## 7. Чек-лист генерации нового источника

- [ ] Определить платформу сайта (Craftum/WordPress/самопис) — по статике HTML и `/static/*.js`.
- [ ] Найти JSON-API каталога (или Supabase-каталог) — НЕ парсить пустой HTML.
- [ ] Проверить: сортировки? поиск? теги (id на рубрику!)? пагинация (limit+1)?
- [ ] Выбрать маппинг: длинные полосы → doujinshi-схема; альбомы → главы.
- [ ] Сверить стиль Kotlin с §5 ДО пуша (локально ktlint + поправка на §5).
- [ ] Прогнать живой запрос каждого эндпоинта с теми же параметрами, что уйдут из Kotlin.

## Remanga v1.4.2 — фикс загрузки глав (2026-10-27)
- Не фильтровать ветки по `count_chapters > 0`: API бывает отдаёт 0 у всех веток — эталон берёт max по `count_chapters` и всё равно запрашивает главы (API игнорирует счётчик).
- В JSON глав бывают явные `null` (name/upload_date/...): `Json { coerceInputValues = true }` спасает все поля с дефолтами.
- На запрос глав — явная проверка `isSuccessful`: иначе HTML-ответ DDoS-Guard валится нечитаемым SerializationException.

## Remanga v1.4.3 — главы: кэш веток, платные главы (2026-10-27)
- Live-проверка API через краулер-прокси (DDoS-Guard его пропускает): детали отдают и main_name/secondary_name наряду с rus_name/en_name; «Лицензировано» = status.id 6; upload_date с микросекундами (SimpleDateFormat с префиксным парсом ок).
- ВЕСЬ топ каталога is_licensed=true, главы is_paid: pages-ответ = {"msg":"Для просмотра нужно авторизироваться","content":{...без pages}}. exmanga.ru (редирект платных глав в эталоне) мёртв — платное нечитаемо без авторизации на сайте.
- fetchChapterList переопределён, ветки из кэша деталей (как в эталоне): второй подряд запрос /api/titles/{dir}/ перед главами — риск-фактор, убран.
- Платные главы новее последней бесплатной скрыты (это ещё не вышедшие бесплатно), старые помечены 💲 и открываются честной ошибкой.

## Remanga v1.4.4 — content-массив у закрытых тайтлов (2026-10-27)
- Симптом от пользователя (скриншот): Unexpected JSON token at offset N: Expected start of the object '{', but had '[' — у тайтлов, требующих авторизации, details отдаёт content как JSON-массив, а не объект (эталон это комментировал и ловил).
- Фикс: details/resloveBranch парсят JsonElement; content не JsonObject → IOException «Для просмотра контента необходима авторизация через WebView 🌐».
- Осторожно: не декодировать DetailsWrapperDto вслепую — сначала JsonElement-проверка content.
