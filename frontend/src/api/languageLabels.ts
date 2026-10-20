/**
 * Языки рынка словами. Сервер называет их по-английски — так их понимает модель
 * (`serp/markets.py`), а на экране они по-русски: «языки: русский, узбекский» у сборки ключей.
 *
 * Своим файлом: `labels.ts` у потолка длины. Подпись есть у каждого языка рынка — это держит
 * тест (`tests/test_serp_markets.py`): без неё язык вышел бы на экран «другой язык (Uzbek)».
 */

const LANGUAGE_TITLES: Record<string, string> = {
  Arabic: 'арабский',
  Bulgarian: 'болгарский',
  Croatian: 'хорватский',
  Czech: 'чешский',
  Danish: 'датский',
  Dutch: 'нидерландский',
  English: 'английский',
  Estonian: 'эстонский',
  Filipino: 'филиппинский',
  Finnish: 'финский',
  French: 'французский',
  German: 'немецкий',
  Greek: 'греческий',
  Hebrew: 'иврит',
  Hungarian: 'венгерский',
  Indonesian: 'индонезийский',
  Italian: 'итальянский',
  Japanese: 'японский',
  Kazakh: 'казахский',
  Latvian: 'латышский',
  Lithuanian: 'литовский',
  Malay: 'малайский',
  Norwegian: 'норвежский',
  Polish: 'польский',
  Portuguese: 'португальский',
  Romanian: 'румынский',
  Russian: 'русский',
  Slovak: 'словацкий',
  Slovenian: 'словенский',
  Spanish: 'испанский',
  Swedish: 'шведский',
  Thai: 'тайский',
  Turkish: 'турецкий',
  Ukrainian: 'украинский',
  Uzbek: 'узбекский',
  Vietnamese: 'вьетнамский',
};

export function languageTitle(name: string): string {
  return LANGUAGE_TITLES[name] ?? `другой язык (${name})`;
}
