"""Фраза-призыв у судьи продаж: где опора — настройки отправителя, а не запись базы.

Судья-модель называет утверждения черновика с номерами записей базы, на которые они
опираются (`judge.py`). Призыв — «Подробнее покажем на коротком созвоне: <ссылка>» —
не утверждение: промпт судьи так и говорит, и ссылку призыва модель видит в данных
отправителя. Но сослаться на настройки в её ответе нечем — опора только номер записи, — и
граница правила плавает: живой замер 08.10 на синтетике задерживал 2–4 хороших черновика из
12 «утверждением без опоры на базу» ровно на такой фразе. Задержка дорога не одной правкой:
писатель по замечанию убирает призыв, правило «нет призыва» возвращает его, и после трёх
правок черновик уходит человеку.

**Правило кодом.** Утверждение модели, процитированное внутри «голого» призыва, не нарушение:
опора у него — настройки отправителя. Голый призыв (`bare`) — предложение черновика, где
стоит ссылка призыва этого письма (`[cta …]` брифа), и больше ничего:

- ссылка в нём одна — сама ссылка призыва, и в черновике она стоит в одном предложении;
- предложение одно и без второй части: без запятой, точки с запятой, тире, скобок,
  двоеточия не перед ссылкой и без союза («и», «что», «and», «that» …);
- ни одного числа и знака валюты, ни слова о деньгах и цене, бесплатном и скидке,
  о гарантии, результате и сроке, ни похвалы себе (`_MORE`).

Список слов — в сторону осторожности: лишнее слово оставляет фразу модели (как до правила),
пропущенное — пропуск утверждения. Обещания модели правило не трогает: их ловит только она.
Цена прописью в той же фразе («обойдётся в пятьсот долларов, детали обсудим…») призыв голым
не делает — в синтетике так написаны четыре опасных случая из восьми.
"""

from __future__ import annotations

import re

from backend.features.sales.agent import reading
from backend.features.sales.agent.facts import Context

#: Что в фразе-призыве уже не призыв: число и валюта; деньги, цена, бесплатное и скидка;
#: гарантия, результат и срок; похвала себе; числа словами.
_MORE = re.compile(
    r"\d|[$€£₽¥%№#]|надцат|дцат|teen\b"
    r"|\b(?:доллар|евро|рубл|руб\b|цен|стоим|стои[тл]|обойд|оплат|плат|бесплатн|даром|скидк"
    r"|бюджет|тариф|прайс|деш[её]в|дорог|выгод|процент|акци|бонус|подар|пробн"
    r"|dollar|euro|usd\b|eur\b|rub\b|price|pricing|cost|fees?\b|rat(?:e|es|ed|ing)\b"
    r"|charge|pay|paid|free|discount|budget|tariff|cheap|expensive|affordable|deal|percent"
    r"|promo|bonus|gift|trial|offer)"
    r"|\b(?:гарант|обеща|обеспеч|результат|рост|вырас|вырос|увелич|удво|утро(?:им|ит)|топ"
    r"|позици|страниц|срок|дн[еёяйи]|день|недел|месяц|час(?:а|ов|ы)?\b|сразу"
    r"|guarantee|promise|ensur|assur|result|grow|increase|double|top\b|rank|page|deadline"
    r"|within|day|week|month|hour|asap|instant)"
    r"|\b(?:лучш|крупн|ведущ|лидер|эксперт|специалист|сертифиц|опыт|лет\b|клиент|партн[её]р"
    r"|наград|единствен|довольн|кейс|известн|над[её]жн|проверен|миров"
    r"|best|lead(?:ing|er)|largest|biggest|expert|specialist|certified|experienc|years?\b"
    r"|client|customer|partner|award|only\b|unique|satisf|case|proven|trusted|renowned"
    r"|famous|world)"
    r"|\b(?:од(?:ин|на|ну|ного)\b|дв[аеу]\b|двух|тр[иёе]\b|тр[её]х|четыр|пят[ьи]|шест"
    r"|сем[ьи]\b|восем|девят|десят|сорок|девяност|сто\b|сот(?:ен|ни|ня)|тысяч|миллион|полов"
    r"|вдво|втро|дважды|трижды"
    r"|one\b|two\b|three|four|five|six|seven|eight|nine|ten\b|eleven|twelve|twenty|thirty"
    r"|forty|fifty|hundred|thousand|million|dozen|half|twice|triple)",
    re.IGNORECASE,
)
#: Вторая часть предложения: знаки и союзы. Двоеточие перед ссылкой в конце — не в счёт:
#: оно снимается вместе с концом фразы.
_PARTS = re.compile(
    r"[,;:()\[\]—–]|\s-\s"
    r"|\b(?:и|а|но|или|либо|что|чтобы|потому|поэтому|который|которая|которое|которые|если|когда"
    r"|and|but|or|so|because|since|that|which|who|while|if|when)\b",
    re.IGNORECASE,
)
_TAIL = " \t.…!?:;"


def _words(text: str) -> list[str]:
    return re.findall(r"\w+", text.casefold().replace("ё", "е"))


def bare(draft: str, context: Context) -> str | None:
    """Голый призыв черновика — предложение со ссылкой призыва и только с ней.

    `None` — у письма нет призыва, ссылка стоит не в одном предложении или фраза говорит
    больше, чем зовёт: тогда её судит модель, как любое утверждение."""
    if context.cta is None:
        return None
    link = reading.normalized(context.cta[1])
    holders = [
        piece
        for piece in reading.sentences(draft)
        if link in {reading.normalized(found) for found in reading.links_in(piece)}
    ]
    if len(holders) != 1 or len(reading.links_in(holders[0])) != 1:
        return None
    rest = reading.without_links(holders[0]).strip().rstrip(_TAIL).strip()
    if not rest or _MORE.search(rest) or _PARTS.search(rest):
        return None
    return holders[0]


def inside(quote: str, phrase: str) -> bool:
    """Цитата модели — кусок фразы подряд, слово в слово (регистр, «ё» и знаки не в счёт)."""
    said, whole = _words(quote), _words(phrase)
    if not said:
        return False
    size = len(said)
    return any(whole[start : start + size] == said for start in range(len(whole) - size + 1))
