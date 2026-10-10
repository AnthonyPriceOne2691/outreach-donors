/**
 * Имя страны по-русски из данных браузера (CLDR) — для кодов вне таблицы рынков
 * (`labels.ts`, `countryTitle`).
 *
 * Проверка прода 10.10.2026: страна вне 55 рынков шла кодом — «NP · 84%». Сервер называет
 * такие страны тем же CLDR (`backend/features/donors/regions_ru.py`): выгрузка и причина
 * отсева говорят теми же словами, что экран.
 */

/** Нет `Intl.DisplayNames` — нет и имён: страна останется кодом, а экран не упадёт. */
const NAMES = (() => {
  try {
    return new Intl.DisplayNames(['ru'], { type: 'region', fallback: 'none' });
  } catch {
    return null;
  }
})();

/** Имя страны по коду ISO; `undefined` — у браузера его нет или это не код страны
 *  («USA», «1» — `RangeError`). */
export function regionName(code: string): string | undefined {
  try {
    return NAMES?.of(code.toUpperCase());
  } catch {
    return undefined;
  }
}
