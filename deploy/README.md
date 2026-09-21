# Выкатка

Что здесь лежит:

| Файл | Что это |
|---|---|
| `nginx.conf` | внутренний nginx контейнера `web`: отдаёт собранный фронт и проксирует `/api` |
| `proxy/outreach.conf` | обратный прокси **хоста**: два поддомена и один сертификат на оба проекта |
| `backup.cron` | еженедельный бэкап базы |

Боевые отличия компоуза — в `docker-compose.prod.yml`: лимиты памяти
и процессора, потолок журналов, фронт только на петле.

## Порядок первой выкатки

Ничего из этого ещё не делалось: **сервис не развёрнут ни разу.** Ниже
порядок, а не отчёт.

```bash
# 1. Код и настройки
git clone https://github.com/AnthonyPriceOne2691/outreach-donors /srv/outreach-donors
cd /srv/outreach-donors
cp .env.example .env && $EDITOR .env      # пароль базы, секрет пропусков, ключи

# 2. Образы из реестра, а не сборка на машине
export BACKEND_IMAGE=ghcr.io/anthonypriceone2691/outreach-donors-backend:main
export WEB_IMAGE=ghcr.io/anthonypriceone2691/outreach-donors-web:main
docker compose -f docker-compose.yml -f docker-compose.prod.yml pull

# 3. Подъём: миграции идут одноразовым контейнером до остальных
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d

# 4. Первый админ
docker compose exec api outreach user-add --email ivan@site.com --role admin

# 5. Прокси хоста и сертификат
sudo cp deploy/proxy/outreach.conf /etc/nginx/sites-available/outreach
sudo ln -s /etc/nginx/sites-available/outreach /etc/nginx/sites-enabled/
sudo certbot --nginx -d outreach.ПРИМЕР.ru -d cases.ПРИМЕР.ru
sudo nginx -t && sudo systemctl reload nginx

# 6. Бэкапы
sudo cp deploy/backup.cron /etc/cron.d/outreach-backup
```

**Сборка на сервере не нужна и нежелательна.** Сборка фронта берёт
2–3 ГБ памяти, а рядом работает соседняя система: `docker compose build`
на этой машине — это способ однажды её уронить. Образы собирает и
публикует CI при слиянии в `main`.

**Что нужно от соседней системы:** перестать публиковать 8080 наружу.
Оба проекта слушают петлю, наружу смотрит только прокси хоста.

## Обновление

```bash
cd /srv/outreach-donors && git pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

`git pull` нужен не ради кода — код приезжает образом, — а ради самих
файлов компоуза и скриптов. Метка `main` двигается с каждым слиянием;
чтобы прибить версию, задайте `BACKEND_IMAGE` и `WEB_IMAGE` с меткой
по SHA коммита.

## Бэкапы и восстановление

```bash
scripts/backup.sh                                   # снять копию
BACKUP_UPLOAD='rclone copy {} storage:outreach/' scripts/backup.sh
scripts/restore.sh backups/2026-09-21-2030          # покажет, что сделает
scripts/restore.sh backups/2026-09-21-2030 --yes    # выполнит
```

**Внешнее хранилище ещё не выбрано** (Storage Box, S3 или диск
агентства), поэтому выгрузка задаётся командой в `BACKUP_UPLOAD`,
а не зашита в скрипт. Пока её нет, копия лежит на том же диске,
что и база, — скрипт говорит об этом вслух при каждом запуске.

Требование по срокам: еженедельно, хранить 90 дней. Глубину держит
хранилище; на машине остаются три последние копии для быстрого отката.

**Восстановление проверено** 21.09.2026 на машине разработки: снятая
копия развёрнута в пустую базу, схема и содержимое совпали. Числа —
в `delivery/archive/`. На боевой машине это ещё предстоит: там другой
объём и другой диск.

## Чего здесь ещё нет

- **Замера `docker stats` под нагрузкой.** Лимиты в `docker-compose.prod.yml`
  — оценка от объёмов, а не измерение. Их придётся пересчитать после
  первого настоящего прогона на машине; до этого 32 ГБ — расчёт, а не факт.
- **Выкатки как таковой.** Решение Anthony 21.09.2026: катим, когда
  будут двадцать почтовых доменов и учётка SendGrid — раньше сервису
  нечего делать на боевой машине.
