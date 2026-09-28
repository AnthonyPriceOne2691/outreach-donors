# Выкатка

Что здесь лежит:

| Файл | Что это |
|---|---|
| `nginx.conf` | внутренний nginx контейнера `web`: отдаёт собранный фронт и проксирует `/api` |
| `proxy/outreach.conf` | обратный прокси **хоста**: два поддомена и один сертификат на оба проекта |
| `backup.cron` | еженедельный бэкап базы с копией в хранилище вне машины |
| `healthwatch.cron` | сторож здоровья раз в пять минут: контейнеры и свежесть бэкапа, тревоги в Telegram |

Боевые отличия компоуза — в `docker-compose.prod.yml`: лимиты памяти
и процессора, потолок журналов, фронт только на петле.

## Подготовка машины (один раз)

```bash
# Docker с плагином compose, nginx, certbot, htpasswd
sudo apt install -y docker.io docker-compose-v2 nginx certbot python3-certbot-nginx apache2-utils
# Фаервол: наружу только ssh, 80 и 443
sudo ufw allow OpenSSH && sudo ufw allow 'Nginx Full' && sudo ufw enable
# Каталог бэкапов
sudo mkdir -p /srv/backups/outreach-donors
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

# 6. Кроны хоста: бэкап и сторож здоровья. Хранилище и бот — раздел
#    «Копия вне машины и тревоги»; без них оба работают и говорят об этом вслух.
sudo cp deploy/backup.cron /etc/cron.d/outreach-donors-backup
sudo cp deploy/healthwatch.cron /etc/cron.d/outreach-donors-healthwatch
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
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps        # все (healthy), кроме migrate
```

**Здоровье фоновых процессов.** У каждого долгоживущего контейнера своя
проверка: воркер — по своей отметке в очереди, циклы добивок и разбора —
по отметкам о жизни на каждом круге, web — по ответу nginx. `unhealthy`
значит «процесс жив, но работу не делает» (завис или падает на каждом
проходе); причину называет `docker inspect --format '{{json .State.Health}}' <контейнер>`.
⚠ Докер `unhealthy` сам НЕ перезапускает — `docker compose restart <сервис>`
делает человек, посмотрев причину. Узнаёт он об этом из тревоги: сторож
(`scripts/healthwatch.sh`, крон раз в пять минут) пишет в Telegram, когда
сервис сломался и когда восстановился, — по переходу, а не каждые пять минут.

## Подключение почты (на боевой машине, когда готовы домены)

До этого сервис работает целиком: прогоны, отбор, контакты, очередь
писем. Экран писем говорит, чего не хватает для отправки, и ничего
не уходит наружу. Код для подключения не меняется — только DNS, `.env`
и кабинет платформы.

**Каждый домен отправки подключается сам по себе.** Письмо с ящика
`anna@mail-a.example` подписано `mail-a.example`, несёт `Message-ID`
на `mail-a.example` и просит отвечать на `anna+метка@replies.mail-a.example`.
Общего домена для ответов нет намеренно: он связал бы все домены между
собой — упала его репутация, упали все (`docs/OUTREACH_THREADS.md`).
Поэтому шаги 1, 3, 4 и 6 повторяются для каждого домена.

**Inbound Parse есть только на тарифе Pro.** Всё остальное — DNS,
Domain Authentication, вебхук событий, пробные письма себе (шаг 6) —
делается на любом тарифе, в том числе на пробном, и делать это стоит
заранее. Письма донорам — только после Pro: без Inbound Parse ответы
принимать нечем, и ответ донора пропадёт.

1. **DNS каждого домена отправки** (пример — `mail-a.example`):
   - три CNAME из Domain Authentication в кабинете SendGrid (с включённой
     automated security) — подпись DKIM и SPF ведёт платформа;
   - TXT `_dmarc.mail-a.example` со значением `v=DMARC1; p=none;` — на
     старт: сначала смотрим, что проходит, ужесточаем потом;
   - MX `replies.mail-a.example` → `mx.sendgrid.net`, приоритет 10 — сюда
     приходят ответы доноров.
2. **`.env`:**
   - `OUTREACH_SENDGRID_API_KEY` — ключ с правом Mail Send;
   - `OUTREACH_SENDER_NAME` — имя в подписи письма;
   - `OUTREACH_UNSUBSCRIBE_URL=https://outreach.ДОМЕН/api/unsubscribe`;
   - `OUTREACH_INBOUND_SECRET` — `openssl rand -hex 32`;
   - `OUTREACH_REPLY_SUBDOMAIN` — приставка поддомена ответов, по умолчанию
     `replies`; менять, только если MX из шага 1 заведён под другим именем;
   - `OUTREACH_EVENTS_PUBLIC_KEY` — из кабинета, после включения подписи событий;
   - `OUTREACH_ALLOWED_RECIPIENTS` — **свои ящики**, до конца проверки (шаг 6);
   - последним — `OUTREACH_TRANSPORT=sendgrid`.
3. **Кабинет SendGrid:**
   - Inbound Parse — на каждый `replies.<домен>`, адрес
     `https://inbound:СЕКРЕТ@outreach.ДОМЕН/api/inbound/replies` (секрет
     паролем в адресе: своих заголовков платформа не шлёт). Режим
     разобранный: галку «POST the raw, full MIME message» не ставить;
   - Event Webhook на `https://outreach.ДОМЕН/api/events/delivery`,
     с подписью (Signed Event Webhook);
   - Tracking → Subscription Tracking **выключен**: иначе платформа
     допишет в текст письма свою отписку;
   - Click Tracking и Open Tracking выключены. Письмо и так выключает их
     в каждом запросе, но включённые в кабинете — это ловушка для любого
     письма, собранного мимо сервиса.
4. **Ящики** — командой `outreach sender-add --email …` на каждый домен,
   когда его DNS уже настроен: разгон идёт с минуты заведения. Видны
   и включаются на экране «Домены рассылки».
5. `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d` —
   контейнеры пересоздаются с новым `.env` (настройки читаются при старте).
6. **Пробное письмо себе — с каждого домена, до снятия предохранителя:**

   ```bash
   docker compose exec api outreach mail-test --to свой@ящик --sender anna@mail-a.example
   ```

   Письмо то же, что уйдёт донору, только на свой ящик; в базу команда
   не пишет ничего и донора не тратит. Она печатает номер письма
   у платформы, наш `Message-ID` и что проверить в заголовках
   полученного письма: DKIM=pass с `d=mail-a.example`, SPF=pass,
   DMARC=pass, `Message-ID` ровно напечатанный (подменён платформой —
   до первого донора не слать), Reply-To на `replies.mail-a.example`,
   List-Unsubscribe. Потом ответить на письмо: ответ встанет на экране
   «Диалоги», вкладка «Не привязаны», с причиной «Ответ на пробное письмо» —
   так проверяются MX и Inbound Parse.
7. Всё сошлось на всех доменах — очистить `OUTREACH_ALLOWED_RECIPIENTS`
   и повторить шаг 5.

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
scripts/backup.sh                                   # снять копию и выгрузить её
scripts/offsite.sh list                             # какие копии лежат в хранилище
scripts/restore.sh backups/2026-09-21-2030          # покажет, что сделает
scripts/restore.sh backups/2026-09-21-2030 --yes    # выполнит
```

Раз в неделю (`backup.cron`, ночь воскресенья) `scripts/backup.sh` снимает
дамп, кладёт его в `/srv/backups/outreach-donors` (там живут три последние
копии — на быстрый откат) и выгружает в S3-совместимое хранилище вне машины
— Cloudflare R2 или Backblaze B2 — через `scripts/offsite.sh`: rclone
в контейнере закреплённой версии, ставить на машину ничего не нужно.
**«Выгружено» — это проверено:** после копирования rclone заново читает
хранилище и сверяет размер и MD5 каждого файла; не сошлось — бэкап упал.

**Любой отказ — тревога в Telegram:** компоуз не ответил, дамп не снялся или
оказался не архивом pg_dump, хранилище не пустило, копия легла не целиком.
Хранилище не настроено — копия остаётся на машине, скрипт говорит об этом
вслух и тревогой. Бэкап, который не запустился вовсе (крон не стоит, машина
спала), о себе сказать не может — его ловит сторож здоровья: свежей копии
нет дольше восьми дней.

Требование по срокам: еженедельно, хранить 90 дней. Глубину держит
хранилище: старые копии удаляет **правило жизненного цикла бакета**, а не
скрипт. Ключу на сервере тогда не нужно право удалять, и тот, кто получил
машину, не сотрёт вместе с ней и копии.

Ручной запуск перед выкаткой — тот же скрипт и тот же путь в хранилище:
`BACKUP_DIR=/srv/backups/outreach-donors KEEP=10 scripts/backup.sh`.
Выгрузку можно выключить на один запуск: `BACKUP_UPLOAD= scripts/backup.sh`;
своя команда в `BACKUP_UPLOAD` (Storage Box, rsync; `{}` — каталог копии)
заменяет хранилище целиком.

### Копия вне машины и тревоги: шаги владельцу

Где что лежит на сервере — в двух файлах рядом с компоузом, оба `chmod 600`.
Кроны читают их сами (`scripts/ops_env.sh`), поэтому ночной бэкап и ручной
запуск перед выкаткой выгружают одинаково.

| Файл | Что | Кто читает |
|---|---|---|
| `/srv/outreach-donors/.env` | `ALERT_TELEGRAM_BOT_TOKEN`, `ALERT_TELEGRAM_CHAT_ID` | контейнеры (остановленный прогон) и кроны хоста |
| `/srv/outreach-donors/.env.ops` | `BACKUP_S3_*` — хранилище | только кроны хоста |

Ключи хранилища — не в `.env`: компоуз отдаёт `.env` в контейнеры целиком,
и ключ от копий оказался бы в сервере, смотрящем в интернет.

1. **Хранилище — одно из двух, оба с бесплатным уровнем в 10 ГБ.**
   За 90 дней копятся тринадцать недельных дампов плюс ручные перед
   выкатками; дамп базы разработки — полмегабайта, боевой — `du -sh
   /srv/backups/outreach-donors/*`.
   - **Cloudflare R2.** R2 → Create bucket (например, `outreach-backups`).
     R2 → API → Manage API tokens → Create API token: права **Object Read & Write**,
     только этот бакет. Кабинет покажет Access Key ID, Secret Access Key
     (один раз) и адрес `https://<ACCOUNT_ID>.r2.cloudflarestorage.com`.
     Бакет → Settings → Object lifecycle rules → Add rule: префикс
     `outreach-donors/`, удалять загруженное через **90 дней**. Cloudflare
     просит платёжные данные даже для бесплатного уровня.
   - **Backblaze B2.** Buckets → Create a Bucket: приватный, шифрование
     включить. Адрес — на карточке бакета (`s3.<регион>.backblazeb2.com`).
     Application Keys → Add a New Application Key: только этот бакет,
     Read and Write — кабинет покажет keyID и applicationKey (один раз).
     Бакет → Lifecycle Settings → свои правила: префикс `outreach-donors/`,
     скрывать через **90** дней, удалять скрытое через **1** день.
     Ключ без права удалять (`deleteFiles`) выпускается только из их
     консольной утилиты — по желанию: скрипту это право не нужно.
2. **Бот.** В Telegram — @BotFather → `/newbot` → имя → он пришлёт токен
   вида `123456789:AA…`. Написать новому боту что угодно (или добавить его
   в группу и написать там `/start@имя_бота`), затем с сервера:
   `curl -s "https://api.telegram.org/bot<ТОКЕН>/getUpdates"` — номер чата
   в `"chat":{"id":…}` (у группы он отрицательный).
3. **Значения.** Бот — две строки в `/srv/outreach-donors/.env`:
   ```bash
   ALERT_TELEGRAM_BOT_TOKEN=123456789:AA…
   ALERT_TELEGRAM_CHAT_ID=123456789
   ```
   Хранилище — отдельный файл:
   ```bash
   sudo install -m 600 /dev/null /srv/outreach-donors/.env.ops
   sudo $EDITOR /srv/outreach-donors/.env.ops
   # BACKUP_S3_ENDPOINT=https://<ACCOUNT_ID>.r2.cloudflarestorage.com
   # BACKUP_S3_BUCKET=outreach-backups
   # BACKUP_S3_ACCESS_KEY_ID=…
   # BACKUP_S3_SECRET=…
   ```
   Затем `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d`:
   `.env` доезжает до контейнеров только пересозданием, `restart` его не видит.
4. **Кроны** — шаг 6 первой выкатки: `backup.cron` и `healthwatch.cron`
   в `/etc/cron.d/` (имена без точки: cron молча пропускает файлы с точкой).
5. **Проверка — каждое звено своим способом:**
   ```bash
   cd /srv/outreach-donors
   scripts/alert.sh "проверка с хоста"                        # сообщение в чате
   docker compose exec api python -m backend.shared.alerts "проверка из контейнера"
   BACKUP_DIR=/srv/backups/outreach-donors KEEP=10 scripts/backup.sh
   #   → «Проверено в хранилище: … каждый файл того же размера и хеша»
   scripts/offsite.sh list                                     # копия видна в хранилище
   BACKUP_DIR=/srv/backups/outreach-donors scripts/healthwatch.sh && cat .healthwatch.state
   #   → тишина и пустой файл: всё здорово
   ```
6. **`.env` и `.env.ops` в бэкап не входят** — их копия у владельца
   (менеджер паролей). Без `OUTREACH_INBOUND_SECRET` не откроется ни одна
   ссылка отписки из уже отправленных писем, без ключей хранилища не
   достать и сами копии.

### Восстановление из хранилища

Восстановление затирает базу: сначала — копия того, что есть сейчас.

```bash
cd /srv/outreach-donors
BACKUP_DIR=/srv/backups/outreach-donors KEEP=10 scripts/backup.sh   # на случай «вернуть как было»
scripts/offsite.sh list                    # размер дампа, время выгрузки, имя копии
scripts/offsite.sh fetch 2026-09-27-030000 /srv/backups/outreach-donors-restore
#   скачает и сверит с хранилищем; не сошлось — копию не предлагает
scripts/restore.sh /srv/backups/outreach-donors-restore/2026-09-27-030000         # покажет, что сделает
scripts/restore.sh /srv/backups/outreach-donors-restore/2026-09-27-030000 --yes   # выполнит
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps                # все healthy
```

- Скачивать — **в отдельный каталог**, не в `/srv/backups/outreach-donors`:
  там ротация бэкапа считает каждую папку копией.
- `restore.sh` останавливает всех, кто пишет, разворачивает дамп, догоняет
  схему миграциями и печатает, сколько доменов в базе. Схема копии новее
  кода (код откатили) — поднимать образом того же коммита
  (`BACKEND_IMAGE` с меткой по SHA): вниз миграции не идут.
- Пока идёт восстановление, сервисы остановлены — сторож может прислать
  «сломалось» и следом «восстановилось». Это оно.
- **Машины нет совсем:** новая по разделу «Подготовка машины», код
  `git clone`, `.env` и `.env.ops` из копии владельца, стек целиком —
  `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d`
  (пустая база с миграциями), затем `fetch` и `restore.sh` как выше:
  он останавливает пишущих, а останавливать и запускать можно только
  то, что уже поднято.

**Проверить копию, не трогая боевую базу** — развернуть её в одноразовый
postgres рядом и посчитать строки:

```bash
docker run -d --name restore-check -e POSTGRES_USER=outreach \
  -e POSTGRES_PASSWORD=check -e POSTGRES_DB=outreach postgres:16-alpine
# По TCP: временный сервер начальной настройки слушает только сокет,
# и готовность по сокету наступает раньше настоящей.
until docker exec restore-check pg_isready -h 127.0.0.1 -U outreach -q; do sleep 1; done
docker exec -i restore-check pg_restore -U outreach -d outreach \
  < /srv/backups/outreach-donors-restore/2026-09-27-030000/outreach.dump
docker exec restore-check psql -U outreach -d outreach -tAc 'select count(*) from domains'
docker rm -f restore-check
```

**Восстановление проверено** 21.09.2026 на машине разработки: снятая
копия развёрнута в пустую базу, схема и содержимое совпали. 28.09.2026 —
вся цепочка через хранилище (MinIO вместо R2): база разработки (21 таблица,
4 969 строк) → `backup.sh` → хранилище (сверка rclone и отдельно листинг
с MD5) → `offsite.sh fetch` → `restore.sh` в одноразовую базу: все 21 таблица
совпали по числу строк и MD5 содержимого. С настоящим R2 или B2 и боевой
копией — ещё нет: выгрузку проверяет шаг 5 выше, копию — одноразовая база.

## Чего здесь ещё нет

- **Замера `docker stats` под нагрузкой.** Лимиты в `docker-compose.prod.yml`
  — оценка от объёмов, а не измерение. Их придётся пересчитать после
  первого настоящего прогона на машине; до этого 32 ГБ — расчёт, а не факт.
- **Выкатки как таковой.** 24.09.2026 решение пересмотрено: катим без
  почты, почту подключаем на боевой машине (раздел выше).
