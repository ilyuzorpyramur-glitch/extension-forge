# Стенд: как собрать заново

Всё, что нужно, чтобы поднять redroid + прокси + Frida на чистой машине.
Секретов здесь нет: ключи и пароли VPN/бота лежат вне репозитория.

## 1. Эмулятор

```sh
docker run -d --name redroid --privileged \
  -p 5555:5555 \
  -v /home/work/redroid-data:/data \
  redroid/redroid:14.0.0_64only-latest \
  androidboot.redroid_gpu_mode=guest
adb connect 172.17.0.2:5555
```

`infra/redroid-net.sh` возвращает то, что теряется при рестарте: правила
`ip rule` для 172.17.0.0/16, default route и `/etc/resolv.conf` внутри контейнера.
Ставится как `redroid-net.service` (After=docker.service).

## 2. Прокси для заблокированных сайтов

`sing-box-split.service` поднимает split-routing: RU-сайты напрямую, остальные
через VPN. Конфиг — `/etc/sing-box/config.json`, в репозиторий не входит.

HTTP-инбаунд для стенда слушает `172.17.0.1:1083` и `127.0.0.1:1083`.
Устройство ходит туда через `adb reverse`:

```sh
adb reverse tcp:1083 tcp:1083
adb shell settings put global http_proxy 127.0.0.1:1083
# после проверки обязательно:
adb shell settings put global http_proxy :0
adb reverse --remove-all
```

## 3. Frida

`frida-server` 16.7.19 под архитектуру контейнера, запуск от root:

```sh
adb push frida-server /data/local/tmp/ && adb shell "su 0 chmod 755 /data/local/tmp/frida-server"
adb shell "su 0 /data/local/tmp/frida-server &"
```

Стенд грузит APK расширения через `DexClassLoader`, поэтому ставить пакет не нужно.

## 4. Проверка

```sh
cd extension-forge && python3 forge.py audit --only amedia --batch 1
```

Батч больше 2 модулей не ставить: пять модулей в одном процессе Frida дают
ложное «не уложился в лимит».
