"""Углы темы и пресеты.

Угол — это описание типа контента, которое подставляется в промпт
строкой. Отдельной ветки кода на угол нет: модель понимает такую
формулировку как есть, а нам не приходится писать по функции на каждый.

**Оператор выбирает пресет, а не пишет угол сам.** Формулировка решает,
что получится: неудачная даёт пул, который выглядит нормально, но ведёт
не к тем сайтам, — и обнаруживается это после того, как выдача уже
оплачена. Новый угол добавляется сюда после прогона и замера.

**Промптов несколько, по типам контента.** Одним текстом нельзя описать
и новости, и обзоры: шаблон обзоров, применённый к новостям, рождает
запросы, которых никто не набирает. Угол уточняет тип внутри своего
промпта, а не заменяет его.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).parent / "prompts"


@dataclass(frozen=True, slots=True)
class Angle:
    """Один угол: какой промпт берём и на чём внутри него сосредоточиться."""

    prompt: str  # имя файла промпта без расширения
    focus: str  # подставляется в промпт как «focus ONLY on this content angle»
    title: str  # как угол называется у нас, для отчётов и логов
    #: Модель даёт темы, а запрос собирает таблица футпринтов (`footprints`).
    footprints: bool = False


# --- Новости -------------------------------------------------------------

BREAKING = Angle(
    "news",
    "breaking news and daily national coverage: what happened today in the country",
    "срочные новости",
)
LOCAL_POLITICS = Angle(
    "news",
    "local and regional news: municipal affairs, city councils, regional authorities",
    "местная политика",
)
ECONOMY = Angle(
    "news",
    "business, finance and economy news: prices, taxes, wages, local business",
    "экономика",
)
SOCIETY = Angle(
    "news",
    "society, courts and investigations: crime, trials, public safety, social issues",
    "общество и суды",
)

# --- Обзоры и сравнения --------------------------------------------------

BEST_OF = Angle(
    "reviews",
    "best-of and top lists: which option to choose in a category",
    "подборки лучшего",
)
COMPARISON = Angle(
    "reviews",
    "head-to-head comparisons and alternatives: one option versus another",
    "сравнения",
)
HONEST_REVIEW = Angle(
    "reviews",
    "hands-on reviews and user experience: is it worth it, pros and cons",
    "отзывы и опыт",
)

# --- Инструкции ----------------------------------------------------------

HOW_TO = Angle(
    "guides",
    "step-by-step how-to guides: how to do something in practice",
    "инструкции",
)
RULES = Angle(
    "guides",
    "rules, documents and procedures: what is required, where to apply, what it costs",
    "правила и документы",
)
TROUBLE = Angle(
    "guides",
    "problem solving: what to do when something went wrong",
    "решение проблем",
)


# --- Гостевые: сайт сам зовёт авторов --------------------------------------
GUEST_TOPICS = Angle(
    "topics",
    "sections and subtopics of the niche that local blogs and online magazines write about",
    "темы для футпринтов",
    footprints=True,
)

#: Пресеты — обкатанные наборы. Порядок внутри набора важен: первым углам
#: достаётся остаток при делении потолка, и первыми они идут в выдачу.
PRESETS: dict[str, tuple[Angle, ...]] = {
    # Медийная ниша: новостные сайты и издания.
    "media": (BREAKING, LOCAL_POLITICS, ECONOMY, SOCIETY),
    # Обзорная: блоги и потребительские журналы, где охотно берут гостевые.
    "reviews": (BEST_OF, COMPARISON, HONEST_REVIEW),
    # Справочная: сайты с инструкциями и разборами правил.
    "guides": (HOW_TO, RULES, TROUBLE),
    # Смешанный: когда ниша заранее неизвестна и нужен широкий охват.
    "wide": (BREAKING, LOCAL_POLITICS, BEST_OF, COMPARISON, HOW_TO, RULES),
    # Гостевые: «<тема> write for us» и соседи — ищет тех, кто сам зовёт
    # авторов и продаёт размещение. Прогоны №21/№24: донор в разы дешевле
    # тематических запросов (`keywords/footprints.py`).
    "guest": (GUEST_TOPICS,),
}

DEFAULT_PRESET = "wide"


class UnknownPresetError(ValueError):
    """Такого пресета нет. Список известных — в сообщении, чтобы не искать."""


def preset(name: str | None) -> tuple[Angle, ...]:
    key = (name or DEFAULT_PRESET).strip().lower()
    angles = PRESETS.get(key)
    if angles is None:
        raise UnknownPresetError(f"Пресета «{name}» нет. Известные: {', '.join(sorted(PRESETS))}")
    return angles


@lru_cache(maxsize=8)
def load_prompt(name: str) -> str:
    """Текст промпта по имени. Читается один раз за запуск."""
    path = PROMPTS_DIR / f"{name}.md"
    if not path.exists():
        raise UnknownPresetError(
            f"Промпта «{name}» нет в {PROMPTS_DIR}. Угол ссылается на несуществующий файл"
        )
    return path.read_text(encoding="utf-8").strip()
