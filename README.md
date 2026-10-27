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
