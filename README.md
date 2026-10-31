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
| `forge.py audit` | прогнать стенд: проверить расширения в живом приложении |
| `forge.py pipe <url\|файл>` | конвейер: сайт → расширение → сборка → стенд → правки |
| `forge.py classify <url>` | только тип сайта и NSFW |

## 🧪 Стенд

Генератор и стенд живут вместе: собрали модуль — сразу проверили его
в живом приложении.

```bash
python3 forge.py audit --from-index --lang any --batch 1 --only domanime
python3 forge.py audit --from-index --only domanime --title-url /43-pervyj-shag-2000.html
```

Стенд ставит APK из `index.min.json` (как это делает приложение), гоняет
каталог, детали, постеры, серии, порядок, фильтры, поиск и видео
(через `getHosterList` → `getVideoList`). Подробности и требования к
хосту — в [`stand/README.md`](stand/README.md).

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

## 🏭 Конвейер: сайт → готовое расширение

```bash
python3 forge.py pipe sites.txt          # список ссылок, по одной в строке
python3 forge.py pipe https://site.ru    # одна ссылка
python3 forge.py pipe sites.txt --classify-only   # только тип сайта и NSFW
python3 forge.py pipe --repair domanime           # починить существующее
```

Что происходит с каждой ссылкой:

| Стадия | Что делает |
|---|---|
| `classify` | тип сайта (аниме / манга / ранобэ) и NSFW — правилами, модель только при ничьей |
| `generate` | разведка, декларация, модуль, иконка в общем стиле |
| `build` | `git push` → GitHub Actions → при падении разбор логов и автопочинка |
| `stand` | ставит APK из индекса и проходит каталог, детали, серии, поиск, видео |
| `feedback` | что именно сломалось → правки → версия выше → снова сборка и стенд |
| `learn` | ошибка и её лечение остаются в `knowledge/lessons.json` |

Скелет (модуль с `TODO`) в репозиторий не попадает: конвейер откатывает
такой модуль и помечает сайт как «генерация не удалась», а не выдаёт
нерабочее расширение за готовое.

## 🤖 Gemini: минимум и по делу

Правила решают всё, что могут. Модель зовётся только там, где
детерминированный код честно не справляется:

| Вызов | Когда |
|---|---|
| классификация | счёт правил близок (тип сайта неочевиден) |
| починка сборки | ошибка Kotlin не из известных шаблонов |
| починка по стенду | шаг упал, готового рецепта нет |

Экономия: ответ кешируется по хешу запроса (`knowledge/ai_cache/`),
лимит вызовов на прогон (`GEMINI_MAX_CALLS`, по умолчанию 40),
перед моделью всегда спрашиваем `knowledge/lessons.json` — повторная
ошибка решается бесплатно.

Ключ: `GEMINI_API_KEY` или файл `~/.gemini_key` (он в `.gitignore`).
Без ключа конвейер работает на правилах и не падает.

Проверить: `python3 gemini.py`.

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

## 📚 База знаний

Весь опыт разработки AnimeStock (Craftum-сайты, Supabase/PostgREST, стиль Kotlin под CI,
маппинг длинных полос и альбомов) — в [KNOWLEDGE.md](KNOWLEDGE.md).

## 🗂 Структура

```
extension-forge/
├── forge.py               # CLI: generate / bump / list / watch
├── registry.json          # какие расширения на попечении (создаётся автоматически)
├── sites/                 # YAML-декларации сайтов
│   └── example.yaml
├── templates/             # шаблоны кода для mihon / aniyomi / lnreader
├── watchdog/
│   └── watchdog.py        # ежемесячная проверка + самопочинка
└── .github/workflows/
    └── monthly-watch.yml  # сторож по расписанию
```

## 📜 Лицензия

MIT

---

## 🤖 Как я пишу код

<p align="center">
  <img src=".github/assets/ai-pair-coding.jpg" alt="AI пишет код, я проверяю" width="100%">
</p>

Не секрет: код в этих репозиториях я делаю не вручную. Основную работу — разбор
сайтов, генерацию расширений, починку селекторов и сборку — выполняет AI,
а я ставлю задачи, проверяю результат и отвечаю за то, что уходит в релиз.

- AI пишет код и готовит APK, GitHub Actions собирает и подписывает
- я проверяю источники, тестирую расширения на устройстве и решаю, что публиковать
- ошибки, регрессии и спорные решения — на мне

Так проект живёт быстрее: больше источников, чаще обновления, меньше рутины.

## ❤️ Поддержать проект

<p align="center">
  <img src=".github/assets/donate-banner.jpg" alt="Поддержать проект" width="100%">
</p>

Расширения бесплатные и без рекламы. Если они экономят твоё время —
можно отправить донат через Tribute:

<p align="center">
  <a href="https://web.tribute.tg/d/T6v"><img src="https://img.shields.io/badge/%D0%9E%D1%82%D0%BA%D1%80%D1%8B%D1%82%D1%8C%20%D0%B2%20%D0%B1%D1%80%D0%B0%D1%83%D0%B7%D0%B5%D1%80%D0%B5-Tribute-ff4d6d?style=for-the-badge&logoColor=white" alt="Поддержать в браузере"></a>
  <a href="https://t.me/tribute/app?startapp=dT6v"><img src="https://img.shields.io/badge/Telegram-Tribute-2CA5E0?style=for-the-badge&logo=telegram&logoColor=white" alt="Поддержать в Telegram"></a>
</p>

<p align="center"><sub>Спасибо, что пользуешься и поддерживаешь ❤️</sub></p>
