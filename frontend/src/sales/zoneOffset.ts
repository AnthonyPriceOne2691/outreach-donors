/**
 * Часовой пояс словами для человека — смещением от UTC: «UTC+5», «UTC−3:30».
 *
 * Имя из базы поясов («Europe/Berlin») — код: человеку оно не говорит, сколько сейчас у получателя,
 * а стояло на виду — в строке каждого лида и в причине дня «Рассылки» (проверка прода 10.11.2026;
 * правило владельца 10.11: на виду — словами для человека, технические коды — в «i» или в
 * подсказке). На виду — смещение, имя пояса — в подсказке у значения.
 *
 * **Считает браузер, а не своя таблица** (`Intl.DateTimeFormat` с `timeZone`): смещение — на момент
 * показа, с летним временем пояса, базой поясов самого браузера. Имени в ней нет — `null`: экран
 * показывает имя как есть, а не выдумывает смещение.
 */

const MINUTE = 60_000;

/** Часы пояса — разбор момента по его времени; `null` — браузер такого пояса не знает. Один разбор
 *  на имя: в таблице лидов пояс называется в каждой строке. */
const clocks = new Map<string, Intl.DateTimeFormat | null>();

function clockOf(zone: string): Intl.DateTimeFormat | null {
  const known = clocks.get(zone);
  if (known !== undefined) return known;
  let made: Intl.DateTimeFormat | null = null;
  try {
    made = new Intl.DateTimeFormat('en-US', {
      timeZone: zone,
      hourCycle: 'h23',
      year: 'numeric',
      month: 'numeric',
      day: 'numeric',
      hour: 'numeric',
      minute: 'numeric',
    });
  } catch {
    // Имени нет в базе поясов браузера — смещения не узнать, и это ответ, а не сбой.
  }
  clocks.set(zone, made);
  return made;
}

/** Показания часов пояса — год, месяц, день, час и минута разбора, — прочитанные как момент UTC.
 *  Час — по модулю суток: полночь часть браузеров пишет «24», уже с датой нового дня. */
export function wallTime(parts: readonly Intl.DateTimeFormatPart[]): number {
  const read = (type: Intl.DateTimeFormatPartTypes) =>
    Number(parts.find((part) => part.type === type)?.value);
  return Date.UTC(read('year'), read('month') - 1, read('day'), read('hour') % 24, read('minute'));
}

/** Минут от UTC на часах пояса в момент `at`: их показания минус сам момент. Момент — до минуты:
 *  секунды часы не показывают, и с ними разность теряла бы минуту. */
function minutesOff(clock: Intl.DateTimeFormat, at: Date): number {
  const wall = wallTime(clock.formatToParts(at));
  return (wall - Math.floor(at.getTime() / MINUTE) * MINUTE) / MINUTE;
}

/** Смещение словами: 300 → «UTC+5», −210 → «UTC−3:30», 0 → «UTC+0». Минус — знаком минуса, не
 *  дефисом. */
export function offsetLabel(minutes: number): string {
  const whole = Math.abs(Math.round(minutes));
  const rest = whole % 60;
  const tail = rest === 0 ? '' : `:${String(rest).padStart(2, '0')}`;
  return `UTC${minutes < 0 ? '−' : '+'}${Math.floor(whole / 60)}${tail}`;
}

/** Смещение пояса `zone` от UTC на момент `at` (без него — сейчас): «UTC+5». Браузер пояса не
 *  знает или момент — не дата — `null`. */
export function zoneOffset(zone: string, at: Date = new Date()): string | null {
  const clock = clockOf(zone);
  if (clock === null) return null;
  try {
    const minutes = minutesOff(clock, at);
    return Number.isFinite(minutes) ? offsetLabel(minutes) : null;
  } catch {
    // Момент — не дата: часам нечего показать.
    return null;
  }
}

/** Слова сервера с именем пояса — те же слова со смещением на его месте: «(пояс Asia/Tashkent)» →
 *  «(пояс UTC+5)». Экран слов не сочиняет: меняет в них только имя, которое сервер назвал сам
 *  (`zone` ответа). Пояса нет или браузер его не знает — слова как есть. */
export function offsetWords(words: string, zone: string | null, at: Date = new Date()): string {
  if (zone === null || zone === '') return words;
  const offset = zoneOffset(zone, at);
  return offset === null ? words : words.replaceAll(zone, offset);
}
