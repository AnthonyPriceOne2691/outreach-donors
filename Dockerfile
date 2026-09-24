# Три образа из одного файла: сервер, воркер и фронт.
#
# Сервер и воркер — один и тот же образ с разной командой: у них общий
# код и общие зависимости, а два файла разъехались бы при первой правке
# (воркер получил бы библиотеку, которой нет у сервера, и падал бы
# только в проде).
#
# Фронт — собранные файлы под nginx, а не dev-сервер: dev-сервер отдаёт
# исходники и пересобирает на лету, ему нужны node_modules и он не умеет
# в кэш-заголовки.

# --- зависимости питона ----------------------------------------------------
FROM python:3.12-slim AS python-base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Сначала только зависимости: слой с ними переживает правку кода и не
# пересобирается на каждый коммит. Пустой `backend/__init__.py` нужен
# лишь затем, чтобы сборщику было что упаковать, — настоящий код придёт
# следующим слоем.
COPY pyproject.toml README.md ./
RUN mkdir -p backend && touch backend/__init__.py && pip install .

# --- сервер и воркер -------------------------------------------------------
FROM python-base AS backend

COPY alembic.ini ./
COPY backend ./backend

# Установка поверх заглушки — настоящим кодом и без зависимостей (они уже
# стоят слоем выше). Без этой строки в site-packages остаётся пустышка
# `backend/__init__.py`, она перекрывает `/app/backend`, и консольная
# команда внутри контейнера падает на `No module named backend.cli` —
# проверено живым запуском 19.09.2026. Сервер и воркер этого не замечали:
# их запускают из `/app`, где свой каталог оказывается в пути первым.
RUN pip install --no-deps .

# Своё имя вместо root: процесс, которому не нужны права, не должен их
# иметь — в том числе внутри контейнера.
RUN useradd --create-home --uid 10001 app && chown -R app:app /app
USER app

# Порт внутри сети докера. Наружу его публикует только компоуз разработки:
# в бою наружу смотрит nginx, а к серверу ходят из своей сети.
EXPOSE 8000

# `--proxy-headers` не украшение: без него счётчик попыток входа видит
# адрес nginx вместо адреса клиента и считает весь интернет одним
# посетителем. Доверять заголовкам можно потому, что снаружи в контейнер
# ходит только nginx из этой же сети.
CMD ["uvicorn", "backend.api.main:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]

# --- сборка фронта ---------------------------------------------------------
FROM node:22-alpine AS web-build

WORKDIR /web

COPY frontend/package.json frontend/package-lock.json ./
# `npm ci`, а не `npm install`: ставит ровно то, что в замке. Иначе образ,
# собранный завтра, отличается от собранного сегодня, и «у меня работало»
# становится правдой обоих.
RUN npm ci

COPY frontend ./
RUN npm run build

# --- фронт под nginx -------------------------------------------------------
FROM nginx:1.27-alpine AS web

COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
COPY deploy/security-headers.conf /etc/nginx/snippets/security-headers.conf
COPY --from=web-build /web/dist /usr/share/nginx/html

EXPOSE 80
