/**
 * Номер записи из адреса экрана.
 *
 * Адрес — чужой ввод: его правят руками и присылают с опечаткой. До
 * 25.09.2026 `/donors/abc` и `/runs/abc/review` уходили на сервер запросом
 * с `NaN`, и экран показывал английский отказ разбора («Input should be
 * a valid integer…»). Негодный номер проверяется здесь, до запроса,
 * и экран говорит словами, что такой записи нет.
 */

/** Самый большой номер записи: столбцы номеров в базе — четырёхбайтные. */
export const MAX_ROW_ID = 2_147_483_647;

/** Номер из адреса или `null`, если это не номер записи. */
export function rowIdOf(raw: string | undefined): number | null {
  if (raw === undefined || !/^\d{1,10}$/.test(raw)) return null;
  const id = Number(raw);
  return id >= 1 && id <= MAX_ROW_ID ? id : null;
}
