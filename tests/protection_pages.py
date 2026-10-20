"""Страницы целиком, как их отдаёт сайт с кодом 200: заглушка защиты и обычная статья.

Общие для судьи главной (`donors/home_signals.py`) и обхода (`crawl/fetch.py`): заглушку
узнают оба, и узнавать её надо в том виде, в каком она приходит, — документом с head
и body, а не строкой заголовка. Адреса — `example`.
"""

from __future__ import annotations

import httpx

#: Страница блокировки Imperva (бывший Distil) вместо главной: поймана 20.10.2026 на главных
#: сбора продаж — судья сегмента читал её как сайт. Заголовок — в head, объяснение — в body.
IMPERVA_BLOCK = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="robots" content="noindex, nofollow">
<meta http-equiv="cache-control" content="max-age=0">
<meta http-equiv="cache-control" content="no-cache">
<meta http-equiv="expires" content="0">
<meta http-equiv="pragma" content="no-cache">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pardon Our Interruption</title>
<style>
body { font-family: Arial, sans-serif; color: #333; }
.container { max-width: 800px; margin: 0 auto; padding: 20px; }
</style>
<script src="/assets/7f3a91c2.js" async></script>
</head>
<body>
<div class="container">
<h1>Pardon Our Interruption</h1>
<p>As you were browsing something about your browser made us think you were a bot.
There are a few reasons this might happen:</p>
<ul>
<li>You're a power user moving through this website with super-human speed.</li>
<li>You've disabled cookies in your web browser.</li>
<li>A third-party browser plugin is preventing JavaScript from running. Additional
information is available in this <a href="https://help.example.com/plugins">support
article</a>.</li>
</ul>
<p>To regain access, please make sure that cookies and JavaScript are enabled before
reloading the page.</p>
</div>
</body>
</html>
"""

#: Обычная статья, где «pardon» — просто слово: в заголовке, описании и тексте, рядом
#: с «Pardon our dust» и названием передачи «Pardon the Interruption». Не заглушка.
PARDON_ARTICLE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Presidential Pardon: How Clemency Works | Law Notes</title>
<meta name="description" content="What a pardon is, who can grant one and how to apply.">
</head>
<body>
<header><nav><a href="/">Home</a> <a href="/guides">Guides</a></nav></header>
<main>
<h1>Presidential Pardon: How Clemency Works</h1>
<p>Pardon our dust while we update this guide.</p>
<p>A pardon forgives a crime and restores the rights lost with the conviction. The word
also names a sports talk show, "Pardon the Interruption", which has nothing to do with law.</p>
</main>
</body>
</html>
"""


def answering(html: str) -> httpx.MockTransport:
    """Сайт, который на любой адрес отвечает этой страницей с кодом 200."""
    return httpx.MockTransport(lambda _request: httpx.Response(200, html=html))
