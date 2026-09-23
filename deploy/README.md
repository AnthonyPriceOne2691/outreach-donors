# Выкатка

Что здесь лежит:

| Файл | Что это |
|---|---|
| `nginx.conf` | внутренний nginx контейнера `web`: отдаёт собранный фронт и проксирует `/api` |
| `proxy/outreach.conf` | обратный прокси **хоста**: два поддомена и один сертификат на оба проекта |
| `backup.cron` | еженедельный бэкап базы |

Боевые отличия компоуза — в `docker-compose.prod.yml`: лимиты памяти
и процессора, потолок журналов, фронт только на петле.

## Подготовка машины (один раз)

```bash
# Docker с плагином compose, nginx, certbot, htpasswd
sudo apt install -y docker.io docker-compose-v2 nginx certbot python3-certbot-nginx apache2-utils
# Фаервол: наружу только ssh, 80 и 443
sudo ufw allow OpenSSH && sudo ufw allow 'Nginx Full' && sudo ufw enable
# Каталог бэкапов
sudo mkdir -p /srv/backups/outreach
```

DNS: A-запись `outreach.ДОМЕН` на адрес машины — до шага 5, иначе
certbot не выпустит сертификат.

## Порядок первой выкатки

```bash
# 1. Код и настройки
git clone https://github.com/AnthonyPriceOne2691/outreach-donors /srv/outreach-donors
cd /srv/outreach-donors
cp .env.example .env && $EDITOR .env
#    обязательно: POSTGRES_PASSWORD (openssl rand -hex 24),
#    ACCESS_JWT_SECRET (openssl rand -hex 32), BACKEND_IMAGE и WEB_IMAGE
#    (раскомментировать — иначе компоуз соберёт образ на машине),
#    ключи Ahrefs, выдачи и модели. Почту — не сейчас, см. ниже.

# 2. Образы из реестра, а не сборка на машине
docker compose -f docker-compose.yml -f docker-compose.prod.yml pull

# 3. Подъём: миграции идут одноразовым контейнером до остальных
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d

# 4. Первый админ
docker compose exec api outreach user-add --email ivan@site.com --role admin

# 5. Прокси хоста, пароль на оболочку и сертификат
sudo htpasswd -c /etc/nginx/outreach.htpasswd команда
sudo cp deploy/proxy/outreach.conf /etc/nginx/sites-available/outreach
sudo sed -i 's/outreach.ПРИМЕР.ru/outreach.ДОМЕН/' /etc/nginx/sites-available/outreach
sudo ln -s /etc/nginx/sites-available/outreach /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d outreach.ДОМЕН

# 6. Бэкапы
sudo cp deploy/backup.cron /etc/cron.d/outreach-backup
```

**Образы задаются в `.env`, а не `export`.** Раньше инструкция ставила
их командой в шаге 2 — в новом шелле при обновлении их уже не было,
и `up -d` начинал сборку на машине.

**Периметр — два слоя.** Оболочка приложения и схема API закрыты
паролем прокси, сам API — входом сервиса; вход ограничен по частоте
и на прокси, и в сервере. Пароля прокси на `/api/` нет намеренно: фронт
шлёт пропуск заголовком `Authorization`, и basic auth на том же заголовке
отказал бы каждому запросу. Вебхуки почты и страница отписки поэтому
открыты без него — у них своя защита (`docs/SECURITY.md`).

**Проверка после подъёма:**

```bash
curl -s https://outreach.ДОМЕН/api/health                  # {"status": "жив"}
curl -so /dev/null -w '%{http_code}\n' https://outreach.ДОМЕН/            # 401 — пароль прокси
curl -so /dev/null -w '%{http_code}\n' https://outreach.ДОМЕН/api/docs    # 401
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps        # все healthy / running
```

## Подключение почты (на боевой машине, когда готовы домены)

До этого сервис работает целиком: прогоны, отбор, контакты, очередь
писем. Экран писем говорит, чего не хватает для отправки, и ничего
не уходит наружу. Код для подключения не меняется — только `.env`
и кабинет платформы.

1. **Домены.** У каждого почтового домена — SPF, DKIM, DMARC; в SendGrid —
   Domain Authentication. Письмо с неподписанного домена уходит в спам.
2. **`.env`:**
   - `OUTREACH_SENDGRID_API_KEY` — ключ с правом Mail Send;
   - `OUTREACH_SENDER_NAME`, `OUTREACH_POSTAL_ADDRESS` — имя и адрес в письме;
   - `OUTREACH_UNSUBSCRIBE_URL=https://outreach.ДОМЕН/api/unsubscribe`;
   - `OUTREACH_REPLY_DOMAIN` — поддомен для ответов (его MX — на SendGrid);
   - `OUTREACH_INBOUND_SECRET` — `openssl rand -hex 32`;
   - `OUTREACH_EVENTS_PUBLIC_KEY` — из кабинета, после включения подписи событий;
   - `OUTREACH_ALLOWED_RECIPIENTS` — **на первые дни свои ящики**, потом очистить;
   - последним — `OUTREACH_TRANSPORT=sendgrid`.
3. **Кабинет SendGrid:**
   - Inbound Parse на `OUTREACH_REPLY_DOMAIN`, адрес
     `https://inbound:СЕКРЕТ@outreach.ДОМЕН/api/inbound/replies` — секрет
     паролем в адресе: своих заголовков платформа не шлёт;
   - Event Webhook на `https://outreach.ДОМЕН/api/events/delivery`,
     с подписью (Signed Event Webhook).
4. **Домены отправки** — на экране «Домены рассылки»: там же разгон.
5. `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d` —
   контейнеры пересоздаются с новым `.env` (настройки читаются при старте).
6. Первое письмо — себе (предохранитель из п. 2), проверить заголовки,
   ответ и отписку; потом снять предохранитель.

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
- **Выкатки как таковой.** 24.09.2026 решение пересмотрено: катим без
  почты, почту подключаем на боевой машине (раздел выше).
