# Outreach Donors

Поиск сайтов-доноров по выдаче Google, проверка их метриками Ahrefs и сбор
цен размещения по email.

Прогон: список ключевых слов и страна → выдача → отсев по порогам (регион, DR,
трафик) → база доноров → запрос цены письмом → разбор ответа в поля «цена белая,
цена серая, способы оплаты».

Правила домена, которые нельзя нарушать молча, — в [okf/](okf/index.md):
экономика запросов, пороги отбора, правило региона, сроки годности.
Процесс и неотменяемые решения — в [delivery/](delivery/CONSTITUTION.md).

## Стек

Python 3.12+, FastAPI, PostgreSQL 16, Redis, SQLAlchemy 2 + Alembic.

## Запуск

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
cp .env.example .env            # заполнить ключи
docker compose -f docker-compose.dev.yml up -d
.venv/bin/alembic upgrade head
```

Порты на хосте нестандартные — 5442 и 6389. Рядом живёт соседняя система,
она занимает 5432 и 6379.

## Проверки

Хук перед отправкой прогоняет всё то же, что и CI, — но до пуша, а не после.
Путь к хукам в `.git/config` не переносится клонированием, поэтому включается
одной командой:

```bash
git config core.hooksPath scripts/hooks
```

Аварийный обход — `git push --no-verify`. В CI обхода нет.


```bash
.venv/bin/ruff check backend/ && .venv/bin/ruff format --check backend/
.venv/bin/mypy backend/
.venv/bin/pytest
```

## Раскладка

```
backend/
  config/     настройки по доменам; обращения к окружению только здесь
  features/
    core/     модели и перечисления домена
  shared/     база данных
  migrations/ Alembic
```

Домен = папка, внутри `api / services / repository / models / schemas`.
Так модули переезжают в общую CRM целиком, а не разбираются по слоям.
