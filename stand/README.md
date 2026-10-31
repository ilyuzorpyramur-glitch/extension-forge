# Стенд

Проверка расширений в живом приложении: генератор собрал модуль — стенд
поднимает его в Tadami на redroid и проходит все шаги, которые делает
пользователь.

## Что проверяется

| Шаг | Что значит |
|---|---|
| `catalog` | каталог отдаёт тайтлы, есть следующая страница |
| `scroll` | вторая и третья страницы не пустые |
| `dup` | нет дублей по url и названию |
| `details` | детали открываются: название, жанры, описание, постер |
| `status` | статус не «неизвестно», если сайт его отдаёт |
| `thumb` / `thumbs` | постеры грузятся (проверяется код ответа и тип) |
| `episodes` | список серий не пустой |
| `order` | порядок серий (у этих сайтов — по убыванию) |
| `epurls` | у каждой серии есть url |
| `filters` | фильтры объявлены и применяются |
| `search` | поиск находит то, что искали |
| `video` | плеер отдаёт ссылки: `getHosterList` → `getVideoList` |

## Запуск

```bash
# из корня extension-forge
python3 forge.py audit --from-index --lang any --batch 1 --only domanime
python3 forge.py audit --from-index --only domanime --title-url /43-pervyj-shag-2000.html
```

Или напрямую:

```bash
python3 stand/audit.py --from-index --batch 4 --out /tmp/audit.json
```

## Что нужно на хосте

- `adb` и запущенный redroid с установленным Tadami;
- `frida-server` в устройстве: `/data/local/tmp/frida-server &`;
- python-пакет `frida` (если он не в системном пути — `FRIDA_PYTHON_PATH`).

Переменные окружения:

| Переменная | По умолчанию | Зачем |
|---|---|---|
| `STAND_DEVICE` | `172.17.0.2:5555` | адрес adb-устройства |
| `STAND_REPO` | `~/repos/aniyomi-ru-anime-repo` | репозиторий расширений, который проверяем |
| `FRIDA_PYTHON_PATH` | авто (`/home/work/.local/...`) | где лежит пакет `frida` |

## Как это связано с генератором

`forge.py audit` — тонкая обёртка: аргументы уходят в `stand/audit.py`.
Скрипт берёт `index.min.json` **из локального репозитория** (после `git pull`),
ставит APK как это делает приложение и гоняет `stand_batch.js` через Frida.

Приложение грузит расширения через `DexClassLoader`, поэтому версия в
`dumpsys` не важна — проверяется именно скачанный APK из индекса.
