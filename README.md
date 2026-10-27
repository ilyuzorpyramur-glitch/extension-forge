<h1 align="center">⚙️ extension-forge</h1>

<p align="center">
  <strong>Генератор расширений и плагинов для Tadami / Aniyomi / Mihon / LNReader</strong>
</p>

<p align="center">
  <a href="https://github.com/ilyuzorpyramur-glitch/extension-forge/actions"><img src="https://github.com/ilyuzorpyramur-glitch/extension-forge/actions/workflows/monthly-watch.yml/badge.svg" alt="watchdog"></a>
  <img src="https://img.shields.io/badge/цели-аниме%20·%20манга%20·%20ранобэ-8ca9ff" alt="targets">
  <img src="https://img.shields.io/badge/сторож-раз%20в%20месяц-90ee90" alt="watchdog">
</p>

---

Один YAML-файл с декларацией сайта → готовое расширение в нужном репозитории:
иконки в едином стиле, настройки качества, выбор качества по умолчанию,
автоинкремент версии и ежемесячный сторож с самовосстановлением.

## Как это работает

```
sites/example.yaml      ← вы описываете сайт (id, URL, селекторы, качества)
        │
        ▼
   forge generate       ← генератор разворачивает модуль расширения
        │                  в целевом репозитории
        ▼
  git commit + push     ← CI целевого репозитория собирает APK,
        │                  подписывает и публикует индекс
        ▼
   watchdog (1/мес)     ← проверка живости сайта и селекторов,
                           самопочинка + bump версии при поломке
```

## Целевые репозитории

| Репозиторий | Тип | Приложение |
|---|---|---|
| [mihon-ru-manga-repo](https://github.com/ilyuzorpyramur-glitch/mihon-ru-manga-repo) | манга | Mihon / Tachiyomi-форки |
| [aniyomi-ru-anime-repo](https://github.com/ilyuzorpyramur-glitch/aniyomi-ru-anime-repo) | аниме | Aniyomi / **Tadami** |
| [lnreader-ru-plugins](https://github.com/ilyuzorpyramur-glitch/lnreader-ru-plugins) | ранобэ | LNReader |

## 🚀 Быстрый старт

```bash
# 1. Скопируйте пример декларации
cp sites/example.yaml sites/my_site.yaml
# отредактируйте под свой сайт

# 2. Сгенерируйте расширение (создастся в целевом репо)
python3 forge.py generate sites/my_site.yaml

# 3. Заполните TODO по селекторам в сгенерированном коде,
#    закоммитьте и запушьте — CI всё соберёт и опубликует.
```

### Команды

| Команда | Что делает |
|---|---|
| `forge.py generate <site.yaml>` | создать расширение в целевом репозитории |
| `forge.py bump <mihon\|aniyomi\|lnreader> <site_id>` | поднять версию расширения |
| `forge.py list` | список расширений на попечении |
| `forge.py watch` | прогнать сторож по живым источникам |
| `forge.py doctor <kind> <site_id>` | чек-лист перед пушем (иконка, version, tsc) |

## 📋 Формат декларации

```yaml
id: my_site              # имя каталога модуля / plugin id
kind: mihon              # mihon | aniyomi | lnreader
name: My Site
base_url: https://my-site.ru
theme: multichan         # multisrc-тема (для mihon), пусто = голый HttpSource
nsfw: false

qualities:               # качества страниц/видео
  - id: "720"
    label: "720p"
  - id: "480"
    label: "480p"
default_quality: "720"   # выбор по умолчанию — попадёт в настройки расширения

selectors:               # по ним сторож проверяет живость
  popular: ".pop-list .item"
  details:
    title: "h1.title"
  chapters: ".chapters a"
```

## ✨ Что получает сгенерированное расширение

- 🎨 **Иконки в едином стиле** — генерируются проверенным `make-icons.py`
  целевого репозитория (все плотности mipmap)
- ⚙️ **Настройки качества** — экран настроек расширения в приложении,
  с выбором качества по умолчанию
- 🔢 **Автоверсионирование** — `versionCode`/`extVersionCode`/`version`
  инкрементируются при каждом обновлении (`forge bump`, хил сторожа)
- 👁 **Сторож** — раз в месяц CI проверяет сайт и селекторы

## 🛡 Сторож и самовосстановление

Раз в месяц (`.github/workflows/monthly-watch.yml`):

1. Для каждого расширения с `watch: true` проверяется живость сайта
2. Проверяются селекторы из декларации — не протухли ли
3. Протухшие селекторы переобнаруживаются адаптивным парсером
   ([Scrapling](https://github.com/D4Vinci/Scrapling) — diff-based
   matchmaking находит элементы, похожие на старые, в новой вёрстке)
4. Обновлённая декларация коммитится, версия расширения поднимается —
   CI целевого репозитория пересобирает и переопубликовывает APK/плагин

Итог: расширение чинится само, без ручного вмешательства, в типовых случаях.


## 🧠 База знаний

Опыт ручной разработки плагинов превращён в код и в документацию — чтобы не наступать
на те же грабли при каждом новом сайте.

| Файл | Что внутри |
|---|---|
| `knowledge/lnreader.md` | Litnet (вёрстка зависит от User-Agent, фиктивный рейтинг в `ld+json`), Libcat (страницы вместо глав, нет сортировки по дате), публикация в `plugins/v3.0.0`, иконки, чек-лист |
| `sites/litnet.yaml`, `sites/libcat.yaml` | готовые декларации с проверенными селекторами и флагами |
| `forge_knowledge.py` | шаблон `static_html` + предупреждения + `forge.py doctor` |

Главные правила, зашитые в генератор:

1. **UA решает вёрстку.** Litnet отдаёт Angular-страницу без глав любому браузерному UA;
   классическая разметка — только с `Litnet/3.5.1 (Android …)`. Поле `html.ua`.
2. **Рейтинг — только настоящий.** `ld+json` Litnet ставит `ratingValue: 5` всем книгам:
   такой рейтинг не выводим (`detail.rating: false`), у Libcat берём дробный из текста.
3. **«Последние» ≠ «Популярное».** Нужен `list_latest_url` или `latest_sort: id`, иначе
   `showLatestNovels` игнорируется и вкладки совпадают.
4. **Сайт без глав.** `chapter_mode: pages_range` + `pages_per_chapter` — страницы
   группируются в главы, диапазон кодируется в path (`book::1-5`).
5. **SEO-вода ≠ описание.** `summary_reject` отбрасывает «Здесь есть возможность читать онлайн…».
6. **Bump версии обязателен.** Без него LNReader не увидит обновление; индекс плагинов —
   `…/plugins/v3.0.0/.dist/plugins.min.json` (корневой путь отдаёт 404), CDN кэширует 5 минут.

### Команда doctor

```bash
forge.py doctor lnreader litnet
```

Проверяет: файл плагина, `version`, `icon`, отсутствие `TODO`, иконку в
`public/static/src/ru/<id>/`, запись в `scripts/make-icons.py`, `tsc` и напоминание
про bump. Код возврата — число проблем.

### Генерация по декларации

```yaml
api_style: html          # включает шаблон static_html (без TODO)
html:
  ua: "Litnet/3.5.1 (Android 13; Xiaomi)"
  list_url: "/ru/top/{genre}?page={page}"
  list_latest_url: "/ru/top/latest-new?page={page}"
  ...
```

Целевой репозиторий можно переопределить переменной окружения
`FORGE_REPO_LNREADER` (удобно для тестовой генерации в пустой каталог).

## 🗂 Структура

```
extension-forge/
├── forge.py               # CLI: generate / bump / list / watch
├── registry.json          # какие расширения на попечении (создаётся автоматически)
├── sites/                 # YAML-декларации сайтов
│   ├── example.yaml
│   ├── litnet.yaml        # LNReader · проверено на живом сайте
│   └── libcat.yaml
├── knowledge/             # накопленные знания (см. раздел «База знаний»)
├── forge_knowledge.py     # шаблон static_html, предупреждения, doctor
├── templates/             # шаблоны кода для mihon / aniyomi / lnreader
├── watchdog/
│   └── watchdog.py        # ежемесячная проверка + самопочинка
└── .github/workflows/
    └── monthly-watch.yml  # сторож по расписанию
```

## 📜 Лицензия

MIT
