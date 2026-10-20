"""Что собрано по одному домену за проход лестницы: адреса, отсев, каналы, форма.

Ступени лестницы пишут сюда, а не друг другу: накопитель один на домен,
и правило «брать ли адрес» в нём одно — фильтр качества, вердикт MX
по домену сайта и страница, где адрес встретился (`add`). Лестница и её
ступени живут в `ladder.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlsplit

from backend.features.contacts.extract import ascii_domain
from backend.features.contacts.messengers import FoundHandle
from backend.features.contacts.mx import MailRoute
from backend.features.contacts.quality import Candidate, foreign_on_legal_page, rejection_reason
from backend.features.core.domain import PageKind

#: Какую страницу с формой показать человеку, если форма на нескольких: раздел
#: контактов — та самая «напишите нам», раздел рекламы — тоже письмо владельцу;
#: форма на главной или в «о нас» — бывает подпиской с полем для текста.
_FORM_RANK = {
    PageKind.CONTACT: 4,
    PageKind.MONEY: 3,
    PageKind.ABOUT: 2,
    PageKind.HOME: 1,
    PageKind.LEGAL: 0,
}

#: Длиннее адрес страницы с формой не храним (`domains.contact_form_url`): такой
#: адрес — это уже параметры трекинга, а не страница.
FORM_URL_MAX = 1024


@dataclass(slots=True)
class Collected:
    """Накопитель годных и отсеянных адресов по одному домену."""

    site_host: str
    good: list[Candidate] = field(default_factory=list)
    #: Каналы связи, кроме почты. Отдельно от `good`, потому что отбор
    #: адресов их не касается: у ника нет ни домена, ни ролевой части,
    #: по которым адрес взвешивают, и отсеивать его нечем.
    handles: set[FoundHandle] = field(default_factory=set)
    rejected: list[tuple[str, str]] = field(default_factory=list)
    seen: set[str] = field(default_factory=set)
    has_form: bool = False
    #: Где форма: страница, которую откроет человек на «Формах». Лучшая по виду
    #: (`_FORM_RANK`), из равных — первая встреченная.
    form_url: str | None = None
    _form_kind: PageKind = field(default=PageKind.LEGAL, repr=False)
    #: Сайт закрылся от обычного запроса: 401, 403, 429 или страница проверки
    #: «вы не робот». Только такие и имеет смысл открывать браузером — он стоит
    #: секунд на страницу.
    blocked: bool = False
    #: Почему не открылась ни одна страница — словами (`pages.refusal_words`).
    refusal: str = ""
    #: Страницы сайта, разобранные обходом и браузером, — по адресу без схемы и `www.`:
    #: страница, которую браузер перерисовал после обхода, — та же, а не вторая.
    read: set[str] = field(default_factory=set)
    #: Вердикт ступени MX по домену сайта.
    route: MailRoute | None = None
    #: Последний шанс сайту ответить: в таком проходе платят и при молчании.
    last_try: bool = False

    def note_form(self, url: str, kind: PageKind) -> None:
        """Форма на странице: запомнить её, если она лучше прежней по виду страницы."""
        self.has_form = True
        if len(url) > FORM_URL_MAX:
            return
        if self.form_url is None or _FORM_RANK.get(kind, 0) > _FORM_RANK.get(self._form_kind, 0):
            self.form_url, self._form_kind = url, kind

    def note_read(self, url: str) -> None:
        """Страница сайта разобрана."""
        parts = urlsplit(url)
        host = (parts.hostname or "").removeprefix("www.")
        self.read.add(f"{host}{parts.path.rstrip('/') or '/'}")

    @property
    def pages_read(self) -> int:
        """Страниц сайта разобрано: ноль — сайт не прочитан вовсе, и «адреса нет» без
        причины было бы неправдой."""
        return len(self.read)

    @property
    def direct(self) -> list[Candidate]:
        """Годные адреса, записанные на сайте прямо, — без догадок (`Candidate.guessed`):
        только они прекращают обход и спуск (`ladder._descend`)."""
        return [candidate for candidate in self.good if not candidate.guessed]

    @property
    def unread_reason(self) -> str:
        """Почему у домена «адреса нет», если сайт не прочитан вовсе; пусто — прочитан."""
        return "" if self.pages_read else self.refusal

    def _undeliverable(self, email: str) -> str | None:
        """Адрес на домене сайта, который почту не принимает, отбился бы.

        Сверяется имя целиком: вердикт MX — про этот домен, и поддомен без
        почты ничего не говорит о почте родительского. Личный ящик на чужом
        домене (`owner@gmail.com`) остаётся: он доставляем.
        """
        if ascii_domain(email.rsplit("@", 1)[-1]) != ascii_domain(self.site_host):
            return None
        if self.route is MailRoute.NONE:
            return f"домен сайта не принимает почту (ни MX, ни A): {email}"
        if self.route is MailRoute.NULL_MX:
            return f"домен сайта объявил, что почту не принимает (нулевой MX): {email}"
        return None

    def _refused_by_page(self, candidate: Candidate) -> bool:
        """Сторонний адрес со страницы правил — не адрес редакции.

        Отсев — по странице, а не по адресу, поэтому адрес не помечается
        увиденным: тот же адрес со страницы контактов годен, даже если
        страница правил встретилась раньше (ревью #144). Отказ пишется
        один раз, сколько бы страниц правил его ни повторили.
        """
        on_page = foreign_on_legal_page(candidate, site_host=self.site_host)
        if on_page is None:
            return False
        if (candidate.email, on_page) not in self.rejected:
            self.rejected.append((candidate.email, on_page))
        return True

    def add(self, candidate: Candidate) -> bool:
        """Взять адрес, если он годный и ещё не встречался."""
        if candidate.email in self.seen:
            # Догадка пришла раньше (обфускация в подвале главной), а теперь
            # тот же адрес записан прямо: он больше не догадка и не должен
            # проигрывать прямым адресам (ревью #137).
            if not candidate.guessed:
                self.good = [
                    candidate if known.email == candidate.email and known.guessed else known
                    for known in self.good
                ]
            return False
        reason = rejection_reason(candidate.email) or self._undeliverable(candidate.email)
        if reason:
            self.seen.add(candidate.email)
            self.rejected.append((candidate.email, reason))
            return False
        if self._refused_by_page(candidate):
            return False
        self.seen.add(candidate.email)
        # Адрес нашёлся там, где он годен: прежний отказ по странице снят.
        self.rejected = [entry for entry in self.rejected if entry[0] != candidate.email]
        self.good.append(candidate)
        return True
