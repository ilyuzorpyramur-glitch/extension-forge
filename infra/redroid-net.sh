#!/bin/sh
# Сеть для redroid: правила, которые теряются при перезагрузке.
# 100/101 — вывести трафик контейнера из-под перехвата sing-box tun,
# иначе MASQUERADE и обратный путь ломаются.
ip rule add from 172.17.0.0/16 lookup main priority 100 2>/dev/null
ip rule add to 172.17.0.0/16 lookup main priority 101 2>/dev/null
# В контейнере нет ни default route, ни resolv.conf после старта.
docker exec redroid sh -c "ip route replace default via 172.17.0.1" 2>/dev/null
docker exec redroid sh -c "printf \"nameserver 77.88.8.8\nnameserver 1.1.1.1\n\" > /etc/resolv.conf" 2>/dev/null
