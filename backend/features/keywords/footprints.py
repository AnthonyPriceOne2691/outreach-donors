"""Футпринты гест-постинга: темы ниши × «пишите для нас» на языке рынка.

**Зачем, если углы уже есть.** Угол просит у модели готовые запросы про
контент — новости, обзоры, инструкции, — и выдача по ним приносит
в основном тех, кто продаёт своё: прогон №18 на «best crm software»
и «hubspot vs salesforce» — 253 из 394 доменов в очереди скрыты как
«продаёт своё». Донора для гест-постинга находит футпринт — «<тема>
write for us», «<тема> guest post»: сайт сам зовёт авторов. Прогоны
№21 и №24, где футпринты вписали руками, дали похожего донора в разы
дешевле (оценка до разбора человеком: ≈65 юнитов против 350–400).

**Модель придумывает только темы, слова футпринта ставит таблица.**
Запросы-футпринты различаются одним шаблоном, и дедуп по доле общих слов
схлопнул бы «diy write for us» и «gardening write for us»: 3/5 = 0,6 —
ровно порог. Поэтому почти-дубли убираются среди тем, а пары «тема ×
шаблон» различны по построению.

**Словарь рынка, а не ниши** — те же языки, что у двери для авторов
(`donors.author_door`). Языка нет в таблице — отказ с выходом, а не
английские слова в чужом рынке: такой запрос уводит выдачу в англоязычный
веб, а у другой письменности гигиена отрезала бы его молча.
"""

from __future__ import annotations

from collections.abc import Sequence

from backend.features.keywords.hygiene import normalize, rejection_reason, same_script

#: Язык рынка (`serp.markets.LANGUAGE_NAMES`) → шаблоны запроса. Первые два —
#: «пишите для нас» и «гостевой пост»; третий зовёт авторов прямее; последний
#: ищет тех, кто продаёт размещение, — спонсорскую статью.
FOOTPRINTS: dict[str, tuple[str, ...]] = {
    "English": (
        "{topic} write for us",
        "{topic} guest post",
        "{topic} submit a guest post",
        "{topic} sponsored post",
    ),
    "German": (
        "{topic} gastbeitrag",
        "{topic} gastartikel",
        "{topic} gastautor werden",
        "{topic} gesponserter beitrag",
    ),
    "Spanish": (
        "{topic} escribe para nosotros",
        "{topic} colabora con nosotros",
        "{topic} artículo invitado",
        "{topic} artículo patrocinado",
    ),
    "French": (
        "{topic} écrire pour nous",
        "{topic} article invité",
        "{topic} devenir rédacteur",
        "{topic} article sponsorisé",
    ),
    "Italian": (
        "{topic} scrivi per noi",
        "{topic} guest post",
        "{topic} articolo ospite",
        "{topic} articolo sponsorizzato",
    ),
    "Portuguese": (
        "{topic} escreva para nós",
        "{topic} guest post",
        "{topic} artigo convidado",
        "{topic} artigo patrocinado",
    ),
    "Dutch": (
        "{topic} schrijf voor ons",
        "{topic} gastblog",
        "{topic} gastartikel",
        "{topic} gesponsord artikel",
    ),
    "Polish": (
        "{topic} napisz dla nas",
        "{topic} artykuł gościnny",
        "{topic} wpis gościnny",
        "{topic} artykuł sponsorowany",
    ),
}


class NoFootprintsError(ValueError):
    """Для языка рынка футпринтов нет. Сообщение называет, какие есть."""


def templates_for(language: str) -> tuple[str, ...]:
    """Шаблоны языка — или отказ, который говорит, что делать."""
    found = FOOTPRINTS.get(language)
    if found is None:
        raise NoFootprintsError(
            f"Футпринтов для языка «{language}» нет — набор «guest» знает: "
            f"{', '.join(sorted(FOOTPRINTS))}. Впишите ключи руками или возьмите "
            "другой набор."
        )
    return found


def topics_needed(share: int, templates: Sequence[str]) -> int:
    """Сколько тем нужно на долю пула: каждая даёт по запросу на шаблон."""
    return -(-share // len(templates)) if share > 0 else 0


def with_niche(topic: str, asked: Sequence[str], templates: Sequence[str]) -> list[str]:
    """Тема оператора — первой: «home improvement write for us» — самый
    широкий запрос ниши, а модель отдаёт подтемы («kitchen remodel»)
    и назвать саму нишу первой обещает через раз (замер 24.09: «garten»
    вышла «gartengestaltung»).

    Только тема на письме самого футпринта и прошедшая гигиену: «ставки
    на спорт» при английских шаблонах — две письменности в одном запросе,
    такую тему переводит модель, а не подставляем мы.
    """
    niche = normalize(topic)
    words = templates[0].format(topic="")
    if not niche or not same_script(niche, words):
        return list(asked)
    if rejection_reason(templates[0].format(topic=niche)) is not None:
        return list(asked)
    return [niche, *asked]


def expand(topics: Sequence[str], templates: Sequence[str]) -> list[str]:
    """Темы × шаблоны, тема за темой: обрезка потолком отнимает последнюю
    тему, а не последний шаблон у всех — шаблоны ищут разное."""
    return list(
        dict.fromkeys(template.format(topic=topic) for topic in topics for template in templates)
    )
