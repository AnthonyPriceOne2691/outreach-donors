"""Тематика сайта: общий список тем — один на все ниши, страны и языки.

**Зачем список, а не слова модели.** Тема нужна, чтобы фильтровать и искать: «покажи
финансовые сайты». Свободный ответ модели даёт «Finance», «personal finance», «финансы
и кредиты» и «банки» для одного и того же — фильтровать по такому нечем. Поэтому модель
выбирает код из списка, а ответ вне списка не принимается на веру (`answer.code_of`).

**Код — латиницей и не зависит от языка.** В базе лежит `finance`, а не «Финансы»: сайт
бывает на любом языке, экран — по-русски, и подпись можно поправить, не трогая базу.
Подпись (`label`) — то, что видит человек; пояснение (`about`) — то, что видит модель:
по-английски и коротко, без брендов и без примеров одной ниши — пример «казино» в теме
«азартные игры» учил бы модель одному рынку.

**Темы широкие — по предмету сайта, а не по способу заработка.** Способ заработка судит
судья площадки (`donors/publisher_judge.py`); здесь — о чём сайт: магазин электроники
и обзорщик гаджетов — оба «Технологии». Ширина выбрана так, чтобы любой сайт ложился
хоть в одну тему, а соседние не путались: «Новости» — газета и городской портал, даже
если статья о деньгах; «Обо всём» — блог без своей темы (так выглядят площадки гостевых
статей на любые темы), «Образ жизни» — журнал о повседневном: мода, дом, еда вперемешку.

**Расширяется без миграции.** Колонка — строка, а не перечисление базы: новая тема — новая
строка здесь (код, подпись, пояснение) и случаи в наборе eval (`scripts/data/topic_golden.jsonl`).
Поменялся смысл темы или промпт — новая версия (`classifier.TOPIC_VERSION`): сайты с прежней
версией прогон и досуд (`outreach topic-backfill`) определят заново. Удалять код нельзя, пока
он лежит в базе: экран показал бы его кодом. Подробнее — `docs/SITE_TOPICS.md`.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Topic:
    """Тема из списка: код в базе, подпись на экране, пояснение для модели."""

    code: str
    label: str
    about: str


#: Порядок — для промпта: от общего к частному, соседние по смыслу рядом, чтобы модель
#: видела границу. Экран сортирует по числу сайтов, а не по этому порядку.
TOPICS: tuple[Topic, ...] = (
    Topic("news", "Новости", "general news and current affairs, politics, newspapers, news agencies, city and regional portals"),
    Topic("general", "Обо всём", "no dominant subject: blogs or magazines publishing on any topic (business, health, tech, travel...) without a focus"),
    Topic("lifestyle", "Образ жизни", "everyday-life magazine or blog mixing fashion, home, food, relationships, celebrities"),
    Topic("business", "Бизнес", "companies, entrepreneurship, management, economy, startups, B2B services, franchising"),
    Topic("finance", "Финансы", "banking, loans, credit, insurance, investing, stock markets, forex, taxes, accounting, personal finance"),
    Topic("crypto", "Криптовалюты", "cryptocurrencies, blockchain, NFT, crypto exchanges and crypto trading"),
    Topic("gambling", "Азартные игры", "betting, bookmakers, casinos, slots, poker, lotteries, betting tips and predictions"),
    Topic("technology", "Технологии", "software, apps, IT, gadgets and electronics, internet, AI, telecom, programming"),
    Topic("marketing", "Маркетинг", "marketing, advertising, SEO, social media marketing, PR, e-commerce growth, web design for business"),
    Topic("science", "Наука", "science, research, space, nature, environment and ecology, popular science"),
    Topic("education", "Образование", "schools, universities, courses, exams, language learning, study help for students"),
    Topic("career", "Карьера", "jobs, vacancies, careers, CVs, HR, freelancing, workplace"),
    Topic("health", "Здоровье", "medicine, diseases, clinics, pharmacy, mental health, nutrition, diets, fitness and wellness"),
    Topic("fashion", "Мода и красота", "fashion, clothing, beauty, cosmetics, skincare, hair, style"),
    Topic("family", "Семья и дети", "parenting, children, pregnancy, family life, weddings"),
    Topic("dating", "Знакомства", "dating, romantic relationships, love and relationship advice (not explicit content)"),
    Topic("home", "Дом и сад", "home improvement, renovation, interior design, furniture, appliances, gardening, cleaning"),
    Topic("real_estate", "Недвижимость", "buying, selling and renting property, housing market, real estate agencies, new buildings"),
    Topic("industry", "Промышленность", "manufacturing, construction industry, engineering, energy, mining, logistics and freight, agriculture"),
    Topic("auto", "Авто", "cars, motorcycles, trucks, car reviews, repair, parts, driving, other vehicles"),
    Topic("travel", "Путешествия", "travel guides, tourism, destinations, hotels, flights, visas"),
    Topic("food", "Еда и напитки", "recipes, cooking, restaurants, food products, drinks, wine and beer"),
    Topic("sports", "Спорт", "sports news, teams, athletes, matches and results, sports equipment (not betting)"),
    Topic("gaming", "Видеоигры", "video games, esports, game reviews, guides and consoles"),
    Topic("entertainment", "Развлечения", "movies, TV series, music, celebrities, show business, humor, events"),
    Topic("culture", "Культура", "books, literature, art, history, museums, theatre, design, photography as art"),
    Topic("hobbies", "Хобби", "crafts, collecting, fishing, hunting, outdoor activities, model making, board games"),
    Topic("pets", "Животные", "pets, pet care, veterinary, animals and wildlife"),
    Topic("shopping", "Покупки", "product reviews across many categories, buying guides, deals, coupons, discounts, marketplaces"),
    Topic("legal", "Право", "law, legal advice, lawyers, courts, rights, immigration and official documents"),
    Topic("society", "Общество", "government bodies and public services, NGOs, charities, social issues, human rights"),
    Topic("religion", "Религия", "religion, faith, churches, spirituality"),
    Topic("esoteric", "Эзотерика", "astrology, horoscopes, tarot, numerology, dream interpretation, magic"),
    Topic("adult", "Для взрослых", "explicit adult content, porn, escort, sex shops"),
)  # fmt: skip

#: Код → тема. Пустых и повторных кодов нет — это стережёт тест (`test_topic_classifier.py`).
BY_CODE: dict[str, Topic] = {topic.code: topic for topic in TOPICS}

#: Код «тема не определена» в фильтре экрана: в базе это пусто, а не строка.
NOT_DEFINED = "none"


def label_of(code: str) -> str:
    """Подпись темы. Незнакомый код — код как есть: тему убрали из списка, а в базе она
    осталась; экран покажет её, а не пропустит строку молча."""
    topic = BY_CODE.get(code)
    return topic.label if topic is not None else code


def prompt_list() -> str:
    """Темы для промпта: «код — что сюда входит», по строке на тему."""
    return "\n".join(f"{topic.code} — {topic.about}" for topic in TOPICS)
