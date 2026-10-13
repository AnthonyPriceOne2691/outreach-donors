"""«Кому не пишем» на письмах продаж — общим правилом прогона доноров, без своей копии.

Решение владельца 13.10 (ревью стыков отправки): список «кому не пишем» ТЗ — гос. и учебные зоны,
платформы и соцсети, крупные СМИ, справочники — действует и на письма продаж. Правило одно с
доноров и сбора базы продаж: `runs/exclusions.by_name` и его слова (`NOT_WRITING_WORDS`), по имени
домена, без модели и без замера. DR > 80 — не здесь: метрик у лида нет, а замер платный.

**Домен лида — сайт его компании и домен его адреса**, как у стоп-листа продаж
(`mail.stopped_by`): лид из базы с сайтом компании `acme.com` и адресом на `bbc.co.uk` пишет из
крупного СМИ. Домен адреса — и корнем (`host_key`): `news.bbc.co.uk` → `bbc.co.uk`. Бесплатная
почта (`gmail.com` и подобные) в списках не стоит — лид с ней не отсекается.

Где действует:
- сборка очереди (`queue._blocked`): лид в очередь не встаёт, причина — в итоге сборки;
- автосборка (`autobuild.queued`, `autobuild.candidates`): письмо такого лида не в счёт очереди,
  а гипотеза, у которой без писем остались только такие лиды, — не кандидат: иначе проход
  ставил бы сборку, которая ничего не соберёт;
- проверка перед отправкой (`mail.check`): письмо, собранное до правила, не уходит ни проходом
  автоотправки, ни пачкой, ни кнопкой — отказ «кому не пишем» словами (`NotWritingError`), а
  добивка останавливает цепочку, как стоп-лист. Ответ лиду в переписке правило не держит: лид
  написал сам, и ответ ему — не рассылка (так же у доноров, `letters/donor_standing.py`).
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.features.runs.exclusions import NOT_WRITING_WORDS, by_name
from backend.features.sales.intake import host_key

#: Как это зовётся в итоге сборки и в отказе отправки — так же называет его итог пачки
#: (`letters/batch.why`: «кому не пишем»).
WHAT = "кому не пишем"


@dataclass(frozen=True, slots=True)
class Excluded:
    """Лид в «кому не пишем»: слова правила и домен, по которому оно сработало."""

    words: str
    host: str

    @property
    def waiting(self) -> str:
        """Причина в итоге сборки — без домена: одинаковые причины складываются."""
        return f"{WHAT}: {self.words}"

    def __str__(self) -> str:
        return f"{WHAT}: {self.words} ({self.host})"


def hosts_of(email: str, host: str) -> list[str]:
    """Домены лида по порядку: сайт компании, домен адреса, корень домена адреса."""
    mailbox = email.rpartition("@")[2].strip().lower()
    found = (host.strip().lower(), mailbox, host_key(mailbox) if mailbox else "")
    return list(dict.fromkeys(name for name in found if name))


def excluded(email: str, host: str) -> Excluded | None:
    """Лид с адресом `email` и сайтом компании `host` — в «кому не пишем»; `None` — пишем."""
    hosts = hosts_of(email, host)
    found = by_name(hosts)
    for name in hosts:
        if name in found:
            return Excluded(NOT_WRITING_WORDS[found[name]], name)
    return None
