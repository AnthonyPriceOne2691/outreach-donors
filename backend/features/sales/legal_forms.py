"""Организационно-правовые формы фирм разных стран: по ним в ключе узнаётся название фирмы.

Ключ «ТОО Ромашка» или «Acme Ltd» — имя одной фирмы: выдача по нему найдёт её карточки в реестрах
и справочниках, а не сайты сегмента. Смета говорит это словами, ключ не отсеивается — решает
человек (`segment_keywords`).

**Источник списка** — ISO 20275 «Financial services — Entity legal forms (ELF)»: перечень форм
по странам с сокращениями, его ведёт GLEIF (gleif.org, «ISO 20275 Entity Legal Forms Code
List»). Здесь — ходовые сокращения многих стран и письменностей, а не весь перечень.

**Как ищется.** Форма — отдельным словом (не внутри слова: «Ltd» в «Ltdbikes», «АО» в «Каолин» —
нет), без учёта регистра, с точками и без («L.L.C.» и «LLC», «Sp. z o.o.» и «sp z oo»): точка в написании ниже необязательна, пробел после неё — тоже; косая черта
(«A/S») обязательна. Исключения — данными, а не правилами отдельных языков:

- `CASED` — без точек сокращение совпадает с обычным словом какого-то языка («spa» — салон,
  «ivs» — капельницы, «lda» — модель тем, тур. «aş» — еда, каз. «ақ» — белый, рус. «пат»): без
  точек оно считается, только если написано в регистре реестра («SpA», «Oy», «АҚ»), с точками —
  в любом.
- `CODES` — двухбуквенные формы, которые без точек совпадают с кодом страны, штата или провинции
  или с единицей («Calgary AB», «Reno NV», «25 KG»): только с точками («S.A.», «N.V.», «K.G.»).
  Ложная пометка хуже пропущенной: «Volvo AB» без точек не помечается.
- `DOTTED` — без точек это обычное слово, а регистра у письменности нет или он не спасает (араб.
  «ذمم», англ. «ad», «lp», «doo», «ska», исп. «vos»): только с точками.
- `ATTACHED` — формы письменностей, где их пишут слитно с названием («株式会社サンプル»,
  «深圳样本有限公司»): ищутся вхождением.

**Не в списке нарочно** — пометка висела бы на ключах целых ниш: PLC (программируемый
контроллер), PT и CV (Индонезия: part-time, physical therapy, резюме), ЖК (жилой комплекс), АД и
АТ (давление и антитела в медицине), ЧП (происшествие), ПП, ПК, MB, KB, SE, Ky, Co, Limited,
Company. «Ltd» остаётся, хотя бывает и «ltd edition»: это главная форма англоязычных стран.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

#: Формы, которые без точек — не слово ни одного языка: регистр не важен.
FORMS: tuple[str, ...] = (
    # Россия, Казахстан, Беларусь, Украина, Центральная Азия, Кавказ, Монголия
    "ООО", "ОАО", "ЗАО", "ПАО", "НАО", "АО", "ТОО", "ИП", "АНО", "ГУП", "МУП", "ОДО", "ЧУП",
    "ТОВ", "ФОП", "ПрАТ", "ЖШС", "МЧЖ", "MChJ", "ОсОО", "ХХК", "შპს", "ՍՊԸ",
    # Болгария, Сербия, Хорватия, Словения, Босния, Северная Македония
    "ЕООД", "ООД", "ЕАД", "ДООЕЛ", "д.о.о.",
    # Англоязычные страны, Индия, Сингапур, Малайзия, Вьетнам, Индонезия
    "L.L.C.", "L.L.P.", "P.L.L.C.", "Inc.", "Corp.", "Ltd.", "Pty Ltd", "Pty.", "Pvt. Ltd.",
    "Pte. Ltd.", "Sdn. Bhd.", "TNHH", "CTCP", "JSC", "Tbk.",
    # Германия, Австрия, Швейцария
    "GmbH", "Ges.m.b.H.", "OHG", "GbR", "KGaA",
    # Франция, Бельгия, Люксембург, Нидерланды
    "S.A.R.L.", "Sàrl", "S.A.S.U.", "E.U.R.L.", "S.N.C.", "S.P.R.L.", "B.V.B.A.", "V.O.F.",
    # Испания, Португалия, Италия, Румыния, Молдова, Латинская Америка
    "S.L.U.", "S.L.L.", "Ltda.", "Unipessoal", "S.R.L.", "S.r.l.s.", "S/A", "EIRELI",
    "S.A. de C.V.", "S. de R.L.", "E.I.R.L.", "PFA",
    # Польша, Чехия, Словакия, Венгрия
    "Sp. z o.o.", "sp.j.", "sp.k.", "s.r.o.", "spol. s r.o.", "Kft.", "Zrt.", "Nyrt.", "Kkt.",
    # Северные страны и Балтия
    "A/S", "I/S", "P/S", "Oyj", "ehf.", "OÜ", "UAB",
    # Греция, Кипр, Турция, Израиль, Иран, Корея
    "Ε.Π.Ε.", "Ι.Κ.Ε.", "ΛΤΔ", "Ltd. Şti.", "Şti.", "בע״מ", "سهامی خاص",
)  # fmt: skip

#: Без точек — обычное слово или единица какого-то языка: без точек — только в регистре реестра.
CASED: tuple[str, ...] = (
    "A.S.A.", "Oy", "SIA", "ApS", "S.p.A.", "S.A.S.", "IVS", "Lda.", "A.Ş.", "Α.Ε.", "Ο.Ε.",
    "Bhd.", "OOO", "MMC", "ПАТ", "АҚ",
)  # fmt: skip

#: Двухбуквенные формы, которые без точек совпадают с кодом страны (ISO 3166-1), штата или
#: провинции или с единицей: без точек пометка легла бы на ключи с регионом и весом («dentist
#: Calgary AB», «plumber Reno NV», «25 KG»). Ложная пометка хуже пропущенной — только с точками.
CODES: tuple[str, ...] = (
    "A.B.",  # Швеция, Литва — и Альберта (Канада)
    "A.G.",  # Германия, Швейцария — и кантон Аргау, Антигуа и Барбуда
    "A.S.",  # Норвегия, Эстония, Латвия, Чехия и Словакия («a.s.») — и Американское Самоа
    "B.V.",  # Нидерланды, Бельгия — и остров Буве
    "K.G.",  # Германия, Австрия — и килограмм, Киргизия
    "N.V.",  # Нидерланды, Бельгия — и Невада
    "S.A.",  # Испания, Франция, Польша, Латинская Америка — и Южная Австралия, Саудовская Аравия
    "S.L.",  # Испания — и Сьерра-Леоне
    "U.G.",  # Германия — и Уганда, микрограмм («ug»)
)

#: Без точек — обычное слово, регистр не спасает: только с точками.
DOTTED: tuple[str, ...] = (
    "d.o.o.", "d.d.", "a.d.", "а.д.", "v.o.s.", "S.K.A.", "e.K.", "e.U.", "Bt.", "K.K.", "G.K.",
    "L.P.", "S.Coop.", "ذ.م.م", "ش.م.م", "ش.م.ع", "ش.م.ل",
)  # fmt: skip

#: Письменности без пробелов: форму пишут слитно с названием — ищется вхождением.
ATTACHED: tuple[str, ...] = (
    "株式会社", "有限会社", "合同会社", "(株)", "(有)", "有限公司", "有限责任公司", "有限責任公司",
    "股份公司", "주식회사", "유한회사", "(주)", "บจก.", "บมจ.", "หจก.",
)  # fmt: skip

#: Точка и знаки сокращения иврита (гершаим, гереш) — необязательны; косая черта — нет.
_MARKS = ".״׳"
_PIECES = re.compile(rf"([{re.escape(_MARKS)}/\s]+)")


def _separator(written: str) -> str:
    """Разделитель частей формы в образце: как его можно написать в ключе."""
    if "/" in written:
        return r"\s*/\s*"
    marks = f"[{re.escape(_MARKS)}]"
    if not written.strip():
        return r"\s+"  # «Pty Ltd»: два слова
    if written[-1].isspace():
        return rf"{marks}?\s*"  # «Sp. z»: точка и пробел, любое из них можно опустить
    return rf"(?:{marks}\s*)?"  # «S.A.»: без точки — слитно, «A S» не форма


def _pattern(spelling: str, *, attached: bool) -> re.Pattern[str]:
    parts = [part for part in _PIECES.split(spelling) if part]
    body = ""
    for number, part in enumerate(parts):
        if not _PIECES.fullmatch(part):
            body += re.escape(part)
        elif number == len(parts) - 1:
            body += rf"[{re.escape(_MARKS)}]?"  # хвостовая точка «Inc.» — необязательна
        else:
            body += _separator(part)
    return re.compile(body if attached else rf"(?<!\w){body}(?!\w)", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class _Form:
    pattern: re.Pattern[str]
    #: Буквы формы в регистре реестра: без точек форма считается, только если написана так.
    cased: str | None = None
    #: Без точек — не форма.
    dotted: bool = False

    def found(self, text: str) -> list[re.Match[str]]:
        return [match for match in self.pattern.finditer(text) if self._holds(match.group())]

    def _holds(self, written: str) -> bool:
        if any(mark in written for mark in _MARKS + "/"):
            return True
        if self.dotted:
            return False
        return self.cased is None or _letters(written) == self.cased


def _letters(written: str) -> str:
    return "".join(char for char in written if char not in _MARKS + "/" and not char.isspace())


_ALL: tuple[_Form, ...] = (
    *(_Form(_pattern(form, attached=False)) for form in FORMS),
    *(_Form(_pattern(form, attached=False), cased=_letters(form)) for form in CASED),
    *(_Form(_pattern(form, attached=False), dotted=True) for form in (*CODES, *DOTTED)),
    *(_Form(_pattern(form, attached=True)) for form in ATTACHED),
)


def firm_form(phrase: str) -> str | None:
    """Форма фирмы в ключе — как её написал человек (первая по месту, у одного места — самая
    длинная: «S.A. de C.V.», а не «S.A.»); `None` — формы нет. Ключ — как вписан: регистр нужен
    формам `CASED`; ширина знаков и составные знаки («㈱») — по NFKC."""
    text = unicodedata.normalize("NFKC", phrase)
    found = [match for form in _ALL for match in form.found(text)]
    if not found:
        return None
    first = min(found, key=lambda match: (match.start(), -len(match.group())))
    return first.group().strip()
