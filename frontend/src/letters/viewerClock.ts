/**
 * Время из слов сервера — по часам смотрящего.
 *
 * Строка последнего прохода автоотправки читалась так: «12.11.2026, 14:17: ушло 2. ящики на паузе
 * между письмами — ближайший освободится в 11:18 UTC» (находка 12.11.2026, экран «Письма» и панель
 * рекламодателей). Момент прохода — по часам браузера (`formatDateTime`), а время в словах сервера
 * — по UTC (`letters/autosend.py`, `_nobody`): одна строка в двух поясах, и ящик «освобождался»
 * раньше, чем прошёл проход.
 *
 * Слов экран не сочиняет: в словах сервера он ставит то же время по своим часам — тем же способом,
 * каким показывает момент прохода (`format.ts`: русская запись, часы и минуты по две цифры), и без
 * «UTC». Текст сервера остаётся в UTC: его читают и другие экраны. Прецедент подмены в словах
 * сервера — имя пояса смещением (`sales/zoneOffset.ts`, `offsetWords`).
 *
 * **День — от момента, о котором слова** (`after`, у прохода — `passed_at`). В словах только часы
 * и минуты, а смещение пояса зависит от дня (летнее время). То, о чём слова, случится после этого
 * момента: время не раньше него — тот же день UTC, раньше — следующий («00:05» в словах прохода в
 * 23:58 — уже завтра).
 *
 * **Время с датой — вместе с датой**: «до 13.11 22:30 UTC» (выдержка домена, `outreach/limits.py`)
 * на востоке — уже 14.11, и одни часы без даты соврали бы днём. Год у даты без года — ближайший к
 * моменту `after`. Дата в незнакомом виде или не время («25:00 UTC», «12:30:45 UTC») — слова как
 * есть: лучше время сервера с пометкой «UTC», чем выдуманное.
 */

const LOCALE = 'ru-RU';
const MINUTE = 60_000;
const DAY = 24 * 60 * MINUTE;

/** «ЧЧ:ММ UTC»; перед ним, может быть, дата — «ДД.ММ» или «ДД.ММ.ГГГГ» — через пробел или «, ».
 *  После «UTC» — не буква, не цифра и не знак: «UTC+5» — смещение пояса, а не время по UTC. Без
 *  ретроспективной проверки (`(?<!…)`): её не знает Safari до 16.4 — что стоит перед найденным,
 *  смотрит код (`momentOf`). */
const UTC_TIME =
  /(?:(?<day>\d{2})\.(?<month>\d{2})(?:\.(?<year>\d{4}))?(?<gap>,? ))?(?<hour>[01]\d|2[0-3]):(?<minute>[0-5]\d) UTC(?![\w+\-−])/g;

/** Момент UTC с этими часами и минутами не раньше `after` — с точностью до минуты: секунд в словах
 *  нет, и «10:42» в словах прохода в 10:42:10 — та же минута, а не завтра. */
function timeAfter(after: number, hour: number, minute: number): number {
  const day = new Date(after);
  const same = Date.UTC(day.getUTCFullYear(), day.getUTCMonth(), day.getUTCDate(), hour, minute);
  return same >= Math.floor(after / MINUTE) * MINUTE ? same : same + DAY;
}

/** Момент UTC с этой датой; такого дня в году нет («31.02») — `null`. */
function dateIn(
  year: number,
  month: number,
  day: number,
  hour: number,
  minute: number,
): number | null {
  const moment = Date.UTC(year, month - 1, day, hour, minute);
  const found = new Date(moment);
  return found.getUTCMonth() === month - 1 && found.getUTCDate() === day ? moment : null;
}

/** Дата без года — в году, где она ближе всего к `after`: «до 01.01» в словах 31 декабря — это
 *  завтра, а не январь прошедшего года. */
function dateNear(
  after: number,
  month: number,
  day: number,
  hour: number,
  minute: number,
): number | null {
  const year = new Date(after).getUTCFullYear();
  const found = [year - 1, year, year + 1]
    .map((candidate) => dateIn(candidate, month, day, hour, minute))
    .filter((moment): moment is number => moment !== null)
    .sort((one, other) => Math.abs(one - after) - Math.abs(other - after));
  return found[0] ?? null;
}

type Found = Record<string, string | undefined>;

/** Момент UTC, который назвали слова; `null` — не время или дата в незнакомом виде. `before` —
 *  слова до найденного. */
function momentOf(found: Found, before: string, after: number): number | null {
  // Хвост числа или времени с секундами («111:18», «12:30:45»), а не часы и минуты.
  if (/[\d.:]$/.test(before)) return null;
  const hour = Number(found.hour);
  const minute = Number(found.minute);
  if (found.day === undefined) {
    // Перед временем через пробел — число: похоже на дату в незнакомом виде («3.11 10:00 UTC»).
    return /\d,? $/.test(before) ? null : timeAfter(after, hour, minute);
  }
  const day = Number(found.day);
  const month = Number(found.month);
  return found.year === undefined
    ? dateNear(after, month, day, hour, minute)
    : dateIn(Number(found.year), month, day, hour, minute);
}

/** Запись момента теми же частями, что были в словах: часы и минуты, перед ними — дата, с годом
 *  или без. Пояс — браузера; названный (`zone`) — тестам. */
function shown(moment: number, found: Found, zone: string | undefined): string {
  const at = new Date(moment);
  const where = zone === undefined ? {} : { timeZone: zone };
  const time = at.toLocaleTimeString(LOCALE, { hour: '2-digit', minute: '2-digit', ...where });
  if (found.day === undefined) return time;
  const year = found.year === undefined ? {} : ({ year: 'numeric' } as const);
  const date = at.toLocaleDateString(LOCALE, {
    day: '2-digit',
    month: '2-digit',
    ...year,
    ...where,
  });
  return `${date}${found.gap ?? ' '}${time}`;
}

/**
 * Слова сервера с временем по UTC — те же слова с этим временем по часам смотрящего, без «UTC»:
 * «…ближайший освободится в 11:18 UTC» → «…ближайший освободится в 14:18».
 *
 * `after` — момент, о котором слова (время прохода): день для часов без даты и год для даты без
 * года. Его нет или он не разбирается — слова как есть; времени по UTC в словах нет — тоже.
 * `zone` — пояс часов вместо пояса браузера: для тестов.
 */
export function viewerClockWords(
  words: string,
  after: string | null | undefined,
  zone?: string,
): string {
  const anchor = typeof after === 'string' && after !== '' ? new Date(after).getTime() : Number.NaN;
  if (Number.isNaN(anchor)) return words;
  let said = '';
  let rest = 0;
  for (const match of words.matchAll(UTC_TIME)) {
    const found = match.groups ?? {};
    const moment = momentOf(found, words.slice(0, match.index), anchor);
    said += words.slice(rest, match.index);
    said += moment === null ? match[0] : shown(moment, found, zone);
    rest = match.index + match[0].length;
  }
  return said + words.slice(rest);
}
